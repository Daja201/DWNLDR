import sqlite3
import os
import threading
import time

DB_PATH = os.environ.get("DB_PATH", "/app/data/app.db")
_lock = threading.Lock()


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db():
    with _lock:
        conn = get_conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                source TEXT NOT NULL,          -- youtube | spotify | image
                mode TEXT NOT NULL,            -- audio | video | image
                is_playlist INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued', -- queued|downloading|completed|error|stopped
                progress REAL NOT NULL DEFAULT 0,
                progress_label TEXT,
                title TEXT,
                output_dir TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_id INTEGER,
                url TEXT NOT NULL,
                source TEXT NOT NULL,
                mode TEXT NOT NULL,
                title TEXT,
                file_path TEXT,
                file_size INTEGER,
                status TEXT NOT NULL,          -- completed|error|stopped
                error TEXT,
                finished_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        conn.commit()
        conn.close()


def get_setting(key, default=None):
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()
        conn.close()


def add_to_queue(url, source, mode, output_dir, is_playlist=False):
    with _lock:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO queue(url, source, mode, output_dir, is_playlist) VALUES (?,?,?,?,?)",
            (url, source, mode, output_dir, 1 if is_playlist else 0),
        )
        conn.commit()
        job_id = cur.lastrowid
        conn.close()
        return job_id


def update_job(job_id, **fields):
    if not fields:
        return
    fields["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [job_id]
    with _lock:
        conn = get_conn()
        conn.execute(f"UPDATE queue SET {cols} WHERE id=?", vals)
        conn.commit()
        conn.close()


def get_job(job_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM queue WHERE id=?", (job_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_next_queued():
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM queue WHERE status='queued' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_queue():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM queue WHERE status IN ('queued','downloading') ORDER BY id ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_history(limit=200):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_history_item(history_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM history WHERE id=?", (history_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def add_history(queue_id, url, source, mode, title, file_path, status, error=None, file_size=None):
    with _lock:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO history(queue_id, url, source, mode, title, file_path, status, error, file_size) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (queue_id, url, source, mode, title, file_path, status, error, file_size),
        )
        conn.commit()
        history_id = cur.lastrowid
        conn.close()
        return history_id


def delete_from_queue(job_id):
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM queue WHERE id=?", (job_id,))
        conn.commit()
        conn.close()
