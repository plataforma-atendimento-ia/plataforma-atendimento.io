import httpx

from ..config import settings
from ..models import Channel
from .base import ChannelError


class WhatsAppCloudProvider:
    """WhatsApp Cloud API oficial da Meta.

    Atenção: a Cloud API não aceita chave de idempotência. Se o processo cair entre o envio
    e a gravação do status, o reconciliador reenvia e pode haver duplicata rara. Mitigação
    planejada: conferir o status pelo webhook antes de reenviar mensagens em `sending`.
    """

    async def send_text(self, channel: Channel, to: str, body: str, idempotency_key: str) -> str:
        if not channel.access_token:
            raise ChannelError("canal sem access_token", retryable=False)
        url = f"https://graph.facebook.com/{settings.whatsapp_api_version}/{channel.phone_number_id}/messages"
        payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    url, json=payload, headers={"Authorization": f"Bearer {channel.access_token}"}
                )
        except httpx.HTTPError as e:
            raise ChannelError(f"rede: {e}", retryable=True) from e
        if r.status_code == 200:
            return r.json()["messages"][0]["id"]
        retryable = r.status_code == 429 or r.status_code >= 500
        raise ChannelError(f"Meta {r.status_code}: {r.text[:300]}", retryable=retryable)
