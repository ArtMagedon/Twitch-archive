import os
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
RECORDINGS_DIR = Path(os.getenv("RECORDINGS_DIR", "./recordings"))
DB_PATH = DATA_DIR / "archive.db"
POLL_INTERVAL_SECONDS = max(30, int(os.getenv("POLL_INTERVAL_SECONDS", "60")))
STREAM_QUALITY = os.getenv("STREAM_QUALITY", "best").strip() or "best"
STREAM_CHECK_TIMEOUT_SECONDS = max(10, int(os.getenv("STREAM_CHECK_TIMEOUT_SECONDS", "25")))
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", "./frontend/dist"))
