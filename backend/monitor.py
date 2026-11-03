import asyncio
import logging
from datetime import datetime, timezone
from .config import POLL_INTERVAL_SECONDS
from .db import get_db
from .twitch import TwitchClient

log = logging.getLogger("monitor")

class Monitor:
    def __init__(self, recorder):
        self.recorder = recorder
        self.twitch = TwitchClient()
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

    async def check(self):
        with get_db() as db:
            channels = [dict(row) for row in db.execute("SELECT * FROM channels WHERE enabled=1 ORDER BY id").fetchall()]
        if not channels:
            return
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            streams = await self.twitch.live_streams([row["login"] for row in channels])
        except Exception as exc:
            with get_db() as db:
                db.execute("UPDATE channels SET last_error=?,last_checked_at=? WHERE enabled=1", (str(exc)[:1000], checked_at))
            raise
        for channel in channels:
            stream = streams.get(channel["login"].lower())
            with get_db() as db:
                if stream:
                    db.execute("""UPDATE channels SET is_live=1,display_name=?,stream_title=?,game_name=?,
                        viewer_count=?,started_at=?,last_checked_at=?,last_error=NULL WHERE id=?""",
                        (stream.get("user_name"), stream.get("title"), stream.get("game_name"),
                         stream.get("viewer_count"), stream.get("started_at"), checked_at, channel["id"]))
                else:
                    db.execute("""UPDATE channels SET is_live=0,stream_title=NULL,game_name=NULL,
                        viewer_count=NULL,started_at=NULL,last_checked_at=?,last_error=NULL WHERE id=?""",
                        (checked_at, channel["id"]))
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
