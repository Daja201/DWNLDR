import logging
import os
from logging.handlers import RotatingFileHandler

LOG_PATH = os.environ.get("LOG_PATH", "/app/data/app.log")

_socketio = None  # set by main.py after socketio is created


def bind_socketio(sio):
    global _socketio
    _socketio = sio


def _build_logger():
    logger = logging.getLogger("reel")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

    file_handler = RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=3)
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    stream_handler = logging.StreamHandler()  # shows up in `docker logs`
    stream_handler.setFormatter(fmt)
    stream_handler.setLevel(logging.DEBUG)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


log = _build_logger()


def emit(level, message, job_id=None):
    """Log to file/stdout AND push live to any connected browser."""
    getattr(log, level, log.info)(message)
    if _socketio is not None:
        try:
            _socketio.emit(
                "log",
                {"level": level, "message": message, "job_id": job_id},
            )
        except Exception:
            pass


def debug(msg, job_id=None):
    emit("debug", msg, job_id)


def info(msg, job_id=None):
    emit("info", msg, job_id)


def warning(msg, job_id=None):
    emit("warning", msg, job_id)


def error(msg, job_id=None):
    emit("error", msg, job_id)
