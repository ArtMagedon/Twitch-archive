import os
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
RECORDINGS_DIR = Path(os.getenv("RECORDINGS_DIR", "./recordings"))
DB_PATH = DATA_DIR / "archive.db"
CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "").strip()
POLL_INTERVAL_SECONDS = max(15, int(os.getenv("POLL_INTERVAL_SECONDS", "45")))
STREAM_QUALITY = os.getenv("STREAM_QUALITY", "best").strip() or "best"
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", "./frontend/dist"))
