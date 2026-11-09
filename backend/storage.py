import hashlib
import os
import tempfile
from pathlib import Path

from .config import HOST_STORAGE_ROOTS, RECORDINGS_DIR
from .db import get_setting, set_setting


EXCLUDED_FILESYSTEMS = {
    "autofs", "cgroup", "cgroup2", "devpts", "devtmpfs", "fusectl",
    "mqueue", "nsfs", "overlay", "proc", "pstore", "securityfs",
    "sysfs", "tmpfs", "tracefs",
}


class StorageUnavailableError(RuntimeError):
    pass


def _unescape_mount_path(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _host_visible_path(path: Path) -> str:
    value = str(path)
    for root in HOST_STORAGE_ROOTS:
        root_text = str(root)
        if value == root_text:
            return "/" + root_text.removeprefix("/host/") 
        if value.startswith(root_text.rstrip("/") + "/"):
            return "/" + value[len("/host/"):]
    return value


def discover_drives():
    """Find mounted host block/network filesystems exposed through /mnt and /media."""
    found = {}
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    for line in lines:
        try:
            left, right = line.split(" - ", 1)
            fields = left.split()
            right_fields = right.split()
            mountpoint = Path(_unescape_mount_path(fields[4]))
            filesystem = right_fields[0]
            source = _unescape_mount_path(right_fields[1]) if len(right_fields) > 1 else "unknown"
            major_minor = fields[2]
        except (ValueError, IndexError):
            continue

        under_host_root = False
        for root in HOST_STORAGE_ROOTS:
            root_text = str(root).rstrip("/")
            if str(mountpoint).startswith(root_text + "/"):
                under_host_root = True
                break
        if not under_host_root or filesystem in EXCLUDED_FILESYSTEMS:
            continue
        if not (
            source.startswith("/dev/")
            or filesystem.lower() in {"nfs", "nfs4", "cifs", "smb3", "fuseblk", "fuse"}
        ):
            continue

        try:
            stats = os.statvfs(mountpoint)
            total = stats.f_blocks * stats.f_frsize
            free = stats.f_bavail * stats.f_frsize
            used = max(0, total - stats.f_bfree * stats.f_frsize)
            if total <= 0:
                continue
        except OSError:
            continue

        identifier = hashlib.sha256(
            f"{source}|{mountpoint}|{filesystem}".encode("utf-8")
        ).hexdigest()[:16]
        label = mountpoint.name or Path(source).name or "Внешний диск"
        found[identifier] = {
            "id": identifier,
            "name": label,
            "source": source,
            "filesystem": filesystem,
            "mountpoint": _host_visible_path(mountpoint),
            "container_mountpoint": str(mountpoint),
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "used_percent": round(used / total * 100, 1),
        }

    return sorted(found.values(), key=lambda item: (item["name"].lower(), item["mountpoint"]))


def get_storage_payload():
    drives = discover_drives()
    selected_id = get_setting("storage_device_id")
    selected = next((item for item in drives if item["id"] == selected_id), None)
    if not selected_id:
        return {
            "mode": "internal",
            "selected_id": None,
            "path": str(RECORDINGS_DIR),
            "display_path": str(RECORDINGS_DIR),
            "available": RECORDINGS_DIR.exists() and os.access(RECORDINGS_DIR, os.W_OK),
            "error": None,
            "devices": drives,
        }

    if selected is None:
        return {
            "mode": "external",
            "selected_id": selected_id,
            "path": None,
            "display_path": None,
            "available": False,
            "error": "Выбранный диск не подключён или не смонтирован.",
            "devices": drives,
        }

    path = Path(selected["container_mountpoint"]) / "recordings"
    return {
        "mode": "external",
        "selected_id": selected_id,
        "path": str(path),
        "display_path": f"{selected['mountpoint'].rstrip('/')}/recordings",
        "available": path.parent.exists() and os.access(path.parent, os.W_OK),
        "error": None,
        "devices": drives,
    }


def resolve_recordings_dir() -> Path:
    selected_id = get_setting("storage_device_id")
    if not selected_id:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        if not os.access(RECORDINGS_DIR, os.W_OK):
            raise StorageUnavailableError(f"Каталог записей недоступен для записи: {RECORDINGS_DIR}")
        return RECORDINGS_DIR

    drive = next((item for item in discover_drives() if item["id"] == selected_id), None)
    if drive is None:
        raise StorageUnavailableError(
            "Выбранный внешний диск не подключён. Запись остановлена, чтобы не заполнить системный диск."
        )

    mountpoint = Path(drive["container_mountpoint"])
    target = mountpoint / "recordings"
    try:
        target.mkdir(parents=True, exist_ok=True)
        if os.stat(target).st_dev != os.stat(mountpoint).st_dev:
            raise StorageUnavailableError("Каталог recordings оказался на другом разделе.")
        with tempfile.NamedTemporaryFile(prefix=".twitch-archive-check-", dir=target, delete=True):
            pass
    except StorageUnavailableError:
        raise
    except OSError as exc:
        raise StorageUnavailableError(f"Нет доступа к диску {drive['name']}: {exc}") from exc
    return target


def update_storage_device(device_id: str | None):
    if not device_id:
        set_setting("storage_device_id", None)
        return get_storage_payload()

    drive = next((item for item in discover_drives() if item["id"] == device_id), None)
    if drive is None:
        raise StorageUnavailableError("Выбранный диск больше не обнаруживается.")
    resolve_before = Path(drive["container_mountpoint"])
    if not os.access(resolve_before, os.W_OK):
        raise StorageUnavailableError("У контейнера нет прав на запись во внешний диск.")
    target = resolve_before / "recordings"
    try:
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".twitch-archive-check-", dir=target, delete=True):
            pass
    except OSError as exc:
        raise StorageUnavailableError(f"Не удалось проверить запись на диск: {exc}") from exc

    set_setting("storage_device_id", device_id)
    return get_storage_payload()


def is_allowed_recording_path(file_path: str) -> bool:
    try:
        path = Path(file_path).resolve()
        roots = [RECORDINGS_DIR.resolve()]
        roots.extend(
            (Path(item["container_mountpoint"]) / "recordings").resolve()
            for item in discover_drives()
        )
        return any(path == root or root in path.parents for root in roots)
    except OSError:
        return False
