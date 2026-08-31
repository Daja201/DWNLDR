import os
import re
import glob
import shlex
import signal
import subprocess
import threading
import mimetypes
import urllib.parse

import requests

import models
import logger
import ram_cache

ACTIVE_PROCESSES = {}
ACTIVE_LOCK = threading.Lock()

# [download]  12.3% of 45.67MiB at 1.23MiB/s ETA 00:34
YT_DLP_PCT_RE = re.compile(r"\[download\]\s+(\d{1,3}\.\d)%")
# [download] Downloading video 3 of 25
YT_DLP_PLAYLIST_IDX_RE = re.compile(r"Downloading (?:item|video) (\d+) of (\d+)", re.IGNORECASE)
# spotdl: "Downloading 3/25: Artist - Song"  (also emits its own [download] % lines)
SPOTDL_IDX_RE = re.compile(r"Downloading\s+(\d+)/(\d+)", re.IGNORECASE)

IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|gif|webp|bmp|tiff?)(\?.*)?$", re.IGNORECASE)


def _register_process(job_id, proc):
    with ACTIVE_LOCK:
        ACTIVE_PROCESSES[job_id] = proc


def _unregister_process(job_id):
    with ACTIVE_LOCK:
        ACTIVE_PROCESSES.pop(job_id, None)


def stop_job(job_id):
    with ACTIVE_LOCK:
        proc = ACTIVE_PROCESSES.get(job_id)
    if proc and proc.poll() is None:
        logger.info(f"Stop requested for job {job_id}, terminating process pid={proc.pid}", job_id)
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception as e:
            logger.warning(f"SIGTERM failed ({e}), trying proc.terminate()", job_id)
            try:
                proc.terminate()
            except Exception as e2:
                logger.error(f"Could not terminate process for job {job_id}: {e2}", job_id)
        models.update_job(job_id, status="stopped")
        return True
    else:
        models.update_job(job_id, status="stopped")
        logger.info(f"Job {job_id} was not running; marked as stopped.", job_id)
        return True


def _run_and_stream(cmd, job_id, progress_cb, cwd=None):
    logger.debug(f"Running command: {' '.join(shlex.quote(c) for c in cmd)}", job_id)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=cwd,
        preexec_fn=os.setsid,
    )
    _register_process(job_id, proc)

    lines = []
    try:
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n")
            if not line:
                continue
            lines.append(line)
            logger.debug(line, job_id)
            progress_cb(line)
        proc.wait()
    finally:
        _unregister_process(job_id)

    return proc.returncode, lines


def _find_new_files(output_dir, before_files):
    after_files = set(glob.glob(os.path.join(output_dir, "**", "*"), recursive=True))
    new_files = [
        f for f in (after_files - before_files)
        if os.path.isfile(f) and not f.endswith((".part", ".ytdl", ".tmp", ".ffmpeg"))
    ]
    new_files.sort(key=lambda f: os.path.getmtime(f))
    return new_files


def _finish_files(job_id, url, source, mode, output_dir, before, error_lines=None):
    """Common tail: diff the folder, record history per new file, cache in RAM."""
    new_files = _find_new_files(output_dir, before)

    if not new_files:
        error_tail = "\n".join((error_lines or [])[-20:]) or "No file was produced."
        logger.error(f"No output file found for job {job_id}:\n{error_tail}", job_id)
        models.update_job(job_id, status="error", error=error_tail[-2000:])
        models.add_history(job_id, url, source, mode, None, None, "error", error_tail[-2000:])
        return

    for f in new_files:
        title = os.path.basename(f)
        size = os.path.getsize(f) if os.path.exists(f) else None
        hist_id = models.add_history(job_id, url, source, mode, title, f, "completed", file_size=size)
        ram_cache.put(hist_id, f)

    last_title = os.path.basename(new_files[-1])
    models.update_job(job_id, status="completed", progress=100, title=last_title,
                       progress_label=f"{len(new_files)} file(s)")
    logger.info(f"Job {job_id} completed: {len(new_files)} file(s)", job_id)


