import logging
import time

import httpx

from .config import CLIENT_ID, CLIENT_SECRET

log = logging.getLogger("twitch")


class TwitchClient:
    def __init__(self):
        self.token = None
        self.expires_at = 0

    async def _token(self, force=False):
        if not force and self.token and time.time() < self.expires_at - 60:
            return self.token
        if not CLIENT_ID or not CLIENT_SECRET or CLIENT_ID == "replace_me":
            raise RuntimeError("Не настроены TWITCH_CLIENT_ID и TWITCH_CLIENT_SECRET в .env")

        log.info("Запрашиваю App Access Token Twitch")
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://id.twitch.tv/oauth2/token",
                data={
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "grant_type": "client_credentials",
                },
            )
            response.raise_for_status()
            payload = response.json()

        self.token = payload["access_token"]
        self.expires_at = time.time() + int(payload.get("expires_in", 3600))
        log.success("Токен Twitch получен; срок действия около %s сек.", payload.get("expires_in", 3600))
        return self.token

    async def _get(self, endpoint, params=None):
        token = await self._token()
        headers = {"Client-Id": CLIENT_ID, "Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.get(
                f"https://api.twitch.tv/helix/{endpoint}",
                params=params,
                headers=headers,
            )
            if response.status_code == 401:
                token = await self._token(force=True)
                headers["Authorization"] = f"Bearer {token}"
                response = await client.get(
                    f"https://api.twitch.tv/helix/{endpoint}",
                    params=params,
                    headers=headers,
                )
            response.raise_for_status()
            return response.json()

    async def live_streams(self, logins: list[str]) -> dict:
        if not logins:
            return {}

        logins = list(dict.fromkeys(login.lower() for login in logins))
        rows = []
        for offset in range(0, len(logins), 100):
            batch = logins[offset:offset + 100]
            params = [("user_login", login) for login in batch]
            payload = await self._get("streams", params=params)
            rows.extend(payload.get("data", []))

        return {row["user_login"].lower(): row for row in rows}

    async def check_connection(self):
        payload = await self._get("streams", params={"first": 1})
        return {"ok": True, "sample_count": len(payload.get("data", []))}
