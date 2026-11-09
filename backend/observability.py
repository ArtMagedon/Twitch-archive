import logging
import os
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import LOG_DIR, LOG_LEVEL

SUCCESS = 25
logging.addLevelName(SUCCESS, "SUCCESS")


def _success(self, message, *args, **kwargs):
    if self.isEnabledFor(SUCCESS):
        self._log(SUCCESS, message, args, **kwargs)


if not hasattr(logging.Logger, "success"):
    logging.Logger.success = _success


class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[37m",
        SUCCESS: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[1;35m",
    }
    RESET = "\033[0m"

    def format(self, record):
        rendered = super().format(record)
        color = self.COLORS.get(record.levelno, self.RESET)
        return f"{color}{rendered}{self.RESET}"


class LogBufferHandler(logging.Handler):
    def __init__(self, max_records=5000):
        super().__init__()
        self.records = deque(maxlen=max_records)
        self.sequence = 0
        self.lock = threading.RLock()

    def emit(self, record):
        try:
            with self.lock:
                self.sequence += 1
                item = {
                    "id": self.sequence,
                    "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                    "level": record.levelname,
                    "levelno": record.levelno,
                    "component": record.name,
                    "message": record.getMessage(),
                    "exception": self.formatter.formatException(record.exc_info) if record.exc_info and self.formatter else (
                        logging.Formatter().formatException(record.exc_info) if record.exc_info else None
                    ),
                    "file": record.pathname.rsplit("/", 1)[-1],
                    "line": record.lineno,
                    "function": record.funcName,
                    "channel_id": getattr(record, "channel_id", None),
                    "channel": getattr(record, "channel", None),
                    "recording_id": getattr(record, "recording_id", None),
                    "exit_code": getattr(record, "exit_code", None),
                }
                self.records.append(item)
        except Exception:
            self.handleError(record)

    def query(self, limit=300, after=0, components=None, level=None):
        with self.lock:
            rows = list(self.records)
            latest_id = self.sequence

        if components:
            selected = set(components)
            rows = [
                row for row in rows
                if any(row["component"] == name or row["component"].startswith(name + ".") for name in selected)
            ]
        if level:
            threshold = logging.getLevelName(level.upper())
            if isinstance(threshold, int):
                rows = [row for row in rows if row["levelno"] >= threshold]
        if after:
            rows = [row for row in rows if row["id"] > after]
        return rows[-limit:], latest_id

    def components(self):
        with self.lock:
            names = sorted({row["component"] for row in self.records})
        return names


buffer_handler = LogBufferHandler()


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.handlers.clear()
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    root.setLevel(level)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(ColorFormatter(
        "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_DIR / "application.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(file_handler)

    buffer_handler.setLevel(logging.DEBUG)
    buffer_handler.setFormatter(logging.Formatter())
    root.addHandler(buffer_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def get_logs(limit=300, after=0, components=None, level=None):
    return buffer_handler.query(limit=limit, after=after, components=components, level=level)


def get_log_components():
    return buffer_handler.components()
