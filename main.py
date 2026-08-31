import os
import re
import io
import mimetypes

import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify, send_file, abort, Response
from flask_socketio import SocketIO

import models
import logger
import worker
import downloader
import ram_cache

DOWNLOAD_BASE = os.environ.get("DOWNLOAD_BASE", "/sdc")

app = Flask(__name__)
app.config["SECRET_KEY"] = "reel-secret"
socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")

logger.bind_socketio(socketio)
worker.bind_socketio(socketio)

YOUTUBE_RE = re.compile(r"(youtube\.com|youtu\.be)", re.IGNORECASE)
SPOTIFY_RE = re.compile(r"open\.spotify\.com", re.IGNORECASE)
IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|gif|webp|bmp|tiff?)(\?.*)?$", re.IGNORECASE)

YT_PLAYLIST_RE = re.compile(r"[?&]list=", re.IGNORECASE)
SPOTIFY_PLAYLIST_RE = re.compile(r"open\.spotify\.com/(playlist|album)/", re.IGNORECASE)


def detect_source(url):
    if YOUTUBE_RE.search(url):
        return "youtube"
    if SPOTIFY_RE.search(url):
        return "spotify"
    if url.lower().startswith(("http://", "https://")) and IMAGE_EXT_RE.search(url):
        return "image"
    return None


def detect_playlist(url, source):
    if source == "youtube":
        return bool(YT_PLAYLIST_RE.search(url))
    if source == "spotify":
        return bool(SPOTIFY_PLAYLIST_RE.search(url))
    return False


def safe_output_dir(subfolder):
    """Prevent path traversal outside DOWNLOAD_BASE while allowing nested
    subfolders on a mounted drive such as /sdc."""
    subfolder = (subfolder or "").strip().strip("/")
    target = os.path.normpath(os.path.join(DOWNLOAD_BASE, subfolder))
    base = os.path.normpath(DOWNLOAD_BASE)
    if target != base and not target.startswith(base + os.sep):
        raise ValueError("Invalid output path")
    return target


@app.route("/")
def index():
    default_folder = models.get_setting("default_folder", "")
    return render_template("index.html", download_base=DOWNLOAD_BASE, default_folder=default_folder)


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "POST":
        data = request.get_json(force=True)
        folder = data.get("default_folder", "")
        models.set_setting("default_folder", folder)
        logger.info(f"Default download folder set to '{folder}'")
        return jsonify({"ok": True})
    return jsonify({"default_folder": models.get_setting("default_folder", ""), "download_base": DOWNLOAD_BASE})


@app.route("/api/browse")
def api_browse():
    """List directories (and files, for reference) under DOWNLOAD_BASE so the
    UI can offer a folder picker for /sdc without shell access."""
    subfolder = request.args.get("path", "")
    try:
        target = safe_output_dir(subfolder)
    except ValueError:
        return jsonify({"error": "Invalid path"}), 400

    if not os.path.isdir(target):
        return jsonify({"error": "Not a directory"}), 404

    entries = []
    try:
        with os.scandir(target) as it:
            for entry in sorted(it, key=lambda e: e.name.lower()):
                if entry.name.startswith("."):
                    continue
                entries.append({"name": entry.name, "is_dir": entry.is_dir()})
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403

    rel = os.path.relpath(target, os.path.normpath(DOWNLOAD_BASE))
    rel = "" if rel == "." else rel.replace(os.sep, "/")
    parent = "/".join(rel.split("/")[:-1]) if rel else None

    return jsonify({"path": rel, "parent": parent, "entries": entries})


@app.route("/api/browse/mkdir", methods=["POST"])
def api_browse_mkdir():
    data = request.get_json(force=True)
    subfolder = data.get("path", "")
    name = (data.get("name") or "").strip()
    if not name or "/" in name or name in (".", ".."):
        return jsonify({"error": "Invalid folder name"}), 400
    try:
        target = safe_output_dir(os.path.join(subfolder, name))
    except ValueError:
        return jsonify({"error": "Invalid path"}), 400
    os.makedirs(target, exist_ok=True)
    return jsonify({"ok": True})


