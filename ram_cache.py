import os
import threading
import time

import logger

RAM_CACHE_MINUTES = float(os.environ.get("RAM_CACHE_MINUTES", "30"))
MAX_ITEM_BYTES = int(os.environ.get("RAM_CACHE_MAX_ITEM_MB", "500")) * 1024 * 1024

_store = {}  # history_id -> {"data": bytes, "filename": str, "expires": float}
_lock = threading.Lock()


def put(history_id, file_path):
    """Load a finished file into RAM so it can be served back to the browser
    for a while, even if the on-disk copy lives on a slow/remote mount."""
    try:
        size = os.path.getsize(file_path)
        if size > MAX_ITEM_BYTES:
            logger.warning(f"Skipping RAM cache for history {history_id}: file too large ({size} bytes)")
            return
        with open(file_path, "rb") as f:
            data = f.read()
        with _lock:
            _store[history_id] = {
                "data": data,
                "filename": os.path.basename(file_path),
                "expires": time.time() + RAM_CACHE_MINUTES * 60,
            }
    except Exception as e:
        logger.warning(f"Could not load file into RAM cache: {e}")


def get(history_id):
    with _lock:
        item = _store.get(history_id)
        if not item:
            return None
        if item["expires"] < time.time():
            _store.pop(history_id, None)
            return None
        return item


def _cleanup_loop():
    while True:
        now = time.time()
        with _lock:
            expired = [k for k, v in _store.items() if v["expires"] < now]
            for k in expired:
                _store.pop(k, None)
        time.sleep(60)


def start():
    t = threading.Thread(target=_cleanup_loop, daemon=True)
    t.start()
    return t
