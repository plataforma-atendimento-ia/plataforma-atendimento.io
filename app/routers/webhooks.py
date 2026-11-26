import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import base64
import json

from .. import assistant, media
from ..channels import cloud
from ..db import utcnow
from ..config import settings
from ..db import SessionLocal
from ..deps import current_user, get_db
from ..models import Channel, Contact, Message, User
from ..realtime import hub
from ..services import STATUS_RANK, conv_payload, handle_inbound, msg_out

router = APIRouter(tags=["webhooks"])


@router.get("/webhooks/whatsapp")
async def verify(
    mode: str = Query(alias="hub.mode", default=""),
    token: str = Query(alias="hub.verify_token", default=""),
    challenge: str = Query(alias="hub.challenge", default=""),
):
    if mode == "subscribe" and hmac.compare_digest(token, settings.whatsapp_verify_token):
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(403, "token de verificação inválido")


@router.post("/webhooks/whatsapp")
async def receive(request: Request):
    raw = await request.body()
    if settings.whatsapp_app_secret:
        sig = request.headers.get("x-hub-signature-256", "")
        expected = "sha256=" + hmac.new(settings.whatsapp_app_secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(401, "assinatura inválida")
    payload = json.loads(raw or b"{}")
    # Responde 200 sempre que a assinatura é válida; cada item é tratado de forma idempotente.
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            await _process_change(change.get("value", {}))
    return {"ok": True}


async def _process_change(value: dict) -> None:
    pnid = value.get("metadata", {}).get("phone_number_id")
    if not pnid:
        return
    async with SessionLocal() as s:
        channel = (await s.execute(select(Channel).where(Channel.phone_number_id == pnid))).scalar_one_or_none()
        if channel is None:
            return
        s.sync_session.info["tenant_id"] = channel.tenant_id
        names = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])}
        for m in value.get("messages", []):
            kind = m.get("type")
            reply_wamid = (m.get("context") or {}).get("id")
            if kind == "reaction":  # cliente reagiu a uma mensagem
                r = m.get("reaction") or {}
                await customer_action(s, channel.tenant_id, r.get("message_id"), "react", emoji=r.get("emoji") or "")
                continue
            text, media_info = "", None
            if kind == "text":
                text = m.get("text", {}).get("body", "")
            elif kind in ("image", "video", "audio", "document", "sticker"):
                part = m.get(kind) or {}
                text = part.get("caption", "")
                try:
                    data, mime = await cloud.download_media(channel, part.get("id"))
                    media_info = media.store_incoming(channel.tenant_id, data, part.get("mime_type") or mime,
                                                      part.get("filename"), voice=bool(part.get("voice")))
                except Exception:
                    text = text or f"[{kind} recebido, mas não foi possível baixar o arquivo]"
            else:
                text = f"[{kind or 'mensagem'} não suportado ainda]"
            await _store_inbound(s, channel, m["from"], names.get(m["from"]), text, m["id"],
                                 media_info=media_info, reply_wamid=reply_wamid)
        for st in value.get("statuses", []):
            await _apply_status(s, channel.tenant_id, st)


async def customer_action(s: AsyncSession, tenant_id: str, wamid: str | None, action: str, text: str = "", emoji: str = "") -> bool:
    """O cliente apagou, editou ou reagiu a uma mensagem no WhatsApp dele: refletimos aqui."""
    if not wamid:
        return False
    msg = (await s.execute(select(Message).where(Message.wamid == wamid, Message.tenant_id == tenant_id))).scalar_one_or_none()
    if msg is None:
        return False
    if action == "revoke" and msg.direction == "in":
        msg.deleted_at, msg.deleted_by = utcnow(), "cliente"
    elif action == "edit" and msg.direction == "in" and text:
        msg.body, msg.edited_at = text, utcnow()
    elif action == "react":
        r = json.loads(msg.reactions or "{}")
        if emoji:
            r["cliente"] = emoji
        else:
            r.pop("cliente", None)
        msg.reactions = json.dumps(r, ensure_ascii=False)
    else:
        return False
    await s.commit()
    await hub.publish(tenant_id, {"type": "message.status", "message": msg_out(msg)})
    return True


