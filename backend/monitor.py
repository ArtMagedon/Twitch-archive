import asyncio
import json
import logging
from datetime import datetime, timezone

from .config import POLL_INTERVAL_SECONDS, STREAM_CHECK_TIMEOUT_SECONDS
from .db import get_db

log = logging.getLogger("monitor")
BROWSER_ARGS = ["--webbrowser-headless", "yes", "--webbrowser-executable", "/usr/bin/chromium"]


class Monitor:
    """Detect live streams through Streamlink's Twitch plugin; no Helix credentials required."""

    def __init__(self, recorder):
        self.recorder = recorder
        self.task = None
        self.running = False

    async def start(self):
        self.running = True
        self.task = asyncio.create_task(self._loop())

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        await self.recorder.stop_all()

    async def _get_stream(self, login: str) -> dict | None:
        url = f"https://www.twitch.tv/{login}"
        process = await asyncio.create_subprocess_exec(
            "streamlink", "--json", "--loglevel", "error", *BROWSER_ARGS, url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=STREAM_CHECK_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise RuntimeError(f"Streamlink не ответил за {STREAM_CHECK_TIMEOUT_SECONDS} секунд")

        message = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            if "no playable streams found" in message.lower() or "no streams found" in message.lower():
                return None
            raise RuntimeError(message[-1000:] or f"Streamlink завершился с кодом {process.returncode}")

        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Streamlink вернул некорректный JSON") from exc

        if not payload.get("streams"):
            return None

        metadata = payload.get("metadata") or {}
        return {
            "id": metadata.get("id"),
            "title": metadata.get("title"),
            "game_name": metadata.get("category"),
            "user_name": metadata.get("author") or login,
        }

    async def check(self):
        with get_db() as db:
            channels = [
                dict(row) for row in db.execute(
                    "SELECT * FROM channels WHERE enabled=1 ORDER BY id"
                ).fetchall()
            ]

        checked_at = datetime.now(timezone.utc).isoformat()
        for channel in channels:
            login = channel["login"]
            try:
                stream = await self._get_stream(login)
                error = None
            except Exception as exc:
                stream = None
                error = str(exc)[:1000]
                log.warning("Could not check channel %s: %s", login, error)

            with get_db() as db:
                if error:
                    db.execute(
                        "UPDATE channels SET last_error=?,last_checked_at=? WHERE id=?",
                        (error, checked_at, channel["id"]),
                    )
                    continue
                if stream:
                    db.execute(
                        """UPDATE channels SET is_live=1,display_name=?,stream_title=?,game_name=?,
                        viewer_count=NULL,started_at=NULL,last_checked_at=?,last_error=NULL WHERE id=?""",
                        (stream.get("user_name"), stream.get("title"), stream.get("game_name"),
                         checked_at, channel["id"]),
                    )
                else:
                    db.execute(
                        """UPDATE channels SET is_live=0,stream_title=NULL,game_name=NULL,
                        viewer_count=NULL,started_at=NULL,last_checked_at=?,last_error=NULL
                        WHERE id=?""",
                        (checked_at, channel["id"]),
                    )

            if stream:
                await self.recorder.start(channel, stream)

    async def _loop(self):
        while self.running:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Monitoring cycle failed")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
