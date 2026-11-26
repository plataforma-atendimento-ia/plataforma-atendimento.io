"""Cliente da ponte NÃO OFICIAL por QR Code (pasta bridge/)."""
import httpx

from ..config import settings
from ..models import Channel
from .base import ChannelError


class BridgeDown(Exception):
    pass


async def request(method: str, path: str, json: dict | None = None, timeout: float = 15) -> dict:
    """Chamada à ponte local. Levanta BridgeDown se ela não estiver rodando."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.request(
                method, settings.bridge_url + path, json=json, headers={"x-bridge-secret": settings.bridge_secret}
            )
    except httpx.HTTPError as e:
        raise BridgeDown(str(e)) from e
    try:
        data = r.json()
    except Exception:
        data = {}
    if r.status_code >= 400:
        raise ChannelError(str(data.get("error", f"ponte respondeu {r.status_code}")), retryable=bool(data.get("retryable", r.status_code >= 500)))
    return data


async def bridge_up() -> bool:
    if not settings.bridge_secret:
        return False
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            return (await client.get(settings.bridge_url + "/health")).status_code == 200
    except httpx.HTTPError:
        return False


class BridgeProvider:
    """Envia pela ponte QR. `to` é o endereço exato do contato (jid), guardado ao receber a mensagem."""

    async def _call(self, channel: Channel, action: str, body: dict) -> dict:
        try:
            return await request("POST", f"/sessions/{channel.id}/{action}", body, timeout=90)
        except BridgeDown as e:
            raise ChannelError(f"ponte do WhatsApp (QR) não está rodando: {e}", retryable=True) from e

    async def send(self, channel: Channel, to: str, payload: dict) -> str:
        body = {"to": to, "text": payload.get("text", ""), "key": payload.get("key"), "kind": payload.get("kind", "text"),
                "path": payload.get("path"), "mime": payload.get("mime"), "filename": payload.get("filename"),
                "voice": bool(payload.get("voice")), "reply": payload.get("reply")}
        return (await self._call(channel, "send", body))["id"]

    async def send_text(self, channel: Channel, to: str, body: str, idempotency_key: str) -> str:
        return await self.send(channel, to, {"kind": "text", "text": body, "key": idempotency_key})

    async def edit(self, channel, to, wamid, text):
        await self._call(channel, "edit", {"to": to, "id": wamid, "text": text})

    async def revoke(self, channel, to, wamid):
        await self._call(channel, "revoke", {"to": to, "id": wamid})

    async def react(self, channel, to, wamid, emoji, from_me):
        await self._call(channel, "react", {"to": to, "id": wamid, "emoji": emoji, "from_me": from_me})
