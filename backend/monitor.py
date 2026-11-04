import asyncio
import json
import logging
from datetime import datetime, timezone

from .config import POLL_INTERVAL_SECONDS, STREAM_CHECK_TIMEOUT_SECONDS
from .db import get_db

log = logging.getLogger("monitor")


class Monitor:
    """Detect live streams through Streamlink's Twitch plugin; no Twitch API credentials required."""

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

    async def _is_live(self, login: str) -> bool:
        url = f"https://www.twitch.tv/{login}"
        process = await asyncio.create_subprocess_exec(
            "streamlink", "--json", "--loglevel", "error", url,
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

        if process.returncode == 0:
            try:
                payload = json.loads(stdout.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("Streamlink вернул некорректный JSON") from exc
            streams = payload.get("streams", {})
            return bool(streams)

        # Streamlink returns a non-zero exit code when the channel is offline or
        # no playable streams are available. This is treated as an offline state.
        message = stderr.decode("utf-8", errors="replace").strip()
        log.debug("Streamlink check for %s returned %s: %s", login, process.returncode, message[-500:])
        return False

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
                live = await self._is_live(login)
                error = None
            except Exception as exc:
                live = None
                error = str(exc)[:1000]
                log.warning("Could not check channel %s: %s", login, error)

            with get_db() as db:
                if live is None:
                    db.execute(
                        "UPDATE channels SET last_error=?,last_checked_at=? WHERE id=?",
                        (error, checked_at, channel["id"]),
                    )
                    continue
                if live:
                    db.execute(
                        """UPDATE channels SET is_live=1,last_checked_at=?,last_error=NULL
                        WHERE id=?""",
                        (checked_at, channel["id"]),
                    )
                else:
                    db.execute(
                        """UPDATE channels SET is_live=0,stream_title=NULL,game_name=NULL,
                        viewer_count=NULL,started_at=NULL,last_checked_at=?,last_error=NULL
                        WHERE id=?""",
                        (checked_at, channel["id"]),
                    )

            if live:
                # Stream metadata is not available without Helix; Streamlink resolves
                # the actual stream when the recorder process starts.
                await self.recorder.start(channel, {})

    async def _loop(self):
        while self.running:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Monitoring cycle failed")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