def download_youtube(job):
    job_id = job["id"]
    url = job["url"]
    mode = job["mode"]  # audio | video
    output_dir = job["output_dir"]
    is_playlist = bool(job.get("is_playlist"))
    os.makedirs(output_dir, exist_ok=True)

    before = set(glob.glob(os.path.join(output_dir, "**", "*"), recursive=True))

    outtmpl_name = "%(playlist_index)s - %(title)s [%(id)s].%(ext)s" if is_playlist else "%(title)s [%(id)s].%(ext)s"
    outtmpl = os.path.join(output_dir, outtmpl_name)

    cmd = [
        "yt-dlp",
        "--newline",
        "--no-warnings",
        "--ignore-config",
        "--yes-playlist" if is_playlist else "--no-playlist",
        "-o", outtmpl,
    ]

    cookies_path = "/app/data/cookies.txt"
    if os.path.exists(cookies_path):
        cmd += ["--cookies", cookies_path]

    if mode == "audio":
        cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
    else:
        cmd += [
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
        ]

    cmd.append(url)

    models.update_job(job_id, status="downloading", progress=0)
    logger.info(f"Starting YouTube {mode}{' playlist' if is_playlist else ''} download for {url}", job_id)

    state = {"item": 1, "total": 1}

    def progress_cb(line):
        idx = YT_DLP_PLAYLIST_IDX_RE.search(line)
        if idx:
            state["item"], state["total"] = int(idx.group(1)), int(idx.group(2))
            models.update_job(job_id, progress_label=f"{state['item']}/{state['total']}")
        pct_match = YT_DLP_PCT_RE.search(line)
        if pct_match:
            pct = float(pct_match.group(1))
            overall = ((state["item"] - 1) + pct / 100.0) / max(state["total"], 1) * 100.0
            models.update_job(job_id, progress=round(overall, 1), status="downloading")

    returncode, lines = _run_and_stream(cmd, job_id, progress_cb)

    job = models.get_job(job_id)
    if job["status"] == "stopped":
        logger.warning(f"YouTube job {job_id} was stopped by user.", job_id)
        # still record whatever finished before the stop
        new_files = _find_new_files(output_dir, before)
        for f in new_files:
            models.add_history(job_id, url, "youtube", mode, os.path.basename(f), f, "stopped")
        if not new_files:
            models.add_history(job_id, url, "youtube", mode, job.get("title"), None, "stopped")
        return

    if returncode != 0:
        # yt-dlp can partially succeed on playlists even with a non-zero exit
        new_files = _find_new_files(output_dir, before)
        if not new_files:
            error_tail = "\n".join(lines[-15:])
            logger.error(f"yt-dlp failed for job {job_id} (exit {returncode}):\n{error_tail}", job_id)
            models.update_job(job_id, status="error", error=error_tail[-2000:])
            models.add_history(job_id, url, "youtube", mode, job.get("title"), None, "error", error_tail[-2000:])
            return

    _finish_files(job_id, url, "youtube", mode, output_dir, before, error_lines=lines)


