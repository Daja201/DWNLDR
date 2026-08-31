FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x entrypoint.sh

ENV DOWNLOAD_BASE=/sdc \
    DB_PATH=/app/data/app.db \
    LOG_PATH=/app/data/app.log \
    RAM_CACHE_MINUTES=30

EXPOSE 4000

ENTRYPOINT ["./entrypoint.sh"]
