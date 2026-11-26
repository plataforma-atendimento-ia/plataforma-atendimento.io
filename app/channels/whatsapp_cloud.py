import httpx

from ..config import settings
from ..models import Channel
from .base import ChannelError, NotSupported

META_NO_EDIT = "a API oficial da Meta não permite editar mensagens: o cliente continua vendo o texto original"
META_NO_REVOKE = "a API oficial da Meta não permite apagar mensagens: o cliente continua vendo a mensagem"


class WhatsAppCloudProvider:
    """WhatsApp Cloud API oficial da Meta.

    Atenção: a Cloud API não aceita chave de idempotência. Se o processo cair entre o envio
    e a gravação do status, o reconciliador reenvia e pode haver duplicata rara. Mitigação
    planejada: conferir o status pelo webhook antes de reenviar mensagens em `sending`.
    """

    def _base(self) -> str:
        return f"https://graph.facebook.com/{settings.whatsapp_api_version}"

    async def check_credentials(self, phone_number_id: str, access_token: str) -> dict:
        """Confere na Meta se o Phone number ID e o token são válidos. Devolve o número exibido."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"{self._base()}/{phone_number_id}",
                    params={"fields": "display_phone_number,verified_name"},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.HTTPError as e:
            raise ChannelError(f"não foi possível falar com a Meta: {e}", retryable=True) from e
        if r.status_code == 200:
            return r.json()
        try:
            msg = r.json().get("error", {}).get("message", r.text[:200])
        except Exception:
            msg = r.text[:200]
        raise ChannelError(f"a Meta recusou as credenciais: {msg}", retryable=False)

    async def _post(self, channel: Channel, path: str, **kw) -> dict:
        if not channel.access_token:
            raise ChannelError("canal sem access_token", retryable=False)
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(f"{self._base()}/{path}", headers={"Authorization": f"Bearer {channel.access_token}"}, **kw)
        except httpx.HTTPError as e:
            raise ChannelError(f"rede: {e}", retryable=True) from e
        if r.status_code == 200:
            return r.json()
        raise ChannelError(f"Meta {r.status_code}: {r.text[:300]}", retryable=r.status_code == 429 or r.status_code >= 500)

    async def _upload(self, channel: Channel, payload: dict) -> str:
        with open(payload["path"], "rb") as f:
            data = f.read()
        res = await self._post(
            channel, f"{channel.phone_number_id}/media",
            data={"messaging_product": "whatsapp", "type": payload["mime"]},
            files={"file": (payload.get("filename") or "arquivo", data, payload["mime"])},
        )
        return res["id"]

    async def send(self, channel: Channel, to: str, payload: dict) -> str:
        kind = payload.get("kind", "text")
        body: dict = {"messaging_product": "whatsapp", "to": to, "type": kind}
        if kind == "text":
            body["text"] = {"body": payload.get("text", "")}
        else:
            media = {"id": await self._upload(channel, payload)}
            if kind in ("image", "video", "document") and payload.get("text"):
                media["caption"] = payload["text"]
            if kind == "document":
                media["filename"] = payload.get("filename") or "documento"
            body[kind] = media
        if payload.get("reply"):
            body["context"] = {"message_id": payload["reply"]["id"]}
        res = await self._post(channel, f"{channel.phone_number_id}/messages", json=body)
        return res["messages"][0]["id"]

    async def send_text(self, channel: Channel, to: str, body: str, idempotency_key: str) -> str:
        return await self.send(channel, to, {"kind": "text", "text": body, "key": idempotency_key})

    async def edit(self, channel, to, wamid, text):
        raise NotSupported(META_NO_EDIT)

    async def revoke(self, channel, to, wamid):
        raise NotSupported(META_NO_REVOKE)

    async def react(self, channel, to, wamid, emoji, from_me):
        await self._post(channel, f"{channel.phone_number_id}/messages", json={
            "messaging_product": "whatsapp", "to": to, "type": "reaction", "reaction": {"message_id": wamid, "emoji": emoji},
        })

    async def download_media(self, channel: Channel, media_id: str) -> tuple[bytes, str]:
        """Baixa um arquivo recebido pelo webhook (imagem, áudio...). Devolve (bytes, mime)."""
        headers = {"Authorization": f"Bearer {channel.access_token}"}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                meta = await client.get(f"{self._base()}/{media_id}", headers=headers)
                meta.raise_for_status()
                info = meta.json()
                r = await client.get(info["url"], headers=headers)
                r.raise_for_status()
        except (httpx.HTTPError, KeyError) as e:
            raise ChannelError(f"não foi possível baixar o arquivo da Meta: {e}", retryable=True) from e
        return r.content, info.get("mime_type") or r.headers.get("content-type", "application/octet-stream")
