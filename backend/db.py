import sqlite3
from contextlib import contextmanager

from .config import DATA_DIR, DB_PATH


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
        CREATE INDEX IF NOT EXISTS idx_recordings_started ON recordings(started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_recordings_channel ON recordings(channel_id);
        CREATE TABLE IF NOT EXISTS app_settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """)


def get_setting(key: str, default: str | None = None) -> str | None:
    with get_db() as db:
        row = db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str | None):
    with get_db() as db:
        if value is None or value == "":
            db.execute("DELETE FROM app_settings WHERE key=?", (key,))
        else:
            db.execute(
                "INSERT INTO app_settings(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )


def reconcile_interrupted_recordings(recordings_dir=None):
    """Close recording rows left active by an unclean container shutdown."""
    from datetime import datetime, timezone
    from pathlib import Path

    with get_db() as db:
        rows = db.execute(
            "SELECT id,file_path FROM recordings WHERE status IN ('starting','recording')"
        ).fetchall()
        for row in rows:
            path = Path(row["file_path"]) if row["file_path"] else None
            try:
                size = path.stat().st_size if path and path.is_file() else 0
            except OSError:
                size = 0
            status = "interrupted" if size > 0 else "error"
            error = "Контейнер был остановлен до штатного завершения записи"
            db.execute(
                "UPDATE recordings SET status=?,ended_at=?,file_size=?,error=? WHERE id=?",
                (status, datetime.now(timezone.utc).isoformat(), size, error, row["id"]),
            )