async def _store_inbound(s: AsyncSession, channel: Channel, phone, name, text, wamid, wa_jid=None,
                         media_info: dict | None = None, reply_wamid: str | None = None) -> bool:
    res = await handle_inbound(s, channel, phone, name, text, wamid, wa_jid, media=media_info, reply_wamid=reply_wamid)
    if res is None:
        return False
    conv, msg = res
    contact = await s.get(Contact, conv.contact_id)
    await hub.publish(channel.tenant_id, {"type": "message.new", "message": msg_out(msg)})
    ai_text = text or ("[o cliente mandou um áudio]" if media_info and media_info["kind"] == "audio" else "[o cliente mandou um arquivo]")
    bot_msgs = await assistant.on_inbound(s, conv, contact, ai_text)  # assistente jurídico (se ligado)
    for m in bot_msgs:
        await hub.publish(channel.tenant_id, {"type": "message.new", "message": msg_out(m)})
    await hub.publish(
        channel.tenant_id, {"type": "conversation.updated", "conversation": await conv_payload(s, conv)}
    )
    return True


async def _apply_status(s: AsyncSession, tenant_id: str, st: dict) -> None:
    msg = (
        await s.execute(select(Message).where(Message.wamid == st.get("id"), Message.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if msg is None:
        return
    new = st.get("status")
    if new == "failed":
        msg.status = "failed"
        errs = st.get("errors") or [{}]
        msg.error = str(errs[0].get("title") or errs[0].get("message") or "falha reportada pela Meta")[:500]
    elif new in STATUS_RANK and STATUS_RANK[new] > STATUS_RANK.get(msg.status, -1):
        msg.status = new  # nunca regride (ex.: read -> delivered)
    else:
        return
    await s.commit()
    await hub.publish(tenant_id, {"type": "message.status", "message": msg_out(msg)})


class SimulateIn(BaseModel):
    phone: str
    name: str | None = None
    text: str = ""
    wamid: str | None = None
    media_b64: str | None = None  # cliente de teste mandando foto/áudio/documento
    media_mime: str | None = None
    media_name: str | None = None
    voice: bool = False
    reply_to_wamid: str | None = None


class CustomerActionIn(BaseModel):
    message_id: str
    action: str  # revoke | edit | react
    text: str = ""
    emoji: str = ""


@router.post("/dev/inbound", status_code=201)
async def simulate_inbound(body: SimulateIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    """Simula um cliente escrevendo. Só em TEST_MODE e só em números de teste (nunca em número real)."""
    if not settings.test_mode:
        raise HTTPException(404)
    channel = (
        await s.execute(
            select(Channel).where(Channel.tenant_id == user.tenant_id, Channel.is_test.is_(True)).limit(1)
        )
    ).scalar_one_or_none()
    if channel is None:
        raise HTTPException(409, "conecte um número de teste antes de simular clientes")
    from ..models import uid

    media_info = None
    if body.media_b64:
        try:
            media_info = media.store_incoming(user.tenant_id, base64.b64decode(body.media_b64), body.media_mime or "",
                                              body.media_name, body.voice)
        except (ValueError, media.MediaError) as e:
            raise HTTPException(422, f"arquivo inválido: {e}")
    if not body.text and not media_info:
        raise HTTPException(422, "mande um texto ou um arquivo")
    created = await _store_inbound(s, channel, body.phone, body.name, body.text, body.wamid or f"sim.{uid()}",
                                   media_info=media_info, reply_wamid=body.reply_to_wamid)
    return {"created": created}


@router.post("/dev/customer-action")
async def simulate_customer_action(body: CustomerActionIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    """Simula o cliente apagando, editando ou reagindo (só em TEST_MODE)."""
    if not settings.test_mode:
        raise HTTPException(404)
    m = await s.get(Message, body.message_id)
    if m is None or m.tenant_id != user.tenant_id or not m.wamid:
        raise HTTPException(404, "mensagem não encontrada")
    return {"applied": await customer_action(s, user.tenant_id, m.wamid, body.action, body.text, body.emoji)}
