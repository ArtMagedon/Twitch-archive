import asyncio
import importlib.metadata
import logging
import platform
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import (
    CLIENT_ID,
    CLIENT_SECRET,
    DATA_DIR,
    DB_PATH,
    FRONTEND_DIST,
    LOG_DIR,
    POLL_INTERVAL_SECONDS,
    RECORDINGS_DIR,
    STREAM_QUALITY,
)
from .db import get_db, init_db, reconcile_interrupted_recordings
from .monitor import Monitor
from .observability import get_log_components, get_logs, setup_logging
from .recorder import Recorder
from .storage import (
    StorageUnavailableError,
    get_storage_payload,
    is_allowed_recording_path,
    update_storage_device,
)

setup_logging()
log = logging.getLogger("api")
APP_STARTED_AT = datetime.now(timezone.utc)
app = FastAPI(title="Twitch Archive API", version="1.1.0")
recorder = Recorder()
monitor = Monitor(recorder)


class ChannelCreate(BaseModel):
    login: str = Field(min_length=1, max_length=25, pattern=r"^[A-Za-z0-9_]+$")


class ChannelUpdate(BaseModel):
    enabled: bool


class StorageUpdate(BaseModel):
    device_id: str | None = None


def serialize_channel(row):
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["is_live"] = bool(result["is_live"])
    result["recording"] = recorder.active(result["id"])
    result["recording_process"] = next(
        (item for item in recorder.diagnostics() if item["channel_id"] == result["id"]),
        None,
    )
    return result


@app.on_event("startup")
async def startup():
    init_db()
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    reconcile_interrupted_recordings(RECORDINGS_DIR)
    await monitor.start()
    log.success("Twitch Archive API запущен; Streamlink=%s", shutil.which("streamlink") or "не найден")


@app.on_event("shutdown")
async def shutdown():
    await monitor.stop()
    log.info("Сервис завершает работу")


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "monitor_running": monitor.running,
        "streamlink_available": shutil.which("streamlink") is not None,
    }


@app.get("/api/settings")
async def settings():
    return {
        "poll_interval_seconds": POLL_INTERVAL_SECONDS,
        "stream_quality": STREAM_QUALITY,
        "storage_limit": None,
        "chat_recording": False,
        "public_streams_only": True,
        "storage": get_storage_payload(),
    }


@app.get("/api/status")
async def status():
    with get_db() as db:
        return {
            "channels": db.execute("SELECT COUNT(*) n FROM channels").fetchone()["n"],
            "live": db.execute(
                "SELECT COUNT(*) n FROM channels WHERE is_live=1 AND enabled=1 AND last_error IS NULL"
            ).fetchone()["n"],
            "recording": sum(
                1 for channel_id in recorder.processes if recorder.active(channel_id)
            ),
            "recordings": db.execute("SELECT COUNT(*) n FROM recordings").fetchone()["n"],
            "monitor": monitor.diagnostics(),
        }


@app.get("/api/channels")
async def list_channels():
    with get_db() as db:
        rows = db.execute(
            """SELECT c.*,(SELECT COUNT(*) FROM recordings r WHERE r.channel_id=c.id)
               AS recordings_count FROM channels c ORDER BY c.login"""
        ).fetchall()
    return [serialize_channel(row) for row in rows]


@app.post("/api/monitor/check")
async def check_now():
    result = await monitor.check(trigger="web")
    return result


@app.post("/api/channels", status_code=201)
async def add_channel(payload: ChannelCreate):
    login = payload.login.lower()
    with get_db() as db:
        if db.execute("SELECT id FROM channels WHERE login=?", (login,)).fetchone():
            raise HTTPException(status_code=409, detail="Канал уже добавлен")
        cursor = db.execute(
            "INSERT INTO channels(login,display_name) VALUES (?,?)", (login, login)
        )
        channel_id = cursor.lastrowid

    log.success("Добавлен канал @%s (id=%s)", login, channel_id)
    await monitor.check(trigger="channel_added")
    with get_db() as db:
        row = db.execute("SELECT * FROM channels WHERE id=?", (channel_id,)).fetchone()
    return serialize_channel(row)


