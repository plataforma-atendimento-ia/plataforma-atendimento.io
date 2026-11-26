from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..automation import lead_out
from ..db import utcnow
from ..deps import current_user, get_db
from ..models import Contact, Conversation, Department, DepartmentMember, Lead, Message, User
from ..realtime import hub
from ..services import claim_conversation, conv_out, conv_payload, msg_out, new_idempotency_key

router = APIRouter(tags=["inbox"])


class SendIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    client_id: str | None = Field(default=None, max_length=64)  # idempotência do cliente


class TransferIn(BaseModel):
    user_id: str | None = None  # None devolve à fila
    department_id: str | None = None  # transferir para a fila de outro setor


async def my_departments(s: AsyncSession, user: User) -> set[str] | None:
    """Setores que a pessoa atende. None = administrador (vê todos)."""
    if user.role == "admin":
        return None
    return set((await s.execute(select(DepartmentMember.department_id).where(DepartmentMember.user_id == user.id))).scalars().all())


def _visible(q, user: User, depts: set[str] | None):
    if depts is None:
        return q
    return q.where(
        or_(
            Conversation.assigned_to == user.id,
            Conversation.department_id.in_(depts or {"-"}),
            Conversation.department_id.is_(None),  # ainda em triagem: qualquer um pode ver
        )
    )


def can_take(c: Conversation, user: User, depts: set[str] | None) -> bool:
    return depts is None or c.department_id is None or c.department_id in depts


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
    await hub.publish(c.tenant_id, {"type": kind, "conversation": await conv_payload(s, c)})


@router.get("/conversations")
async def list_conversations(
    view: str = "all", user: User = Depends(current_user), s: AsyncSession = Depends(get_db)
):
    """view: all (abertas que posso ver) | queue (fila geral dos meus setores) | mine (assumidas por mim) | triage
    | dept:<id> (fila de um setor) | closed (encerradas)."""
    depts = await my_departments(s, user)
    q = (
        select(Conversation, Contact)
        .join(Contact, Contact.id == Conversation.contact_id)
        .where(Conversation.tenant_id == user.tenant_id)
        .order_by(Conversation.last_message_at.desc())
        .limit(200)
    )
    if view == "mine":
        q = q.where(Conversation.assigned_to == user.id, Conversation.status == "open")
    elif view.startswith("dept:"):
        dept_id = view[5:]
        if depts is not None and dept_id not in depts:
            raise HTTPException(403, "você não atende este setor")
        q = q.where(Conversation.department_id == dept_id, Conversation.status == "queue")
    elif view == "triage":  # a IA ainda está entendendo o assunto (sem setor)
        q = q.where(Conversation.department_id.is_(None), Conversation.status != "closed")
    elif view == "queue":
        q = _visible(q.where(Conversation.status == "queue"), user, depts)
    elif view == "closed":
        q = _visible(q.where(Conversation.status == "closed"), user, depts)
    else:
        q = _visible(q.where(Conversation.status != "closed"), user, depts)
    rows = (await s.execute(q)).all()
    leads = {
        l.contact_id: lead_out(l)
        for l in (await s.execute(select(Lead).where(Lead.tenant_id == user.tenant_id))).scalars().all()
    }
    return [conv_out(c, ct, leads.get(ct.id)) for c, ct in rows]


@router.get("/queues")
async def queues(user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    """Barra lateral: meus atendimentos e as filas dos setores que a pessoa atende (com contagem)."""
    depts = await my_departments(s, user)
    mine = (
        await s.execute(
            select(func.count()).select_from(Conversation).where(
                Conversation.tenant_id == user.tenant_id, Conversation.assigned_to == user.id, Conversation.status == "open"
            )
        )
    ).scalar_one()
    waiting = dict(
        (
            await s.execute(
                select(Conversation.department_id, func.count())
                .where(Conversation.tenant_id == user.tenant_id, Conversation.status == "queue")
                .group_by(Conversation.department_id)
            )
        ).all()
    )
    dq = select(Department).where(Department.tenant_id == user.tenant_id, Department.active.is_(True)).order_by(Department.position)
    if depts is not None:
        dq = dq.where(Department.id.in_(depts or {"-"}))
    sectors = [
        {"id": d.id, "name": d.name, "color": d.color, "waiting": waiting.get(d.id, 0), "owner_user_id": d.owner_user_id}
        for d in (await s.execute(dq)).scalars().all()
    ]
    return {"mine": mine, "triage": waiting.get(None, 0), "sectors": sectors, "is_admin": depts is None}


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
    if not can_take(c, user, await my_departments(s, user)):
        raise HTTPException(403, "esta conversa é de um setor que você não atende")
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
    return await conv_payload(s, c)


@router.post("/conversations/{conv_id}/transfer")
async def transfer(
    conv_id: str, body: TransferIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_db)
):
    """Transfere para uma pessoa, para a fila de outro setor, ou devolve à fila."""
    c = await _conv(s, user, conv_id)
    if user.role != "admin" and c.assigned_to != user.id:
        raise HTTPException(403, "só o responsável ou um admin pode transferir")
    values = {"version": Conversation.version + 1, "routed_by": "usuario"}
    if body.department_id:
        dept = await s.get(Department, body.department_id)
        if dept is None or dept.tenant_id != user.tenant_id or not dept.active:
            raise HTTPException(404, "setor não encontrado")
        values.update(department_id=dept.id, route_note=f"{dept.name}: transferido por {user.name}"[:300])
        if dept.owner_user_id and not body.user_id:  # setor com responsável direto
            values.update(assigned_to=dept.owner_user_id, status="open")
        else:
            values.update(assigned_to=None, status="queue")
    if body.user_id:
        target = await s.get(User, body.user_id)
        if target is None or target.tenant_id != user.tenant_id:
            raise HTTPException(404, "atendente não encontrado")
        values.update(assigned_to=target.id, status="open")
    elif not body.department_id:
        values.update(assigned_to=None, status="queue")
    await s.execute(
        update(Conversation).where(Conversation.id == conv_id, Conversation.tenant_id == user.tenant_id).values(**values)
    )
    await s.commit()
    await s.refresh(c)
    await _publish_conv(s, c)
    return await conv_payload(s, c)


@router.post("/conversations/{conv_id}/close")
async def close(conv_id: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    c = await _conv(s, user, conv_id)
    if user.role != "admin" and c.assigned_to != user.id:
        raise HTTPException(403, "só o responsável ou um admin pode encerrar")
    c.status = "closed"
    c.version += 1
    await s.commit()
    await _publish_conv(s, c)
    return await conv_payload(s, c)


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