def download_spotify(job):
    """spotdl handles tracks, playlists and albums identically - it matches
    each track against YouTube/YouTube Music and tags the result with Spotify
    metadata. Always audio."""
    job_id = job["id"]
    url = job["url"]
    output_dir = job["output_dir"]
    os.makedirs(output_dir, exist_ok=True)

    before = set(glob.glob(os.path.join(output_dir, "**", "*"), recursive=True))

    cmd = [
        "spotdl",
        "download", url,
        "--output", os.path.join(output_dir, "{artists} - {title}.{output-ext}"),
        "--format", "mp3",
        "--threads", "2",
        "--print-errors",
    ]

    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if client_id and client_secret:
        cmd += ["--client-id", client_id, "--client-secret", client_secret]

    models.update_job(job_id, status="downloading", progress=0)
    logger.info(f"Starting Spotify download for {url}", job_id)

    state = {"item": 1, "total": 1}

    def progress_cb(line):
        idx = SPOTDL_IDX_RE.search(line)
        if idx:
            state["item"], state["total"] = int(idx.group(1)), int(idx.group(2))
            overall = (state["item"] - 1) / max(state["total"], 1) * 100.0
            models.update_job(job_id, progress=round(overall, 1),
                               progress_label=f"{state['item']}/{state['total']}", status="downloading")
        pct_match = YT_DLP_PCT_RE.search(line)
        if pct_match:
            pct = float(pct_match.group(1))
            overall = ((state["item"] - 1) + pct / 100.0) / max(state["total"], 1) * 100.0
            models.update_job(job_id, progress=round(overall, 1), status="downloading")

    returncode, lines = _run_and_stream(cmd, job_id, progress_cb)

    job = models.get_job(job_id)
    if job["status"] == "stopped":
        logger.warning(f"Spotify job {job_id} was stopped by user.", job_id)
        new_files = _find_new_files(output_dir, before)
        for f in new_files:
            models.add_history(job_id, url, "spotify", "audio", os.path.basename(f), f, "stopped")
        if not new_files:
            models.add_history(job_id, url, "spotify", "audio", job.get("title"), None, "stopped")
        return

    _finish_files(job_id, url, "spotify", "audio", output_dir, before, error_lines=lines)


def download_image(job):
    job_id = job["id"]
    url = job["url"]
    output_dir = job["output_dir"]
    os.makedirs(output_dir, exist_ok=True)

    before = set(glob.glob(os.path.join(output_dir, "**", "*"), recursive=True))

    models.update_job(job_id, status="downloading", progress=0)
    logger.info(f"Starting image download for {url}", job_id)

    try:
        parsed = urllib.parse.urlparse(url)
        name = os.path.basename(parsed.path) or "image"
        resp = requests.get(url, stream=True, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()

        if not IMAGE_EXT_RE.search(name):
            ctype = resp.headers.get("Content-Type", "").split(";")[0].strip()
            ext = mimetypes.guess_extension(ctype) or ".jpg"
            name = name + ext if "." not in name else name

        dest = os.path.join(output_dir, name)
        base, ext = os.path.splitext(dest)
        counter = 1
        while os.path.exists(dest):
            dest = f"{base}_{counter}{ext}"
            counter += 1

        total = int(resp.headers.get("Content-Length", 0)) or None
        written = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                job_now = models.get_job(job_id)
                if job_now and job_now["status"] == "stopped":
                    logger.warning(f"Image job {job_id} stopped by user.", job_id)
                    resp.close()
                    models.add_history(job_id, url, "image", "image", None, None, "stopped")
                    return
                f.write(chunk)
                written += len(chunk)
                if total:
                    models.update_job(job_id, progress=round(written / total * 100, 1), status="downloading")

        models.update_job(job_id, progress=100)
    except Exception as e:
        logger.error(f"Image download failed for job {job_id}: {e}", job_id)
        models.update_job(job_id, status="error", error=str(e))
        models.add_history(job_id, url, "image", "image", None, None, "error", str(e))
        return

    _finish_files(job_id, url, "image", "image", output_dir, before)


def process_job(job):
    job_id = job["id"]
    try:
        if job["source"] == "youtube":
            download_youtube(job)
        elif job["source"] == "spotify":
            download_spotify(job)
        elif job["source"] == "image":
            download_image(job)
        else:
            logger.error(f"Unknown source '{job['source']}' for job {job_id}", job_id)
            models.update_job(job_id, status="error", error="Unknown source")
    except Exception as e:
        logger.error(f"Unhandled exception processing job {job_id}: {e}", job_id)
        models.update_job(job_id, status="error", error=str(e))
        models.add_history(job_id, job["url"], job["source"], job["mode"], job.get("title"), None, "error", str(e))
