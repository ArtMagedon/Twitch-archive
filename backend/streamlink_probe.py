import asyncio
import json
import shutil


class StreamlinkProbeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        exit_code: int | None = None,
        stderr: str = "",
        stdout: str = "",
    ):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = stdout

    def as_dict(self):
        return {
            "error": str(self),
            "exit_code": self.exit_code,
            "stderr": self.stderr[-6000:],
            "stdout": self.stdout[-2000:],
        }


def streamlink_browser_available() -> tuple[str | None, str | None]:
    return shutil.which("streamlink"), shutil.which("chromium")


async def probe_twitch_channel(
    login: str,
    *,
    timeout: int = 25,
    loglevel: str = "error",
) -> dict:
    """
    Inspect a public Twitch stream using Streamlink's Twitch plugin.

    No application-level Twitch Helix credentials are used. Streamlink handles
    Twitch's own access-token flow and requests a Client Integrity token through
    its isolated Chromium browser when Twitch requires one.
    """
    executable, chromium = streamlink_browser_available()
    if not executable:
        raise StreamlinkProbeError("Streamlink не найден в PATH контейнера.")
    if not chromium:
        raise StreamlinkProbeError(
            "Chromium не найден. Он нужен Streamlink для получения Twitch Client Integrity token."
        )

    args = [
        executable,
        "--no-config",
        "--json",
        "--loglevel",
        loglevel,
        "--webbrowser",
        "yes",
        "--webbrowser-headless",
        "yes",
        "--webbrowser-executable",
        chromium,
        "--webbrowser-timeout",
        "15",
        f"https://www.twitch.tv/{login}",
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise StreamlinkProbeError(
            f"Не удалось запустить Streamlink: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        process.kill()
        stdout, stderr = await process.communicate()
        raise StreamlinkProbeError(
            f"Streamlink не ответил за {timeout} секунд.",
            exit_code=process.returncode,
            stderr=stderr.decode("utf-8", errors="replace"),
            stdout=stdout.decode("utf-8", errors="replace"),
        ) from exc
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
            await process.communicate()
        raise

    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()

    if process.returncode != 0:
        lowered = stderr_text.lower()
        if "no playable streams found" in lowered or "no streams found" in lowered:
            return {
                "live": False,
                "metadata": {},
                "available_qualities": [],
                "stderr": stderr_text[-6000:],
                "exit_code": process.returncode,
            }

        raise StreamlinkProbeError(
            stderr_text[-6000:]
            or f"Streamlink завершился с кодом {process.returncode} без диагностического вывода.",
            exit_code=process.returncode,
            stderr=stderr_text,
            stdout=stdout_text,
        )

    try:
        payload = json.loads(stdout_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise StreamlinkProbeError(
            f"Streamlink вернул некорректный JSON: {exc}",
            exit_code=process.returncode,
            stderr=stderr_text,
            stdout=stdout_text,
        ) from exc

    streams = payload.get("streams") or {}
    metadata = payload.get("metadata") or {}
    return {
        "live": bool(streams),
        "metadata": {
            "id": metadata.get("id"),
            "title": metadata.get("title"),
            "game_name": metadata.get("category") or metadata.get("game"),
            "user_name": metadata.get("author") or login,
        },
        "available_qualities": list(streams.keys()),
        "stderr": stderr_text[-6000:],
        "exit_code": process.returncode,
    }
