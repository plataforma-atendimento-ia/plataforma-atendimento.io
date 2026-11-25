"""Roteiro automático de qualificação de leads (por regras, SEM IA).

Quando um contato novo escreve e ninguém assumiu a conversa, o sistema faz quatro perguntas
(nome, interesse, urgência, faixa de investimento), classifica o lead (quente, morno, frio) e
avisa que um atendente vai assumir. Se o cliente pedir uma pessoa, ou se um atendente assumir
a conversa, a automação para na hora. Na Fase 3 este roteiro vira uma conversa conduzida por IA."""
import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import utcnow
from .models import Contact, Conversation, Lead, Message, Tenant, uid

# palavra inteira, já sem acentos: "atendente" aciona, mas "atendentes" (plural, ex.: "5 atendentes") não
HUMAN_RE = re.compile(r"\b(atendente|humano|falar com alguem|falar com uma pessoa)\b")

URGENCY = {"1": "hoje", "2": "semana", "3": "sem_pressa"}
URGENCY_WORDS = {"hoje": ("hoje", "urgente", "agora", "ja"), "semana": ("semana",), "sem_pressa": ("pressa", "depois", "mes", "futuro")}
BUDGET = {"1": "ate_500", "2": "500_2000", "3": "acima_2000"}
POINTS_U = {"hoje": 3, "semana": 2, "sem_pressa": 1}
POINTS_B = {"acima_2000": 3, "500_2000": 2, "ate_500": 1}

Q_URGENCY = "Para quando você precisa?\n1) Hoje\n2) Esta semana\n3) Sem pressa"
Q_BUDGET = "Qual faixa de investimento faz sentido para você?\n1) Até R$ 500\n2) De R$ 500 a R$ 2.000\n3) Acima de R$ 2.000"


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in t if not unicodedata.combining(ch)).strip()


def _pick(low: str, digits: dict[str, str], words: dict[str, tuple] | None = None) -> str | None:
    m = re.match(r"^\s*([1-9])\b", low)
    if m and m.group(1) in digits:
        return digits[m.group(1)]
    for value, ws in (words or {}).items():
        if any(w in low for w in ws):
            return value
    return None


def score_of(urgency: str, budget: str) -> str:
    pts = POINTS_U.get(urgency, 1) + POINTS_B.get(budget, 1)
    return "quente" if pts >= 5 else "morno" if pts >= 3 else "frio"


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
    }


async def get_lead(s: AsyncSession, tenant_id: str, contact_id: str) -> Lead | None:
    return (
        await s.execute(select(Lead).where(Lead.tenant_id == tenant_id, Lead.contact_id == contact_id))
    ).scalar_one_or_none()


def _bot_message(conv: Conversation, body: str) -> Message:
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


async def on_inbound(s: AsyncSession, conv: Conversation, contact: Contact, text: str) -> list[Message]:
    """Decide e grava a resposta automática (se houver). Devolve as mensagens criadas (já confirmadas)."""
    tenant = await s.get(Tenant, conv.tenant_id)
    if not tenant.automation_on or conv.assigned_to is not None or conv.status == "closed":
        return []
    lead = await get_lead(s, conv.tenant_id, contact.id)
    if lead is None:
        lead = Lead(tenant_id=conv.tenant_id, contact_id=contact.id)
        s.add(lead)
        await s.flush()
    if lead.qual_step == "done":
        return []

    low = _norm(text)
    reply: str | None = None

    if HUMAN_RE.search(low):
        lead.qual_step, lead.stage = "done", ("qualificado" if lead.score else "atendimento")
        reply = "Claro! Já estou chamando um atendente para falar com você."
    elif lead.qual_step is None:
        lead.qual_step, lead.stage = "name", "qualificando"
        reply = f"Olá! Sou o assistente da {tenant.name}. Para te atender melhor, qual é o seu nome?"
    elif lead.qual_step == "name":
        lead.name = re.sub(r"\s+", " ", text).strip()[:80] or None
        first = (lead.name or "").split(" ")[0]
        if lead.name and not contact.name:
            contact.name = lead.name
        lead.qual_step = "interest"
        reply = f"Prazer, {first}! O que você está procurando hoje?"
    elif lead.qual_step == "interest":
        lead.interest = re.sub(r"\s+", " ", text).strip()[:300]
        lead.qual_step = "urgency"
        reply = Q_URGENCY
    elif lead.qual_step == "urgency":
        value = _pick(low, URGENCY, URGENCY_WORDS)
        if value is None:
            reply = "Não entendi. Responda 1, 2 ou 3.\n" + Q_URGENCY
        else:
            lead.urgency, lead.qual_step = value, "budget"
            reply = Q_BUDGET
    elif lead.qual_step == "budget":
        value = _pick(low, BUDGET)
        if value is None:
            reply = "Não entendi. Responda 1, 2 ou 3.\n" + Q_BUDGET
        else:
            lead.budget, lead.qual_step = value, "done"
            lead.score = score_of(lead.urgency or "sem_pressa", value)
            lead.stage = "qualificado"
            first = (lead.name or "").split(" ")[0]
            reply = f"Obrigado, {first}! Anotei tudo e já chamei um atendente. Você está na fila."

    if reply is None:
        return []
    msg = _bot_message(conv, reply)
    s.add(msg)
    await s.commit()
    return [msg]
