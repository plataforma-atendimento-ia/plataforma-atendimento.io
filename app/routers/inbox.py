from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..deps import current_user, get_db
from ..models import Contact, Conversation, Message, User
from ..realtime import hub
from ..services import claim_conversation, conv_out, msg_out, new_idempotency_key

router = APIRouter(tags=["inbox"])


class SendIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    client_id: str | None = Field(default=None, max_length=64)  # idempotência do cliente


class TransferIn(BaseModel):
    user_id: str | None = None  # None devolve à fila


async def _conv(s: AsyncSession, user: User, conv_id: str) -> Conversation:
    c = (
        await s.execute(
            select(Conversation).where(Conversation.id == conv_id, Conversation.tenant_id == user.tenant_id)
        )
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, "conversa não encontrada")
    return c


async def _publish_conv(s: AsyncSession, c: Conversation, kind: str = "conversation.updated"):
    contact = await s.get(Contact, c.contact_id)
    await hub.publish(c.tenant_id, {"type": kind, "conversation": conv_out(c, contact)})


@router.get("/conversations")
async def list_conversations(
    view: str = "all", user: User = Depends(current_user), s: AsyncSession = Depends(get_db)
):
    q = (
        select(Conversation, Contact)
        .join(Contact, Contact.id == Conversation.contact_id)
        .where(Conversation.tenant_id == user.tenant_id)
        .order_by(Conversation.last_message_at.desc())
        .limit(200)
    )
    if view == "queue":
        q = q.where(Conversation.status == "queue")
    elif view == "mine":
        q = q.where(Conversation.assigned_to == user.id, Conversation.status == "open")
    elif view == "closed":
        q = q.where(Conversation.status == "closed")
    else:
        q = q.where(Conversation.status != "closed")
    rows = (await s.execute(q)).all()
    return [conv_out(c, ct) for c, ct in rows]


@router.get("/conversations/{conv_id}/messages")
async def list_messages(conv_id: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    await _conv(s, user, conv_id)
    rows = (
        await s.execute(
            select(Message)
            .where(Message.conversation_id == conv_id, Message.tenant_id == user.tenant_id)
            .order_by(Message.created_at)
        )
    ).scalars().all()
    return [msg_out(m) for m in rows]


@router.post("/conversations/{conv_id}/claim")
async def claim(conv_id: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    c = await _conv(s, user, conv_id)
    open_count = (
        await s.execute(
            select(func.count()).select_from(Conversation).where(
                Conversation.tenant_id == user.tenant_id,
                Conversation.assigned_to == user.id,
                Conversation.status == "open",
            )
        )
    ).scalar_one()
    if open_count >= user.max_concurrent:
        raise HTTPException(409, "limite de conversas simultâneas atingido")
    if not await claim_conversation(s, user.tenant_id, conv_id, user.id):
        raise HTTPException(409, "outro atendente já assumiu esta conversa")
    await s.refresh(c)
    await _publish_conv(s, c, "conversation.claimed")
    return conv_out(c, await s.get(Contact, c.contact_id))


@router.post("/conversations/{conv_id}/transfer")
async def transfer(
    conv_id: str, body: TransferIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_db)
):
    c = await _conv(s, user, conv_id)
    if user.role != "admin" and c.assigned_to != user.id:
        raise HTTPException(403, "só o responsável ou um admin pode transferir")
    new_status = "queue"
    if body.user_id:
        target = await s.get(User, body.user_id)
        if target is None or target.tenant_id != user.tenant_id:
            raise HTTPException(404, "atendente não encontrado")
        new_status = "open"
    await s.execute(
        update(Conversation)
        .where(Conversation.id == conv_id, Conversation.tenant_id == user.tenant_id)
        .values(assigned_to=body.user_id, status=new_status, version=Conversation.version + 1)
    )
    await s.commit()
    await s.refresh(c)
    await _publish_conv(s, c)
    return conv_out(c, await s.get(Contact, c.contact_id))


@router.post("/conversations/{conv_id}/close")
async def close(conv_id: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    c = await _conv(s, user, conv_id)
    if user.role != "admin" and c.assigned_to != user.id:
        raise HTTPException(403, "só o responsável ou um admin pode encerrar")
    c.status = "closed"
    c.version += 1
    await s.commit()
    await _publish_conv(s, c)
    return conv_out(c, await s.get(Contact, c.contact_id))


@router.post("/conversations/{conv_id}/messages", status_code=201)
async def send_message(
    conv_id: str, body: SendIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_db)
):
    c = await _conv(s, user, conv_id)
    if c.assigned_to != user.id or c.status != "open":
        raise HTTPException(403, "assuma a conversa antes de responder")
    key = body.client_id or new_idempotency_key()
    existing = (
        await s.execute(select(Message).where(Message.conversation_id == conv_id, Message.idempotency_key == key))
    ).scalar_one_or_none()
    if existing:  # reenvio do mesmo clique: devolve a mesma mensagem
        return msg_out(existing)
    m = Message(
        tenant_id=user.tenant_id,
        conversation_id=conv_id,
        direction="out",
        body=body.body,
        status="queued",  # gravada ANTES de qualquer envio (outbox)
        sender_user_id=user.id,
        idempotency_key=key,
        next_attempt_at=utcnow(),
    )
    s.add(m)
    c.last_message_at = utcnow()
    c.last_preview = body.body[:200]
    try:
        await s.commit()
    except IntegrityError:
        await s.rollback()
        existing = (
            await s.execute(select(Message).where(Message.conversation_id == conv_id, Message.idempotency_key == key))
        ).scalar_one()
        return msg_out(existing)
    await hub.publish(user.tenant_id, {"type": "message.new", "message": msg_out(m)})
    return msg_out(m)


@router.post("/messages/{message_id}/resend")
async def resend(message_id: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    m = (
        await s.execute(select(Message).where(Message.id == message_id, Message.tenant_id == user.tenant_id))
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, "mensagem não encontrada")
    if m.direction != "out" or m.status != "failed":
        raise HTTPException(409, "só mensagens com falha podem ser reenviadas")
    m.status, m.attempts, m.error, m.next_attempt_at = "queued", 0, None, utcnow()
    await s.commit()
    await hub.publish(user.tenant_id, {"type": "message.status", "message": msg_out(m)})
    return msg_out(m)
