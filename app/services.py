import json

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


KIND_LABEL = {"image": "📷 Foto", "video": "🎬 Vídeo", "audio": "🎤 Áudio", "document": "📄 Documento"}


def preview_of(m: Message) -> str:
    if m.deleted_at:
        return "Mensagem apagada"
    if (m.kind or "text") == "text":
        return (m.body or "")[:200]
    label = "🎤 Mensagem de voz" if m.voice else KIND_LABEL.get(m.kind, "Arquivo")
    return (label + (": " + m.body if m.body else ""))[:200]


def msg_out(m: Message) -> dict:
    deleted = m.deleted_at is not None
    try:
        reactions = json.loads(m.reactions) if m.reactions else {}
    except ValueError:
        reactions = {}
    return {
        "id": m.id,
        "conversation_id": m.conversation_id,
        "direction": m.direction,
        "body": "" if deleted else m.body,
        "kind": "text" if deleted else (m.kind or "text"),
        "media_url": f"/media/{m.id}" if (m.media_path and not deleted) else None,
        "media_mime": m.media_mime if not deleted else None,
        "media_name": m.media_name if not deleted else None,
        "media_size": m.media_size if not deleted else None,
        "voice": bool(m.voice),
        "reply_to_id": m.reply_to_id,
        "edited": m.edited_at is not None,
        "deleted": deleted,
        "reactions": reactions,
        "remote_note": m.remote_note,
        "status": m.status,
        "error": m.error,
        "attempts": m.attempts,
        "sender_user_id": m.sender_user_id,
        "created_at": m.created_at.isoformat(),
    }


def conv_out(c: Conversation, contact: Contact, lead: dict | None = None) -> dict:
    return {
        "lead": lead,
        "department_id": c.department_id,
        "routed_by": c.routed_by,
        "route_note": c.route_note,
        "ai_state": c.ai_state,
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
    s: AsyncSession, channel: Channel, phone: str, name: str | None, text: str, wamid: str, wa_jid: str | None = None,
    media: dict | None = None, reply_wamid: str | None = None,
) -> tuple[Conversation, Message] | None:
    """media: {"path", "mime", "name", "size", "kind", "voice"} já gravado em MEDIA_DIR."""
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
        contact = Contact(tenant_id=channel.tenant_id, phone=phone, name=name, wa_jid=wa_jid)
        s.add(contact)
        await s.flush()
    elif name and not contact.name:
        contact.name = name
    if wa_jid:
        contact.wa_jid = wa_jid

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
    if media:
        msg.kind, msg.media_path, msg.media_mime = media["kind"], media["path"], media["mime"]
        msg.media_name, msg.media_size, msg.voice = media.get("name"), media.get("size"), bool(media.get("voice"))
    if reply_wamid:
        quoted = (
            await s.execute(select(Message.id).where(Message.conversation_id == conv.id, Message.wamid == reply_wamid))
        ).scalar_one_or_none()
        msg.reply_to_id = quoted
    s.add(msg)
    conv.last_message_at = utcnow()
    conv.last_preview = preview_of(msg)
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
