"""Ações sobre mensagens no estilo WhatsApp: anexos, áudio gravado, responder, editar, apagar para todos e reagir.

O que o canal não permite (ex.: a API oficial da Meta não edita nem apaga) é feito só no sistema e fica
anotado em `remote_note`, para o atendente saber que o cliente ainda vê a versão original."""
import json
import os
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import media
from ..channels import provider_for
from ..channels.base import ChannelError, NotSupported
from ..config import settings
from ..db import utcnow
from ..deps import current_user, get_db
from ..models import Channel, Contact, Conversation, Message, User
from ..realtime import hub
from ..security import decode_token
from ..services import msg_out, new_idempotency_key, preview_of

router = APIRouter(tags=["mensagens"])
REACTIONS = {"👍", "❤️", "😂", "😮", "😢", "🙏", ""}


async def _conv_for_reply(s: AsyncSession, user: User, conv_id: str) -> Conversation:
    c = (await s.execute(select(Conversation).where(Conversation.id == conv_id, Conversation.tenant_id == user.tenant_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, "conversa não encontrada")
    if c.assigned_to != user.id or c.status != "open":
        raise HTTPException(403, "assuma a conversa antes de responder")
    return c


async def _valid_reply(s: AsyncSession, conv_id: str, reply_to_id: str | None) -> str | None:
    if not reply_to_id:
        return None
    q = await s.get(Message, reply_to_id)
    if q is None or q.conversation_id != conv_id:
        raise HTTPException(422, "a mensagem citada não é desta conversa")
    return q.id


@router.post("/conversations/{conv_id}/attachments", status_code=201)
async def send_attachment(
    conv_id: str,
    file: UploadFile = File(...),
    caption: str = Form("", max_length=1024),
    voice: bool = Form(False),
    client_id: str | None = Form(None, max_length=64),
    reply_to_id: str | None = Form(None, max_length=32),
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_db),
):
    c = await _conv_for_reply(s, user, conv_id)
    mime = media.base_mime(file.content_type or "")
    try:
        kind = media.kind_for(mime)
        data = await file.read(media.LIMITS[kind] + 1)
        media.check_size(kind, len(data))
    except media.MediaError as e:
        raise HTTPException(422, str(e))
    key = client_id or new_idempotency_key()
    existing = (await s.execute(select(Message).where(Message.conversation_id == conv_id, Message.idempotency_key == key))).scalar_one_or_none()
    if existing:
        return msg_out(existing)
    rel = media.save(user.tenant_id, data, mime, file.filename)
    size = len(data)
    if kind == "audio" and (voice or mime in ("audio/webm", "audio/wav")) and mime != "audio/ogg":
        try:  # o WhatsApp só aceita mensagem de voz em OGG/Opus
            rel, size = await media.to_voice(user.tenant_id, rel)
            mime = "audio/ogg"
        except media.MediaError as e:
            raise HTTPException(422, str(e))
    m = Message(
        tenant_id=user.tenant_id, conversation_id=conv_id, direction="out", body=caption.strip(), status="queued",
        sender_user_id=user.id, idempotency_key=key, next_attempt_at=utcnow(), kind=kind, media_path=rel, media_mime=mime,
        media_name=(file.filename or "arquivo")[:255], media_size=size, voice=bool(voice and kind == "audio"),
        reply_to_id=await _valid_reply(s, conv_id, reply_to_id),
    )
    s.add(m)
    c.last_message_at, c.last_preview = utcnow(), preview_of(m)
    try:
        await s.commit()
    except IntegrityError:
        await s.rollback()
        raise HTTPException(409, "mensagem repetida")
    await hub.publish(user.tenant_id, {"type": "message.new", "message": msg_out(m)})
    return msg_out(m)


@router.get("/media/{message_id}")
async def get_media(
    message_id: str, token: str | None = Query(None), authorization: str | None = Header(None), s: AsyncSession = Depends(get_db)
):
    """Abre o arquivo de uma mensagem. Aceita o token na URL porque <img> e <audio> não mandam cabeçalho."""
    raw = token or (authorization or "").removeprefix("Bearer ").strip()
    claims = decode_token(raw) if raw else None
    if not claims:
        raise HTTPException(401, "não autenticado")
    m = await s.get(Message, message_id)
    if m is None or m.tenant_id != claims["tid"] or not m.media_path or m.deleted_at:
        raise HTTPException(404, "arquivo não encontrado")
    path = media.abs_path(m.media_path)
    if not os.path.exists(path):
        raise HTTPException(404, "arquivo não encontrado")
    return FileResponse(
        path, media_type=m.media_mime or "application/octet-stream",
        filename=m.media_name if m.kind == "document" else None,
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


async def _context(s: AsyncSession, user: User, message_id: str):
    m = await s.get(Message, message_id)
    if m is None or m.tenant_id != user.tenant_id:
        raise HTTPException(404, "mensagem não encontrada")
    c = await s.get(Conversation, m.conversation_id)
    contact = await s.get(Contact, c.contact_id)
    channel = await s.get(Channel, c.channel_id)
    return m, c, contact, channel


def _mine(user: User, m: Message) -> None:
    if m.direction != "out" or (m.sender_user_id != user.id and user.role != "admin"):
        raise HTTPException(403, "você só pode alterar mensagens que enviou")


class EditIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@router.patch("/messages/{message_id}")
async def edit_message(message_id: str, body: EditIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    m, c, contact, channel = await _context(s, user, message_id)
    _mine(user, m)
    if m.deleted_at or (m.kind or "text") != "text":
        raise HTTPException(409, "só dá para editar mensagens de texto")
    if utcnow() - m.created_at > timedelta(minutes=settings.edit_window_minutes):
        raise HTTPException(409, f"o WhatsApp só permite editar até {settings.edit_window_minutes} minutos depois do envio")
    new = body.body.strip()
    if m.wamid and m.status not in ("queued", "failed", "canceled"):
        try:
            await provider_for(channel).edit(channel, contact.wa_jid or contact.phone, m.wamid, new)
            m.remote_note = None
        except NotSupported as e:
            m.remote_note = str(e)
        except ChannelError as e:
            raise HTTPException(502, f"não foi possível editar no WhatsApp agora: {e}")
    m.body, m.edited_at = new, utcnow()
    if c.last_preview and c.last_message_at <= m.created_at:
        c.last_preview = preview_of(m)
    await s.commit()
    await hub.publish(user.tenant_id, {"type": "message.status", "message": msg_out(m)})
    return msg_out(m)


@router.delete("/messages/{message_id}")
async def delete_for_everyone(message_id: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    m, c, contact, channel = await _context(s, user, message_id)
    _mine(user, m)
    if m.deleted_at:
        return msg_out(m)
    if utcnow() - m.created_at > timedelta(hours=settings.delete_window_hours):
        raise HTTPException(409, f"o WhatsApp só permite apagar para todos até {settings.delete_window_hours} horas depois do envio")
    if m.status in ("queued", "failed"):
        m.status = "canceled"  # ainda não saiu: só cancela o envio
    elif m.wamid:
        try:
            await provider_for(channel).revoke(channel, contact.wa_jid or contact.phone, m.wamid)
            m.remote_note = None
        except NotSupported as e:
            m.remote_note = str(e)
        except ChannelError as e:
            raise HTTPException(502, f"não foi possível apagar no WhatsApp agora: {e}")
    m.deleted_at, m.deleted_by = utcnow(), user.id
    if c.last_message_at <= m.created_at:
        c.last_preview = preview_of(m)
    await s.commit()
    await hub.publish(user.tenant_id, {"type": "message.status", "message": msg_out(m)})
    return msg_out(m)


class ReactIn(BaseModel):
    emoji: str = Field(max_length=8)


@router.post("/messages/{message_id}/react")
async def react(message_id: str, body: ReactIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    m, c, contact, channel = await _context(s, user, message_id)
    if body.emoji not in REACTIONS:
        raise HTTPException(422, "reação não disponível")
    if c.assigned_to != user.id and user.role != "admin":
        raise HTTPException(403, "assuma a conversa antes de reagir")
    if m.deleted_at:
        raise HTTPException(409, "mensagem apagada")
    if m.wamid:
        try:
            await provider_for(channel).react(channel, contact.wa_jid or contact.phone, m.wamid, body.emoji, m.direction == "out")
        except NotSupported:
            pass
        except ChannelError as e:
            raise HTTPException(502, f"não foi possível reagir no WhatsApp agora: {e}")
    r = json.loads(m.reactions or "{}")
    if body.emoji:
        r[user.id] = body.emoji
    else:
        r.pop(user.id, None)
    m.reactions = json.dumps(r, ensure_ascii=False)
    await s.commit()
    await hub.publish(user.tenant_id, {"type": "message.status", "message": msg_out(m)})
    return msg_out(m)