@app.patch("/api/channels/{channel_id}")
async def update_channel(channel_id: int, payload: ChannelUpdate):
    with get_db() as db:
        cursor = db.execute(
            "UPDATE channels SET enabled=? WHERE id=?",
            (int(payload.enabled), channel_id),
        )
        if not cursor.rowcount:
            raise HTTPException(status_code=404, detail="Канал не найден")
        if not payload.enabled:
            db.execute(
                """UPDATE channels SET is_live=0,stream_title=NULL,game_name=NULL,
                   viewer_count=NULL,started_at=NULL,last_error=NULL WHERE id=?""",
                (channel_id,),
            )

    if not payload.enabled:
        await recorder.stop(channel_id)
        log.info("Мониторинг канала id=%s отключён", channel_id)
    else:
        log.info("Мониторинг канала id=%s включён", channel_id)
        await monitor.check(trigger="channel_enabled")

    with get_db() as db:
        row = db.execute("SELECT * FROM channels WHERE id=?", (channel_id,)).fetchone()
    return serialize_channel(row)


@app.delete("/api/channels/{channel_id}")
async def delete_channel(channel_id: int):
    with get_db() as db:
        exists = db.execute("SELECT id,login FROM channels WHERE id=?", (channel_id,)).fetchone()
    if not exists:
        raise HTTPException(status_code=404, detail="Канал не найден")
    await recorder.stop(channel_id)
    with get_db() as db:
        db.execute("DELETE FROM channels WHERE id=?", (channel_id,))
    log.info("Удалён канал @%s; файлы видеозаписей оставлены на диске", exists["login"])
    return {"ok": True}


@app.get("/api/recordings")
async def list_recordings(channel_id: int | None = None, limit: int = 200):
    limit = max(1, min(limit, 500))
    query = "SELECT r.*,c.login,c.display_name FROM recordings r JOIN channels c ON c.id=r.channel_id"
    params = []
    if channel_id is not None:
        query += " WHERE r.channel_id=?"
        params.append(channel_id)
    query += " ORDER BY r.id DESC LIMIT ?"
    params.append(limit)
    with get_db() as db:
        rows = db.execute(query, params).fetchall()

    output = []
    for row in rows:
        item = dict(row)
        item["file_exists"] = bool(
            item["file_path"]
            and is_allowed_recording_path(item["file_path"])
            and Path(item["file_path"]).is_file()
        )
        item["download_url"] = f"/api/recordings/{item['id']}/download"
        output.append(item)
    return output


