import threading
import time

import models
import downloader
import logger

_socketio = None


def bind_socketio(sio):
    global _socketio
    _socketio = sio


def _broadcast_queue_update():
    if _socketio:
        try:
            _socketio.emit("queue_update", {})
        except Exception:
            pass


def worker_loop(poll_interval=2):
    logger.info("Queue worker thread started.")
    while True:
        try:
            job = models.get_next_queued()
            if job:
                logger.info(f"Picked up job {job['id']} ({job['source']}/{job['mode']}): {job['url']}")
                _broadcast_queue_update()
                downloader.process_job(job)
                _broadcast_queue_update()
        except Exception as e:
            logger.error(f"Worker loop error: {e}")
        time.sleep(poll_interval)


def start():
    t = threading.Thread(target=worker_loop, daemon=True)
    t.start()
    return t
