import os
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
RECORDINGS_DIR = Path(os.getenv("RECORDINGS_DIR", "./recordings"))
DB_PATH = DATA_DIR / "archive.db"
LOG_DIR = DATA_DIR / "logs"
POLL_INTERVAL_SECONDS = max(15, int(os.getenv("POLL_INTERVAL_SECONDS", "45")))
STREAM_CHECK_TIMEOUT_SECONDS = max(10, int(os.getenv("STREAM_CHECK_TIMEOUT_SECONDS", "25")))
STREAM_QUALITY = os.getenv("STREAM_QUALITY", "best").strip() or "best"
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", "./frontend/dist"))
HOST_STORAGE_ROOTS = tuple(
    Path(item.strip())
    for item in os.getenv("HOST_STORAGE_ROOTS", "/host/mnt,/host/media").split(",")
    if item.strip()
)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