@app.route("/api/queue", methods=["GET"])
def api_get_queue():
    return jsonify(models.list_queue())


@app.route("/api/history", methods=["GET"])
def api_get_history():
    return jsonify(models.list_history())


@app.route("/api/add", methods=["POST"])
def api_add():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    mode = data.get("mode", "audio")
    folder = data.get("folder", "")

    if not url:
        return jsonify({"error": "URL is required"}), 400

    source = detect_source(url)
    if not source:
        logger.warning(f"Rejected URL, could not detect source: {url}")
        return jsonify({"error": "URL must be a YouTube link, an open.spotify.com link, or a direct image URL"}), 400

    if source == "spotify":
        mode = "audio"
    if source == "image":
        mode = "image"

    is_playlist = detect_playlist(url, source)

    try:
        output_dir = safe_output_dir(folder)
    except ValueError:
        return jsonify({"error": "Invalid folder path"}), 400

    job_id = models.add_to_queue(url, source, mode, output_dir, is_playlist=is_playlist)
    logger.info(f"Queued job {job_id}: [{source}/{mode}{'|playlist' if is_playlist else ''}] {url} -> {output_dir}")
    socketio.emit("queue_update", {})
    return jsonify({"ok": True, "job_id": job_id, "is_playlist": is_playlist})


@app.route("/api/stop/<int:job_id>", methods=["POST"])
def api_stop(job_id):
    ok = downloader.stop_job(job_id)
    socketio.emit("queue_update", {})
    return jsonify({"ok": ok})


@app.route("/api/queue/<int:job_id>", methods=["DELETE"])
def api_delete_queue(job_id):
    downloader.stop_job(job_id)
    models.delete_from_queue(job_id)
    socketio.emit("queue_update", {})
    return jsonify({"ok": True})


@app.route("/api/play/<int:history_id>")
def api_play(history_id):
    item = models.get_history_item(history_id)
    if not item or not item["file_path"] or not os.path.exists(item["file_path"]):
        abort(404)
    mimetype = mimetypes.guess_type(item["file_path"])[0] or "application/octet-stream"
    logger.debug(f"Streaming file for history item {history_id}: {item['file_path']}")
    return send_file(item["file_path"], mimetype=mimetype, conditional=True)


@app.route("/api/fetch/<int:history_id>")
def api_fetch(history_id):
    """Serve a finished download to the browser for local saving. Prefers the
    in-RAM cached copy (fast, works even if the disk mount is slow/remote),
    falls back to the on-disk file."""
    cached = ram_cache.get(history_id)
    if cached:
        return send_file(
            io.BytesIO(cached["data"]),
            mimetype=mimetypes.guess_type(cached["filename"])[0] or "application/octet-stream",
            as_attachment=True,
            download_name=cached["filename"],
        )

    item = models.get_history_item(history_id)
    if not item or not item["file_path"] or not os.path.exists(item["file_path"]):
        abort(404)
    return send_file(item["file_path"], as_attachment=True,
                      download_name=os.path.basename(item["file_path"]))


@app.route("/api/logs/tail")
def api_logs_tail():
    log_path = os.environ.get("LOG_PATH", "/app/data/app.log")
    if not os.path.exists(log_path):
        return jsonify({"lines": []})
    with open(log_path, "r", errors="ignore") as f:
        lines = f.readlines()[-300:]
    return jsonify({"lines": lines})


@socketio.on("connect")
def on_connect():
    logger.debug("Browser client connected via websocket")


if __name__ == "__main__":
    models.init_db()
    os.makedirs(DOWNLOAD_BASE, exist_ok=True)
    ram_cache.start()
    worker.start()
    logger.info(f"App starting. DOWNLOAD_BASE={DOWNLOAD_BASE}")
    socketio.run(app, host="0.0.0.0", port=4000)
