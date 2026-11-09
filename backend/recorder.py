import asyncio
import logging
import re
import shutil
import time
from collections import deque
from datetime import datetime, timezone

from .config import STREAM_QUALITY
from .db import get_db
from .storage import resolve_recordings_dir

log = logging.getLogger("recorder")


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value or "channel")[:80]


class Recorder:
    def __init__(self):
        self.processes: dict[int, asyncio.subprocess.Process] = {}
        self.tasks: dict[int, asyncio.Task] = {}
        self.recording_ids: dict[int, int] = {}
        self.stopping: set[int] = set()
        self.retry_after: dict[int, float] = {}
        self.failure_count: dict[int, int] = {}
        self.last_errors: dict[int, str] = {}

    def active(self, channel_id: int) -> bool:
        process = self.processes.get(channel_id)
        return process is not None and process.returncode is None

    def diagnostics(self):
        return [
            {
                "channel_id": channel_id,
                "pid": process.pid,
                "return_code": process.returncode,
                "recording_id": self.recording_ids.get(channel_id),
                "retry_in_seconds": max(0, round(self.retry_after.get(channel_id, 0) - time.monotonic())),
                "last_error": self.last_errors.get(channel_id),
            }
            for channel_id, process in self.processes.items()
        ]

    async def start(self, channel: dict, stream: dict):
        channel_id, login = channel["id"], channel["login"]
        if self.active(channel_id):
            return
        pending = self.tasks.get(channel_id)
        if pending is not None and not pending.done():
            return
        remaining = self.retry_after.get(channel_id, 0) - time.monotonic()
        if remaining > 0:
            log.debug(
                "Повторный запуск @%s отложен на %s сек.",
                login,
                round(remaining),
                extra={"channel_id": channel_id, "channel": login},
            )
            return

        now = datetime.now(timezone.utc)
        try:
            recordings_dir = resolve_recordings_dir()
            folder = recordings_dir / safe_name(login) / now.strftime("%Y-%m-%d")
            folder.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            message = f"Не удалось подготовить каталог для записи: {exc}"
            with get_db() as db:
                cursor = db.execute(
                    """INSERT INTO recordings
                       (channel_id,stream_id,title,game_name,started_at,ended_at,status,file_path,error)
                       VALUES (?,?,?,?,?,?,'error','',?)""",
                    (
                        channel_id,
                        stream.get("id"),
                        stream.get("title"),
                        stream.get("game_name"),
                        now.isoformat(),
                        now.isoformat(),
                        message,
                    ),
                )
            self._schedule_retry(channel_id)
            self.last_errors[channel_id] = message
            log.exception(
                "Невозможно начать запись @%s: каталог хранения недоступен (recording id=%s)",
                login,
                cursor.lastrowid,
                extra={"channel_id": channel_id, "channel": login, "recording_id": cursor.lastrowid},
            )
            return

        path = folder / f"{now.strftime('%Y%m%d_%H%M%S')}_{safe_name(login)}.ts"
        with get_db() as db:
            cursor = db.execute(
                """INSERT INTO recordings
                   (channel_id,stream_id,title,game_name,started_at,status,file_path)
                   VALUES (?,?,?,?,?,'starting',?)""",
                (
                    channel_id,
                    stream.get("id"),
                    stream.get("title"),
                    stream.get("game_name"),
                    now.isoformat(),
                    str(path),
                ),
            )
            recording_id = cursor.lastrowid

        executable = shutil.which("streamlink")
        if not executable:
            message = "Исполняемый файл Streamlink не найден в PATH контейнера."
            with get_db() as db:
                db.execute(
                    "UPDATE recordings SET status='error',ended_at=?,error=? WHERE id=?",
                    (datetime.now(timezone.utc).isoformat(), message, recording_id),
                )
            self._schedule_retry(channel_id)
            self.last_errors[channel_id] = message
            log.error(
                "%s",
                message,
                extra={"channel_id": channel_id, "channel": login, "recording_id": recording_id},
            )
            return

        args = [
            executable,
            "--force",
            "--loglevel",
            "info",
            "--output",
            str(path),
            f"https://www.twitch.tv/{login}",
            STREAM_QUALITY,
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            self.processes[channel_id] = process
            self.recording_ids[channel_id] = recording_id
            self.tasks[channel_id] = asyncio.create_task(
                self._watch(channel_id, recording_id, process, path, login),
                name=f"streamlink-{login}",
            )
            log.info(
                "Streamlink запущен: @%s pid=%s quality=%s (recording id=%s)",
                login,
                process.pid,
                STREAM_QUALITY,
                recording_id,
                extra={"channel_id": channel_id, "channel": login, "recording_id": recording_id},
            )
        except Exception:
            message = "Ошибка создания процесса Streamlink."
            with get_db() as db:
                db.execute(
                    "UPDATE recordings SET status='error',ended_at=?,error=? WHERE id=?",
                    (datetime.now(timezone.utc).isoformat(), message, recording_id),
                )
            self._schedule_retry(channel_id)
            self.last_errors[channel_id] = message
            log.exception(
                "Ошибка запуска Streamlink для @%s",
                login,
                extra={"channel_id": channel_id, "channel": login, "recording_id": recording_id},
            )

    async def _read_stderr(self, channel_id, recording_id, process, login, tail, ready):
        if not process.stderr:
            return
        while True:
            raw = await process.stderr.readline()
            if not raw:
                break
            message = raw.decode("utf-8", errors="replace").strip()
            if not message:
                continue
            tail.append(message)

            lowered = message.lower()
            if any(marker in lowered for marker in ("opening stream", "starting stream", "stream started")):
                ready.set()
                with get_db() as db:
                    db.execute(
                        "UPDATE recordings SET status='recording' WHERE id=? AND status='starting'",
                        (recording_id,),
                    )

            if any(marker in lowered for marker in ("error:", "exception", "failed", "fatal")):
                level = logging.ERROR
            elif "warning" in lowered:
                level = logging.WARNING
            else:
                level = logging.INFO
            log.log(
                level,
                "Streamlink @%s: %s",
                login,
                message,
                extra={"channel_id": channel_id, "channel": login, "recording_id": recording_id},
            )

    async def _watch(self, channel_id, recording_id, process, path, login):
        tail = deque(maxlen=120)
        ready = asyncio.Event()
        reader = asyncio.create_task(
            self._read_stderr(channel_id, recording_id, process, login, tail, ready)
        )
        startup_watchdog = asyncio.create_task(
            self._startup_watchdog(channel_id, recording_id, process, login, ready)
        )

        return_code = await process.wait()
        await reader
        startup_watchdog.cancel()
        try:
            await startup_watchdog
        except asyncio.CancelledError:
            pass

        try:
            file_size = path.stat().st_size if path.exists() else 0
        except OSError:
            file_size = 0

        manually_stopped = channel_id in self.stopping
        self.stopping.discard(channel_id)
        if manually_stopped:
            status = "stopped"
            error = None
        elif return_code == 0 and file_size > 0:
            status = "completed"
            error = None
            self.retry_after[channel_id] = time.monotonic() + 30
            self.failure_count[channel_id] = 0
        else:
            status = "error"
            details = "\n".join(tail)
            error = details[-6000:] if details else "Streamlink не передал диагностический вывод."
            error = f"{error}\nКод завершения: {return_code}; размер файла: {file_size} байт."
            self._schedule_retry(channel_id)
            self.last_errors[channel_id] = error

        with get_db() as db:
            db.execute(
                "UPDATE recordings SET ended_at=?,status=?,file_size=?,error=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), status, file_size, error, recording_id),
            )

        if self.processes.get(channel_id) is process:
            self.processes.pop(channel_id, None)
            self.tasks.pop(channel_id, None)
            self.recording_ids.pop(channel_id, None)

        if status == "error":
            delay = max(0, round(self.retry_after.get(channel_id, 0) - time.monotonic()))
            log.error(
                "Streamlink завершился с ошибкой: @%s code=%s bytes=%s; повтор через %s сек.",
                login,
                return_code,
                file_size,
                delay,
                extra={
                    "channel_id": channel_id,
                    "channel": login,
                    "recording_id": recording_id,
                    "exit_code": return_code,
                },
            )
        else:
            log.success(
                "Запись завершена: @%s id=%s status=%s bytes=%s",
                login,
                recording_id,
                status,
                file_size,
                extra={"channel_id": channel_id, "channel": login, "recording_id": recording_id},
            )

    async def _startup_watchdog(self, channel_id, recording_id, process, login, ready):
        await asyncio.sleep(15)
        if process.returncode is None and not ready.is_set():
            log.warning(
                "Streamlink для @%s работает 15 сек., но ещё не подтвердил открытие потока. "
                "Проверьте следующие строки stderr в консоли.",
                login,
                extra={"channel_id": channel_id, "channel": login, "recording_id": recording_id},
            )

    def _schedule_retry(self, channel_id):
        attempts = self.failure_count.get(channel_id, 0) + 1
        self.failure_count[channel_id] = attempts
        delay = min(30 * (2 ** min(attempts - 1, 5)), 900)
        self.retry_after[channel_id] = time.monotonic() + delay

    async def stop(self, channel_id: int) -> bool:
        process = self.processes.get(channel_id)
        if not process:
            return False
        if process.returncode is not None:
            task = self.tasks.get(channel_id)
            if task:
                await task
            return False

        self.stopping.add(channel_id)
        log.info("Останавливаю Streamlink для channel_id=%s pid=%s", channel_id, process.pid)
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=15)
        except asyncio.TimeoutError:
            log.warning("Streamlink не завершился за 15 сек.; отправляю SIGKILL channel_id=%s", channel_id)
            process.kill()
            await process.wait()
        task = self.tasks.get(channel_id)
        if task:
            await task
        return True

    async def stop_all(self):
        for channel_id in list(self.processes):
            await self.stop(channel_id)
