"""Rotas chamadas PELA ponte QR (pasta bridge/). Protegidas por segredo compartilhado."""
import hmac

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..config import settings
from ..db import SessionLocal
from ..models import Channel
from ..realtime import hub
import base64

from .. import media
from .webhooks import _apply_status, _store_inbound, customer_action

router = APIRouter(prefix="/bridge", tags=["bridge"])


def _check(secret: str | None) -> None:
    if not settings.bridge_secret:
        raise HTTPException(503, "ponte QR desligada (defina BRIDGE_SECRET)")
    if not secret or not hmac.compare_digest(secret, settings.bridge_secret):
        raise HTTPException(401, "segredo inválido")


async def _qr_channel(s, channel_id: str) -> Channel:
    ch = (await s.execute(select(Channel).where(Channel.id == channel_id, Channel.via == "qr"))).scalar_one_or_none()
    if ch is None:
        raise HTTPException(404, "canal QR desconhecido")
    s.sync_session.info["tenant_id"] = ch.tenant_id
    return ch


class EventIn(BaseModel):
    channel_id: str
    type: str = Field(pattern="^(connected|logged_out)$")
    phone: str | None = None


class InboundIn(BaseModel):
    channel_id: str
    jid: str = Field(max_length=64)
    phone: str = Field(min_length=3, max_length=32)
    name: str | None = Field(default=None, max_length=200)
    text: str = Field(default="", max_length=8000)
    wamid: str = Field(max_length=128)
    reply_wamid: str | None = Field(default=None, max_length=128)
    media_b64: str | None = None
    media_mime: str | None = Field(default=None, max_length=120)
    media_name: str | None = Field(default=None, max_length=255)
    voice: bool = False


class UpdateIn(BaseModel):
    channel_id: str
    wamid: str = Field(max_length=128)
    action: str = Field(pattern="^(revoke|edit|react)$")
    text: str = Field(default="", max_length=8000)
    emoji: str = Field(default="", max_length=8)


class StatusIn(BaseModel):
    channel_id: str
    wamid: str
    status: str = Field(pattern="^(sent|delivered|read)$")


@router.post("/event")
async def event(body: EventIn, x_bridge_secret: str | None = Header(default=None)):
    _check(x_bridge_secret)
    async with SessionLocal() as s:
        ch = await _qr_channel(s, body.channel_id)
        if body.type == "connected":
            ch.link_status = "connected"
            if body.phone:
                ch.display_phone = "+" + "".join(c for c in body.phone if c.isdigit())
        else:
            ch.link_status = "disconnected"
        await s.commit()
        await hub.publish(ch.tenant_id, {"type": "channel.updated", "channel": {"id": ch.id, "link_status": ch.link_status}})
    return {"ok": True}


@router.post("/inbound")
async def inbound(body: InboundIn, x_bridge_secret: str | None = Header(default=None)):
    _check(x_bridge_secret)
    async with SessionLocal() as s:
        ch = await _qr_channel(s, body.channel_id)
        if ch.link_status == "disconnected":
            return {"created": False}
        media_info = None
        if body.media_b64:
            try:
                media_info = media.store_incoming(ch.tenant_id, base64.b64decode(body.media_b64), body.media_mime or "",
                                                  body.media_name, body.voice)
            except (ValueError, media.MediaError):
                body.text = body.text or "[arquivo recebido, mas inválido]"
        created = await _store_inbound(s, ch, body.phone, body.name, body.text, body.wamid, wa_jid=body.jid,
                                       media_info=media_info, reply_wamid=body.reply_wamid)
    return {"created": created}


@router.post("/update")
async def update(body: UpdateIn, x_bridge_secret: str | None = Header(default=None)):
    """O cliente apagou, editou ou reagiu a uma mensagem."""
    _check(x_bridge_secret)
    async with SessionLocal() as s:
        ch = await _qr_channel(s, body.channel_id)
        applied = await customer_action(s, ch.tenant_id, body.wamid, body.action, body.text, body.emoji)
    return {"applied": applied}


@router.post("/status")
async def status(body: StatusIn, x_bridge_secret: str | None = Header(default=None)):
    _check(x_bridge_secret)
    async with SessionLocal() as s:
        ch = await _qr_channel(s, body.channel_id)
        await _apply_status(s, ch.tenant_id, {"id": body.wamid, "status": body.status})
    return {"ok": True}
