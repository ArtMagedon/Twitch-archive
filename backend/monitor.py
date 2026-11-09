import asyncio
import logging
from datetime import datetime, timezone

from .config import POLL_INTERVAL_SECONDS, STREAM_CHECK_TIMEOUT_SECONDS
from .db import get_db
from .streamlink_probe import StreamlinkProbeError, probe_twitch_channel

log = logging.getLogger("monitor")
MAX_PARALLEL_STREAM_CHECKS = 3


class Monitor:
    """Detect Twitch live streams through Streamlink; application Helix API is not used."""

    def __init__(self, recorder):
        self.recorder = recorder
        self.task = None
        self.running = False
        self.check_lock = asyncio.Lock()
        self.last_check_at = None
        self.last_success_at = None
        self.last_error = None
        self.check_count = 0
        self.consecutive_failures = 0
        self.last_result = None
        self.last_detection_method = "streamlink"

    async def start(self):
        if self.running:
            return
        self.running = True
        log.info(
            "Мониторинг запущен через Streamlink; интервал проверки %s сек.",
            POLL_INTERVAL_SECONDS,
        )
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

    async def _check_channel(self, channel, semaphore):
        async with semaphore:
            login = channel["login"]
            try:
                result = await probe_twitch_channel(
                    login,
                    timeout=STREAM_CHECK_TIMEOUT_SECONDS,
                    loglevel="error",
                )
                return channel, result, None
            except asyncio.CancelledError:
                raise
            except StreamlinkProbeError as exc:
                return channel, None, exc
            except Exception as exc:
                return channel, None, exc

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

            log.info(
                "Начинаю проверку Twitch-эфиров через Streamlink: каналов=%s, источник=%s",
                len(channels),
                trigger,
            )
            if not channels:
                self.last_success_at = started.isoformat()
                self.last_error = None
                self.consecutive_failures = 0
                self.last_result = {
                    "ok": True,
                    "checked": 0,
                    "live": 0,
                    "failed": 0,
                    "trigger": trigger,
                    "method": "streamlink",
                }
                log.info("Проверка завершена: нет включённых каналов")
                return self.last_result

            semaphore = asyncio.Semaphore(MAX_PARALLEL_STREAM_CHECKS)
            results = await asyncio.gather(
                *(self._check_channel(channel, semaphore) for channel in channels)
            )

            checked_at = datetime.now(timezone.utc).isoformat()
            live_count = 0
            failed_checks = 0
            channel_errors = {}

            for channel, result, error in results:
                channel_id = channel["id"]
                login = channel["login"]
                was_live = bool(channel["is_live"])

                if error is not None:
                    failed_checks += 1
                    detail = str(error)[:4000]
                    channel_errors[login] = detail
                    with get_db() as db:
                        db.execute(
                            "UPDATE channels SET last_error=?,last_checked_at=? WHERE id=?",
                            (detail, checked_at, channel_id),
                        )
                    log.warning(
                        "Не удалось проверить @%s через Streamlink: %s",
                        login,
                        detail,
                        extra={"channel_id": channel_id, "channel": login},
                    )
                    # A failed probe is not evidence that the channel went offline.
                    continue

                if result["live"]:
                    live_count += 1
                    metadata = result.get("metadata") or {}
                    with get_db() as db:
                        db.execute(
                            """UPDATE channels SET is_live=1,display_name=?,stream_title=?,game_name=?,
                               viewer_count=NULL,last_checked_at=?,last_error=NULL WHERE id=?""",
                            (
                                metadata.get("user_name") or login,
                                metadata.get("title"),
                                metadata.get("game_name"),
                                checked_at,
                                channel_id,
                            ),
                        )
                    if not was_live:
                        log.success(
                            "Канал @%s вышел в эфир: %s",
                            login,
                            metadata.get("title") or "(без названия)",
                            extra={"channel_id": channel_id, "channel": login},
                        )
                    await self.recorder.start(channel, {
                        **metadata,
                        "id": metadata.get("id"),
                    })
                else:
                    with get_db() as db:
                        db.execute(
                            """UPDATE channels SET is_live=0,stream_title=NULL,game_name=NULL,
                               viewer_count=NULL,started_at=NULL,last_checked_at=?,last_error=NULL WHERE id=?""",
                            (checked_at, channel_id),
                        )
                    if was_live:
                        log.info(
                            "Канал @%s завершил эфир; останавливаю активную запись",
                            login,
                            extra={"channel_id": channel_id, "channel": login},
                        )
                    await self.recorder.stop(channel_id)

            if failed_checks:
                self.last_error = (
                    f"Не удалось проверить каналы: {failed_checks} из {len(channels)}"
                )
                self.consecutive_failures += 1
            else:
                self.last_error = None
                self.consecutive_failures = 0
                self.last_success_at = checked_at

            self.last_result = {
                "ok": failed_checks == 0,
                "checked": len(channels),
                "live": live_count,
                "failed": failed_checks,
                "trigger": trigger,
                "method": "streamlink",
                "checked_at": checked_at,
                "errors": channel_errors,
            }
            log.success(
                "Проверка завершена: каналов=%s, в эфире=%s, записывается=%s, ошибок=%s",
                len(channels),
                live_count,
                len(self.recorder.processes),
                failed_checks,
            )
            return self.last_result

    def diagnostics(self):
        return {
            "running": self.running,
            "interval_seconds": POLL_INTERVAL_SECONDS,
            "detection_method": self.last_detection_method,
            "probe_timeout_seconds": STREAM_CHECK_TIMEOUT_SECONDS,
            "max_parallel_checks": MAX_PARALLEL_STREAM_CHECKS,
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
