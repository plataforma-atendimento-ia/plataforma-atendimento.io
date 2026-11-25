"""Worker da outbox: envia mensagens `queued` com retry exponencial e nunca perde mensagem."""
import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select, update

from .channels import provider_for
from .config import settings
from .db import SessionLocal, utcnow
from .models import Channel, Contact, Conversation, Message
from .realtime import hub
from .services import msg_out

log = logging.getLogger("outbox")


def backoff_for(attempts: int) -> timedelta:
    steps = settings.outbox_backoff_seconds
    return timedelta(seconds=steps[min(max(attempts - 1, 0), len(steps) - 1)])


async def reconcile_stuck() -> None:
    """Mensagens presas em `sending` (worker caiu) voltam para a fila."""
    limit = utcnow() - timedelta(seconds=settings.sending_timeout_seconds)
    async with SessionLocal() as s:
        await s.execute(
            update(Message)
            .where(Message.status == "sending", Message.updated_at < limit)
            .values(status="queued", next_attempt_at=utcnow())
        )
        await s.commit()


async def process_batch(test_provider=None, limit: int = 20) -> int:
    """Processa uma rodada. Devolve quantas mensagens foram tentadas.
    O provedor é escolhido por canal (teste = simulado, real = Meta). `test_provider` permite
    substituir o simulado em testes."""
    await reconcile_stuck()
    now = utcnow()
    async with SessionLocal() as s:
        ids = (
            await s.execute(
                select(Message.id)
                .where(
                    Message.direction == "out",
                    Message.status == "queued",
                    Message.next_attempt_at <= now,
                )
                .order_by(Message.created_at)
                .limit(limit)
            )
        ).scalars().all()

    tried = 0
    for mid in ids:
        async with SessionLocal() as s:
            # reivindicação atômica: dois workers nunca enviam a mesma mensagem
            res = await s.execute(
                update(Message)
                .where(Message.id == mid, Message.status == "queued")
                .values(status="sending", updated_at=utcnow())
            )
            await s.commit()
            if res.rowcount == 0:
                continue
            tried += 1
            msg = await s.get(Message, mid)
            conv = await s.get(Conversation, msg.conversation_id)
            contact = await s.get(Contact, conv.contact_id)
            channel = await s.get(Channel, conv.channel_id)
            chosen = (test_provider or provider_for(channel)) if channel.is_test else provider_for(channel)
            try:
                to = contact.wa_jid or contact.phone  # ponte QR responde ao endereço exato recebido
                wamid = await chosen.send_text(channel, to, msg.body, msg.idempotency_key)
                msg.status, msg.wamid, msg.error = "sent", wamid, None
                msg.attempts += 1
            except Exception as e:  # ChannelError ou qualquer erro inesperado
                msg.attempts += 1
                msg.error = str(e)[:500]
                retryable = getattr(e, "retryable", True)
                if retryable and msg.attempts < settings.outbox_max_attempts:
                    msg.status = "queued"
                    msg.next_attempt_at = utcnow() + backoff_for(msg.attempts)
                else:
                    msg.status = "failed"
                log.warning("envio falhou msg=%s tentativa=%s: %s", mid, msg.attempts, e)
            await s.commit()
            event = {"type": "message.status", "message": msg_out(msg)}
            tenant_id = msg.tenant_id
        await hub.publish(tenant_id, event)
    return tried


async def run_worker() -> None:
    while True:
        try:
            await process_batch()
        except Exception:
            log.exception("erro no worker da outbox")
        await asyncio.sleep(settings.outbox_poll_seconds)
