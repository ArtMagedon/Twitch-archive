import time
import httpx
from .config import CLIENT_ID, CLIENT_SECRET

class TwitchClient:
    def __init__(self):
        self.token = None
        self.expires_at = 0

    async def _token(self):
        if self.token and time.time() < self.expires_at - 60:
            return self.token
        if not CLIENT_ID or not CLIENT_SECRET or CLIENT_ID == "replace_me":
            raise RuntimeError("Не настроены TWITCH_CLIENT_ID и TWITCH_CLIENT_SECRET в .env")
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post("https://id.twitch.tv/oauth2/token", data={
                "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                "grant_type": "client_credentials",
            })
            response.raise_for_status()
            payload = response.json()
        self.token = payload["access_token"]
        self.expires_at = time.time() + int(payload.get("expires_in", 3600))
        return self.token

    async def live_streams(self, logins: list[str]) -> dict:
        if not logins:
            return {}
        token = await self._token()
        params = [("user_login", login) for login in logins]
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get("https://api.twitch.tv/helix/streams", params=params,
                headers={"Client-Id": CLIENT_ID, "Authorization": f"Bearer {token}"})
            response.raise_for_status()
            rows = response.json().get("data", [])
        return {row["user_login"].lower(): row for row in rows}
