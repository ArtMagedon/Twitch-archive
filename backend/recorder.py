import asyncio
import logging
import re
from datetime import datetime
from .config import RECORDINGS_DIR
from .db import get_db, get_settings

log = logging.getLogger("recorder")

def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", value or "channel").strip("._")
    return (cleaned or "channel")[:100]

class Recorder:
    def __init__(self):
        self.processes: dict[int, asyncio.subprocess.Process] = {}
        self.tasks: dict[int, asyncio.Task] = {}
        self.recording_ids: dict[int, int] = {}
        self.stopping: set[int] = set()

    def active(self, channel_id: int) -> bool:
        process = self.processes.get(channel_id)
        return process is not None and process.returncode is None

    async def start(self, channel: dict, stream: dict):
        channel_id, login = channel["id"], channel["login"]
        if self.active(channel_id):
            return
        now = datetime.now().astimezone()
        settings = get_settings()
        subdirectory = settings["storage_subdirectory"].strip().strip("/")
        base_dir = (RECORDINGS_DIR / subdirectory).resolve()
        if RECORDINGS_DIR.resolve() not in base_dir.parents and base_dir != RECORDINGS_DIR.resolve():
            raise ValueError("Некорректный подкаталог хранения")
        folder = base_dir / safe_name(login) / now.strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        template = settings["filename_template"]
        values = {
            "channel": safe_name(login),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H-%M-%S"),
            "title": safe_name(stream.get("title") or "stream"),
            "id": "pending",
        }
        with get_db() as db:
            cursor = db.execute("""INSERT INTO recordings
                (channel_id,stream_id,title,game_name,started_at,status,file_path)
                VALUES (?,?,?,?,?,'starting',?)""",
                (channel_id, stream.get("id"), stream.get("title"), stream.get("game_name"), now.isoformat(), str(folder / "pending.ts")))
            recording_id = cursor.lastrowid
            values["id"] = str(recording_id)
            path = folder / (safe_name(template.format_map(values)) + ".ts")
            db.execute("UPDATE recordings SET file_path=? WHERE id=?", (str(path), recording_id))
        quality = settings["stream_quality"]
        args = [
            "streamlink", "--force", "--webbrowser-headless", "yes",
            "--webbrowser-executable", "/usr/bin/chromium",
            "--output", str(path), f"https://www.twitch.tv/{login}", quality,
        ]
        try:
            process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE)
            self.processes[channel_id] = process
            self.recording_ids[channel_id] = recording_id
            with get_db() as db:
                db.execute("UPDATE recordings SET status='recording' WHERE id=?", (recording_id,))
            self.tasks[channel_id] = asyncio.create_task(self._watch(channel_id, recording_id, process, path))
            log.info("Recording started: %s (recording id=%s, quality=%s)", login, recording_id, quality)
        except Exception as exc:
            with get_db() as db:
                db.execute("UPDATE recordings SET status='error',ended_at=?,error=? WHERE id=?",
                    (datetime.now().astimezone().isoformat(), str(exc), recording_id))
            log.exception("Failed to start Streamlink for %s", login)

    async def _watch(self, channel_id, recording_id, process, path):
        stderr_task = asyncio.create_task(process.stderr.read()) if process.stderr else None
        return_code = await process.wait()
        stderr = await stderr_task if stderr_task else b""
        file_size = path.stat().st_size if path.exists() else 0
        manually_stopped = channel_id in self.stopping
        self.stopping.discard(channel_id)
        status = "stopped" if file_size > 0 and manually_stopped else (
            "completed" if file_size > 0 and return_code == 0 else "error")
        error = None if status in ("completed", "stopped") else (
            stderr.decode(errors="replace")[-4000:] or f"Streamlink завершился с кодом {return_code}")
        with get_db() as db:
            db.execute("UPDATE recordings SET ended_at=?,status=?,file_size=?,error=? WHERE id=?",
                (datetime.now().astimezone().isoformat(), status, file_size, error, recording_id))
        if self.processes.get(channel_id) is process:
            self.processes.pop(channel_id, None)
            self.tasks.pop(channel_id, None)
            self.recording_ids.pop(channel_id, None)
        log.info("Recording ended: id=%s status=%s bytes=%s", recording_id, status, file_size)

    async def stop(self, channel_id: int) -> bool:
        process = self.processes.get(channel_id)
        if not process or process.returncode is not None:
            return False
        self.stopping.add(channel_id)
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=15)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        task = self.tasks.get(channel_id)
        if task:
            await task
        return True

    async def stop_all(self):
        for channel_id in list(self.processes):
            await self.stop(channel_id)
