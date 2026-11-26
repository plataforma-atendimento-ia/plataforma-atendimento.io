"""Ficha do lead e mensagens automáticas (usadas pelo assistente jurídico em app/assistant.py)."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import utcnow
from .models import Conversation, Lead, Message, uid


def lead_out(l: Lead | None) -> dict | None:
    if l is None:
        return None
    return {
        "name": l.name,
        "interest": l.interest,
        "urgency": l.urgency,
        "budget": l.budget,
        "score": l.score,
        "stage": l.stage,
        "step": l.qual_step,
        "summary": l.summary,
    }


async def get_lead(s: AsyncSession, tenant_id: str, contact_id: str) -> Lead | None:
    return (
        await s.execute(select(Lead).where(Lead.tenant_id == tenant_id, Lead.contact_id == contact_id))
    ).scalar_one_or_none()


def bot_message(conv: Conversation, body: str) -> Message:
    conv.last_message_at = utcnow()
    conv.last_preview = body[:200]
    return Message(
        tenant_id=conv.tenant_id,
        conversation_id=conv.id,
        direction="out",
        body=body,
        status="queued",
        sender_user_id=None,  # nulo = enviada pela automação
        idempotency_key=uid(),
        next_attempt_at=utcnow(),
    )
