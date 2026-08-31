# Reel — YouTube, Spotify & image downloader (Docker Compose)

Self-hosted web app. Paste a link, it queues the download, saves it to a
folder you pick on the server (defaults to `/sdc`), and lets you play or
pull the finished file back to your own computer. Live progress and a debug
log stream over websockets. Plain black-and-white UI, no branding.

## Supports

- **YouTube** — single video or full playlist, audio (mp3) or video (mp4).
  A playlist is detected automatically from `?list=` in the URL.
- **Spotify** — track, playlist or album (via `spotdl`, always audio, since
  Spotify itself never serves raw audio).
- **Direct image URLs** — any link ending in a common image extension.
- **Any mix of the above queued together** — one job runs at a time.

## Quick start

```bash
cd reel
docker compose up -d --build
```

Open **http://YOUR_SERVER_IP:4000**

## Choosing where files land

Click the **Folder** button in the UI to browse the server's `/sdc` drive
(or wherever you mounted it — see below), create new subfolders, and select
one as the destination. It's remembered as the default.

To point at a different host drive, edit `docker-compose.yml`:

```yaml
volumes:
  - /mnt/my_nas/media:/sdc   # <- change the LEFT side only
  - ./data:/app/data
```

## Getting files onto your own computer

Every finished download shows a **Download** button in History. This pulls
the file straight to your browser. Finished files are also kept in RAM for
a while (`RAM_CACHE_MINUTES`, default 30) so the download is instant even
if `/sdc` is a slow network mount — after that window it falls back to
reading the file from disk directly.

## Debug logs

- Live in the browser: the Log panel at the bottom of the page.
- On disk: `./data/app.log` (rotates at 5MB, keeps 3 backups).
- Via Docker: `docker compose logs -f reel`

## Age-restricted / private YouTube videos

1. Export cookies from a logged-in browser session as `cookies.txt`.
2. Save it as `./data/cookies.txt`.
3. Uncomment the cookies volume line in `docker-compose.yml` and restart.

## Notes

- Spotify links always download as audio (mp3) — there's no legal way to
  fetch raw Spotify audio directly, hence the YouTube-matching approach.
- The queue processes one job at a time to avoid getting rate-limited.
- `entrypoint.sh` upgrades `yt-dlp` and `spotdl` on every container start,
  since YouTube changes frequently break old versions.
