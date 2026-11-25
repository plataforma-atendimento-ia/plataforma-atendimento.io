from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .db import utcnow
from .models import Channel, Contact, Conversation, InboundEvent, Message, uid

STATUS_RANK = {"queued": 0, "sending": 1, "sent": 2, "delivered": 3, "read": 4}


async def conv_payload(s: AsyncSession, c: Conversation) -> dict:
    """Conversa pronta para enviar à tela, com contato e ficha do lead."""
    from .automation import get_lead, lead_out

    contact = await s.get(Contact, c.contact_id)
    return conv_out(c, contact, lead_out(await get_lead(s, c.tenant_id, c.contact_id)))


def msg_out(m: Message) -> dict:
    return {
        "id": m.id,
        "conversation_id": m.conversation_id,
        "direction": m.direction,
        "body": m.body,
        "status": m.status,
        "error": m.error,
        "attempts": m.attempts,
        "sender_user_id": m.sender_user_id,
        "created_at": m.created_at.isoformat(),
    }


def conv_out(c: Conversation, contact: Contact, lead: dict | None = None) -> dict:
    return {
        "lead": lead,
        "id": c.id,
        "status": c.status,
        "assigned_to": c.assigned_to,
        "version": c.version,
        "contact": {"id": contact.id, "name": contact.name, "phone": contact.phone},
        "last_preview": c.last_preview,
        "last_message_at": c.last_message_at.isoformat(),
        "waiting_since": c.created_at.isoformat() if c.status == "queue" else None,
    }


async def handle_inbound(
    s: AsyncSession, channel: Channel, phone: str, name: str | None, text: str, wamid: str
) -> tuple[Conversation, Message] | None:
    """Registra mensagem recebida. Idempotente por wamid. Devolve None se for duplicada."""
    s.add(InboundEvent(tenant_id=channel.tenant_id, wamid=wamid))
    try:
        await s.flush()
    except IntegrityError:
        await s.rollback()
        return None

    contact = (
        await s.execute(
            select(Contact).where(Contact.tenant_id == channel.tenant_id, Contact.phone == phone)
        )
    ).scalar_one_or_none()
    if contact is None:
        contact = Contact(tenant_id=channel.tenant_id, phone=phone, name=name)
        s.add(contact)
        await s.flush()
    elif name and not contact.name:
        contact.name = name

    conv = (
        await s.execute(
            select(Conversation).where(
                Conversation.tenant_id == channel.tenant_id,
                Conversation.contact_id == contact.id,
                Conversation.status != "closed",
            )
        )
    ).scalar_one_or_none()
    if conv is None:
        conv = Conversation(tenant_id=channel.tenant_id, contact_id=contact.id, channel_id=channel.id)
        s.add(conv)
        await s.flush()

    msg = Message(
        tenant_id=channel.tenant_id,
        conversation_id=conv.id,
        direction="in",
        body=text,
        status="received",
        wamid=wamid,
        idempotency_key=wamid,
    )
    s.add(msg)
    conv.last_message_at = utcnow()
    conv.last_preview = text[:200]
    await s.commit()
    return conv, msg


async def claim_conversation(s: AsyncSession, tenant_id: str, conv_id: str, user_id: str) -> bool:
    """Assumir é um UPDATE condicional atômico: só um atendente vence."""
    res = await s.execute(
        update(Conversation)
        .where(
            Conversation.id == conv_id,
            Conversation.tenant_id == tenant_id,
            Conversation.assigned_to.is_(None),
            Conversation.status != "closed",
        )
        .values(assigned_to=user_id, status="open", version=Conversation.version + 1)
    )
    await s.commit()
    return res.rowcount == 1


def new_idempotency_key() -> str:
    return uid()
