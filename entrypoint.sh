#!/usr/bin/env bash
set -e

echo "[entrypoint] Upgrading yt-dlp..."
pip install --no-cache-dir --upgrade yt-dlp || echo "[entrypoint] WARNING: yt-dlp upgrade failed, continuing with existing version"

echo "[entrypoint] Upgrading spotdl..."
pip install --no-cache-dir --upgrade spotdl || echo "[entrypoint] WARNING: spotdl upgrade failed, continuing with existing version"

echo "[entrypoint] yt-dlp version: $(yt-dlp --version)"
echo "[entrypoint] spotdl version: $(spotdl --version || true)"
echo "[entrypoint] ffmpeg: $(ffmpeg -version | head -n1)"

mkdir -p "$(dirname "$DB_PATH")" "$DOWNLOAD_BASE"

echo "[entrypoint] Starting app on port 4000..."
exec python /app/main.py
