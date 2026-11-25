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

    async def send_text(self, channel: Channel, to: str, body: str, idempotency_key: str) -> str:
        try:
            data = await request("POST", f"/sessions/{channel.id}/send", {"to": to, "text": body, "key": idempotency_key})
        except BridgeDown as e:
            raise ChannelError(f"ponte do WhatsApp (QR) não está rodando: {e}", retryable=True) from e
        return data["id"]
