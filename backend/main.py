import logging
import re
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from .config import FRONTEND_DIST, RECORDINGS_DIR, POLL_INTERVAL_SECONDS, STREAM_CHECK_TIMEOUT_SECONDS
from .db import get_db, get_settings, init_db, reconcile_interrupted_recordings, save_settings
from .monitor import Monitor
from .recorder import Recorder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = FastAPI(title="Twitch Archive API", version="1.1.0")
recorder = Recorder()
monitor = Monitor(recorder)

class ChannelCreate(BaseModel):
    login: str = Field(min_length=1, max_length=25, pattern=r"^[A-Za-z0-9_]+$")

class ChannelUpdate(BaseModel):
    enabled: bool

class SettingsUpdate(BaseModel):
    stream_quality: str = Field(pattern=r"^(best|worst|1080p60|1080p|720p60|720p|480p|360p)$")
    storage_subdirectory: str = Field(default="", max_length=100)
    filename_template: str = Field(min_length=1, max_length=120)

    @field_validator("storage_subdirectory")
    @classmethod
    def validate_storage_subdirectory(cls, value):
        value = value.strip().strip("/")
        if value and (value.startswith(".") or ".." in value.split("/") or not re.fullmatch(r"[A-Za-z0-9._/-]+", value)):
            raise ValueError("Подкаталог может содержать только латиницу, цифры, точку, дефис, подчёркивание и /")
        return value

    @field_validator("filename_template")
    @classmethod
    def validate_filename_template(cls, value):
        allowed = {"channel", "date", "time", "title", "id"}
        tokens = re.findall(r"{([^{}]+)}", value)
        if not tokens or any(token not in allowed for token in tokens):
            raise ValueError("Используйте шаблон с полями {channel}, {date}, {time}, {title}, {id}")
        if "{" in re.sub(r"{[^{}]+}", "", value) or "}" in re.sub(r"{[^{}]+}", "", value):
            raise ValueError("Некорректные фигурные скобки в шаблоне")
        if "/" in value or "\\" in value:
            raise ValueError("Шаблон должен задавать только имя файла, без каталогов")
        return value

def serialize_channel(row):
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["is_live"] = bool(result["is_live"])
    result["recording"] = recorder.active(result["id"])
    return result

@app.on_event("startup")
async def startup():
    init_db()
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    reconcile_interrupted_recordings(RECORDINGS_DIR)
    await monitor.start()

@app.on_event("shutdown")
async def shutdown():
    await monitor.stop()

@app.get("/api/health")
async def health():
    return {"status": "ok"}

@app.get("/api/settings")
async def settings():
    saved = get_settings()
    return {
        "poll_interval_seconds": POLL_INTERVAL_SECONDS,
        "stream_check_timeout_seconds": STREAM_CHECK_TIMEOUT_SECONDS,
        "stream_quality": saved["stream_quality"],
        "storage_directory": str(RECORDINGS_DIR / saved["storage_subdirectory"]),
        "storage_subdirectory": saved["storage_subdirectory"],
        "filename_template": saved["filename_template"],
        "available_qualities": ["best", "1080p60", "1080p", "720p60", "720p", "480p", "360p", "worst"],
        "file_format": "MPEG-TS (.ts)",
        "storage_limit": None,
        "chat_recording": False,
        "public_streams_only": True,
        "detection_method": "Streamlink (без Twitch Helix API)",
    }

@app.put("/api/settings")
async def update_settings(payload: SettingsUpdate):
    values = payload.model_dump()
    save_settings(values)
    return await settings()

@app.get("/api/status")
async def status():
    with get_db() as db:
        return {
            "channels": db.execute("SELECT COUNT(*) n FROM channels").fetchone()["n"],
            "live": db.execute("SELECT COUNT(*) n FROM channels WHERE is_live=1 AND enabled=1").fetchone()["n"],
            "recording": sum(1 for channel_id in recorder.processes if recorder.active(channel_id)),
            "recordings": db.execute("SELECT COUNT(*) n FROM recordings").fetchone()["n"],
        }

@app.get("/api/channels")
async def list_channels():
    with get_db() as db:
        rows = db.execute("""SELECT c.*,(SELECT COUNT(*) FROM recordings r WHERE r.channel_id=c.id)
            AS recordings_count FROM channels c ORDER BY c.login""").fetchall()
    return [serialize_channel(row) for row in rows]

@app.post("/api/channels", status_code=201)
async def add_channel(payload: ChannelCreate):
    login = payload.login.lower()
    with get_db() as db:
        if db.execute("SELECT id FROM channels WHERE login=?", (login,)).fetchone():
            raise HTTPException(status_code=409, detail="Канал уже добавлен")
        cursor = db.execute("INSERT INTO channels(login,display_name) VALUES (?,?)", (login, login))
        row = db.execute("SELECT * FROM channels WHERE id=?", (cursor.lastrowid,)).fetchone()
    return serialize_channel(row)

@app.patch("/api/channels/{channel_id}")
async def update_channel(channel_id: int, payload: ChannelUpdate):
    with get_db() as db:
        cursor = db.execute("UPDATE channels SET enabled=? WHERE id=?", (int(payload.enabled), channel_id))
        if not cursor.rowcount:
            raise HTTPException(status_code=404, detail="Канал не найден")
        row = db.execute("SELECT * FROM channels WHERE id=?", (channel_id,)).fetchone()
    if not payload.enabled:
        await recorder.stop(channel_id)
    return serialize_channel(row)

@app.delete("/api/channels/{channel_id}")
async def delete_channel(channel_id: int):
    with get_db() as db:
        exists = db.execute("SELECT id FROM channels WHERE id=?", (channel_id,)).fetchone()
    if not exists:
        raise HTTPException(status_code=404, detail="Канал не найден")
    await recorder.stop(channel_id)
    with get_db() as db:
        db.execute("DELETE FROM channels WHERE id=?", (channel_id,))
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
        item["file_exists"] = Path(item["file_path"]).is_file()
        item["download_url"] = f"/api/recordings/{item['id']}/download"
        output.append(item)
    return output

@app.get("/api/recordings/{recording_id}/download")
async def download_recording(recording_id: int):
    with get_db() as db:
        row = db.execute("SELECT file_path FROM recordings WHERE id=?", (recording_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    path = Path(row["file_path"]).resolve()
    base = RECORDINGS_DIR.resolve()
    if base not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(path, filename=path.name, media_type="video/mp2t")

@app.delete("/api/recordings/{recording_id}")
async def delete_recording(recording_id: int):
    with get_db() as db:
        row = db.execute("SELECT file_path,status FROM recordings WHERE id=?", (recording_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Запись не найдена")
        if row["status"] in ("starting", "recording"):
            raise HTTPException(status_code=409, detail="Нельзя удалить активную запись")
        path = Path(row["file_path"]).resolve()
        base = RECORDINGS_DIR.resolve()
        if base in path.parents and path.is_file():
            path.unlink()
        db.execute("DELETE FROM recordings WHERE id=?", (recording_id,))
    return {"ok": True}

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
