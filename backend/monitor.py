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
        self.check_lock = asyncio.Lock()
        self.last_check_at = None
        self.last_success_at = None
        self.last_error = None
        self.check_count = 0
        self.consecutive_failures = 0
        self.last_result = None

    async def start(self):
        if self.running:
            return
        self.running = True
        log.info("Мониторинг запущен; интервал проверки %s сек.", POLL_INTERVAL_SECONDS)
        self.task = asyncio.create_task(self._loop(), name="twitch-monitor")

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        await self.recorder.stop_all()
        log.info("Мониторинг остановлен")

    async def check(self, trigger="manual"):
        async with self.check_lock:
            started = datetime.now(timezone.utc)
            self.last_check_at = started.isoformat()
            self.check_count += 1

            with get_db() as db:
                channels = [
                    dict(row) for row in db.execute(
                        "SELECT * FROM channels WHERE enabled=1 ORDER BY id"
                    ).fetchall()
                ]

            log.info("Начинаю проверку эфиров Twitch: каналов=%s, источник=%s", len(channels), trigger)
            if not channels:
                self.last_success_at = started.isoformat()
                self.last_error = None
                self.consecutive_failures = 0
                self.last_result = {"ok": True, "checked": 0, "live": 0, "trigger": trigger}
                log.info("Проверка завершена: нет включённых каналов")
                return self.last_result

            checked_at = datetime.now(timezone.utc).isoformat()
            try:
                streams = await self.twitch.live_streams([row["login"] for row in channels])
            except Exception as exc:
                self.last_error = str(exc)
                self.consecutive_failures += 1
                with get_db() as db:
                    db.execute(
                        "UPDATE channels SET last_error=?,last_checked_at=? WHERE enabled=1",
                        (str(exc)[:4000], checked_at),
                    )
                log.exception("Не удалось получить состояние каналов через Twitch Helix API")
                self.last_result = {
                    "ok": False,
                    "checked": len(channels),
                    "live": None,
                    "trigger": trigger,
                    "error": str(exc),
                }
                return self.last_result

            live_count = 0
            for channel in channels:
                stream = streams.get(channel["login"].lower())
                was_live = bool(channel["is_live"])
                if stream:
                    live_count += 1
                    with get_db() as db:
                        db.execute(
                            """UPDATE channels SET is_live=1,display_name=?,stream_title=?,game_name=?,
                               viewer_count=?,started_at=?,last_checked_at=?,last_error=NULL WHERE id=?""",
                            (
                                stream.get("user_name"),
                                stream.get("title"),
                                stream.get("game_name"),
                                stream.get("viewer_count"),
                                stream.get("started_at"),
                                checked_at,
                                channel["id"],
                            ),
                        )
                    if not was_live:
                        log.success(
                            "Канал @%s вышел в эфир: %s",
                            channel["login"],
                            stream.get("title") or "(без названия)",
                            extra={"channel_id": channel["id"], "channel": channel["login"]},
                        )
                    await self.recorder.start(channel, stream)
                else:
                    with get_db() as db:
                        db.execute(
                            """UPDATE channels SET is_live=0,stream_title=NULL,game_name=NULL,
                               viewer_count=NULL,started_at=NULL,last_checked_at=?,last_error=NULL WHERE id=?""",
                            (checked_at, channel["id"]),
                        )
                    if was_live:
                        log.info(
                            "Канал @%s завершил эфир; останавливаю активную запись, если она ещё работает",
                            channel["login"],
                            extra={"channel_id": channel["id"], "channel": channel["login"]},
                        )
                    await self.recorder.stop(channel["id"])

            self.last_success_at = checked_at
            self.last_error = None
            self.consecutive_failures = 0
            self.last_result = {
                "ok": True,
                "checked": len(channels),
                "live": live_count,
                "trigger": trigger,
                "checked_at": checked_at,
            }
            log.success(
                "Проверка завершена: каналов=%s, в эфире=%s, записывается=%s",
                len(channels),
                live_count,
                len(self.recorder.processes),
            )
            return self.last_result

    def diagnostics(self):
        return {
            "running": self.running,
            "interval_seconds": POLL_INTERVAL_SECONDS,
            "last_check_at": self.last_check_at,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "check_count": self.check_count,
            "consecutive_failures": self.consecutive_failures,
            "last_result": self.last_result,
            "active_recordings": len([
                channel_id for channel_id in self.recorder.processes
                if self.recorder.active(channel_id)
            ]),
        }

    async def _loop(self):
        while self.running:
            try:
                await self.check(trigger="scheduled")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Ошибка фонового цикла мониторинга")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