@app.get("/api/recordings/{recording_id}/download")
async def download_recording(recording_id: int):
    with get_db() as db:
        row = db.execute(
            "SELECT file_path FROM recordings WHERE id=?", (recording_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    path = Path(row["file_path"]) if row["file_path"] else None
    if not path or not is_allowed_recording_path(str(path)) or not path.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден или диск не подключён")
    return FileResponse(path, filename=path.name, media_type="video/mp2t")


@app.delete("/api/recordings/{recording_id}")
async def delete_recording(recording_id: int):
    with get_db() as db:
        row = db.execute(
            "SELECT file_path,status FROM recordings WHERE id=?", (recording_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Запись не найдена")
        if row["status"] in ("starting", "recording"):
            raise HTTPException(status_code=409, detail="Нельзя удалить активную запись")
        path = Path(row["file_path"]) if row["file_path"] else None
        if path and path.exists():
            if not is_allowed_recording_path(str(path)):
                raise HTTPException(status_code=403, detail="Файл находится вне разрешённых каталогов")
            if path.is_file():
                path.unlink()
        db.execute("DELETE FROM recordings WHERE id=?", (recording_id,))
    log.info("Удалена запись id=%s", recording_id)
    return {"ok": True}


@app.get("/api/logs")
async def list_logs(
    components: str | None = None,
    level: str | None = None,
    limit: int = 300,
    after: int = 0,
):
    selected = [item.strip() for item in components.split(",") if item.strip()] if components else None
    limit = max(1, min(limit, 1000))
    items, latest_id = get_logs(
        limit=limit,
        after=max(0, after),
        components=selected,
        level=level,
    )
    return {
        "items": items,
        "components": get_log_components(),
        "latest_id": latest_id,
        "limit": limit,
    }


@app.get("/api/storage")
async def storage_info():
    return get_storage_payload()


@app.post("/api/storage")
async def set_storage(payload: StorageUpdate):
    try:
        result = update_storage_device(payload.device_id)
    except StorageUnavailableError as exc:
        log.error("Не удалось изменить каталог записей: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.success("Каталог записей изменён: %s", result.get("display_path"))
    return result


@app.get("/api/diagnostics")
async def diagnostics():
    try:
        streamlink_version = importlib.metadata.version("streamlink")
    except importlib.metadata.PackageNotFoundError:
        streamlink_version = None

    try:
        with get_db() as db:
            database = {
                "accessible": True,
                "journal_mode": db.execute("PRAGMA journal_mode").fetchone()[0],
                "channels": db.execute("SELECT COUNT(*) FROM channels").fetchone()[0],
                "recordings": db.execute("SELECT COUNT(*) FROM recordings").fetchone()[0],
            }
    except Exception as exc:
        database = {"accessible": False, "error": str(exc)}

    storage = get_storage_payload()
    return {
        "application": {
            "name": "Twitch Archive",
            "version": app.version,
            "started_at": APP_STARTED_AT.isoformat(),
            "uptime_seconds": round((datetime.now(timezone.utc) - APP_STARTED_AT).total_seconds()),
        },
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "twitch": {
            "credentials_configured": bool(CLIENT_ID and CLIENT_SECRET),
            "client_id_present": bool(CLIENT_ID),
            "client_secret_present": bool(CLIENT_SECRET),
            "access_token_cached": bool(monitor.twitch.token),
            "token_expires_in_seconds": max(
                0, round(monitor.twitch.expires_at - datetime.now().timestamp())
            ) if monitor.twitch.token else 0,
        },
        "streamlink": {
            "available": shutil.which("streamlink") is not None,
            "executable": shutil.which("streamlink"),
            "version": streamlink_version,
            "quality": STREAM_QUALITY,
            "processes": recorder.diagnostics(),
        },
        "monitor": monitor.diagnostics(),
        "database": {"path": str(DB_PATH), **database},
        "logging": {"directory": str(LOG_DIR), "file": str(LOG_DIR / "application.log")},
        "storage": {
            "mode": storage["mode"],
            "selected_id": storage["selected_id"],
            "path": storage["display_path"],
            "available": storage["available"],
            "error": storage["error"],
            "devices": storage["devices"],
        },
        "recordings_directory_default": str(RECORDINGS_DIR),
    }


@app.post("/api/diagnostics/twitch")
async def test_twitch_connection():
    try:
        result = await monitor.twitch.check_connection()
        log.success("Проверка подключения к Twitch API завершилась успешно")
        return result
    except Exception as exc:
        log.exception("Проверка подключения к Twitch API завершилась ошибкой")
        return {"ok": False, "error": str(exc)}


@app.post("/api/diagnostics/streamlink")
async def test_streamlink():
    executable = shutil.which("streamlink")
    if not executable:
        log.error("Проверка Streamlink: исполняемый файл не найден в PATH")
        return {"ok": False, "error": "Streamlink не найден в PATH контейнера."}

    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await asyncio.wait_for(process.communicate(), timeout=5)
        version = output.decode("utf-8", errors="replace").strip()
        result = {"ok": process.returncode == 0, "executable": executable, "version": version}
        if result["ok"]:
            log.success("Проверка Streamlink прошла успешно: %s", version)
        else:
            log.error("Streamlink --version завершился с кодом %s: %s", process.returncode, version)
        return result
    except Exception as exc:
        log.exception("Не удалось выполнить Streamlink --version")
        return {"ok": False, "error": str(exc)}


if FRONTEND_DIST.exists():
    assets = FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}")
    async def frontend(path: str):
        candidate = (FRONTEND_DIST / path).resolve()
        root = FRONTEND_DIST.resolve()
        if root in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
