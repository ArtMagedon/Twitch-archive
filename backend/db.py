import sqlite3
from contextlib import contextmanager
from .config import DATA_DIR, DB_PATH, STREAM_QUALITY

DEFAULT_SETTINGS = {
    "stream_quality": STREAM_QUALITY,
    "storage_subdirectory": "",
    "filename_template": "{date}_{time}_{channel}_{id}",
}

@contextmanager
def get_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_db() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS channels (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          login TEXT NOT NULL UNIQUE COLLATE NOCASE,
          display_name TEXT,
          enabled INTEGER NOT NULL DEFAULT 1,
          is_live INTEGER NOT NULL DEFAULT 0,
          stream_title TEXT,
          game_name TEXT,
          viewer_count INTEGER,
          started_at TEXT,
          last_checked_at TEXT,
          last_error TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS recordings (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
          stream_id TEXT,
          title TEXT,
          game_name TEXT,
          started_at TEXT NOT NULL,
          ended_at TEXT,
          status TEXT NOT NULL,
          file_path TEXT NOT NULL,
          file_size INTEGER NOT NULL DEFAULT 0,
          error TEXT
        );
        CREATE TABLE IF NOT EXISTS app_settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_recordings_started ON recordings(started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_recordings_channel ON recordings(channel_id);
        """)
        for key, value in DEFAULT_SETTINGS.items():
            db.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES (?,?)", (key, value))

def get_settings():
    with get_db() as db:
        values = {row["key"]: row["value"] for row in db.execute("SELECT key,value FROM app_settings")}
    return {**DEFAULT_SETTINGS, **values}

def save_settings(values: dict):
    with get_db() as db:
        for key, value in values.items():
            db.execute(
                "INSERT INTO app_settings(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

def reconcile_interrupted_recordings(recordings_dir):
    """Close database entries left active by an unclean container shutdown."""
    from datetime import datetime, timezone
    from pathlib import Path
    base = Path(recordings_dir).resolve()
    with get_db() as db:
        rows = db.execute("SELECT id,file_path FROM recordings WHERE status IN ('starting','recording')").fetchall()
        for row in rows:
            path = Path(row["file_path"]).resolve()
            size = path.stat().st_size if base in path.parents and path.is_file() else 0
            status = "interrupted" if size > 0 else "error"
            error = "Контейнер был остановлен до штатного завершения записи"
            db.execute("UPDATE recordings SET status=?,ended_at=?,file_size=?,error=? WHERE id=?",
                       (status, datetime.now(timezone.utc).isoformat(), size, error, row["id"]))
