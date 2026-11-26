"""Assistente jurídico: decide, a cada mensagem do cliente, para onde a conversa vai.

- Encaminha na hora para o setor certo (ou direto ao responsável do setor, ex.: Pedro em precatórios).
- Explica dúvidas gerais em linguagem simples, sem parecer sobre o caso.
- Nos setores em que a análise exige consulta paga (ex.: aposentadoria), oferece horários livres da agenda.
- Na dúvida entre setores, pergunta com um menu numerado em vez de arriscar o setor errado.
- Para quando um atendente assume, quando o cliente pede uma pessoa ou depois de encaminhar.
"""
import json
import logging
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import agenda
from .ai import DeptInfo, get_ai
from .ai.knowledge import DISCLAIMER, norm
from .automation import bot_message, get_lead
from .models import Contact, Conversation, Department, DepartmentMember, Lead, Message, Tenant, User

log = logging.getLogger("assistant")
MIN_CONFIDENCE = 0.6


def _brl(cents: int | None) -> str:
    return "" if not cents else f" (R$ {cents / 100:,.2f})".replace(",", "X").replace(".", ",").replace("X", ".")


async def departments_of(s: AsyncSession, tenant_id: str) -> list[Department]:
    return (
        await s.execute(
            select(Department).where(Department.tenant_id == tenant_id, Department.active.is_(True)).order_by(Department.position)
        )
    ).scalars().all()


def dept_info(d: Department) -> DeptInfo:
    return DeptInfo(
        slug=d.slug, name=d.name, description=d.description or "",
        keywords=[k.strip() for k in (d.keywords or "").split(",") if k.strip()],
        needs_consult=bool(d.needs_consult), is_default=bool(d.is_default),
    )


def _menu(depts: list[Department]) -> tuple[str, list[str]]:
    ordered = [d for d in depts if not d.is_default] + [d for d in depts if d.is_default]
    lines = [f"{i}) {d.name if not d.is_default else 'Outro assunto'}" for i, d in enumerate(ordered, 1)]
    return "\n".join(lines), [d.slug for d in ordered]


async def _members(s: AsyncSession, dept: Department) -> list[User]:
    return (
        await s.execute(
            select(User).join(DepartmentMember, DepartmentMember.user_id == User.id).where(DepartmentMember.department_id == dept.id)
        )
    ).scalars().all()


def _who(u: User) -> str:
    return ((u.title or "") + " " + u.name).strip()


async def on_inbound(s: AsyncSession, conv: Conversation, contact: Contact, text: str) -> list[Message]:
    tenant = await s.get(Tenant, conv.tenant_id)
    if not tenant.automation_on or conv.status == "closed" or conv.assigned_to is not None or conv.ai_state == "done":
        return []
    depts = await departments_of(s, conv.tenant_id)
    if not depts:
        return []
    by_slug = {d.slug: d for d in depts}
    ctx = json.loads(conv.ai_context or "{}")
    low = norm(text)
    lead = await get_lead(s, conv.tenant_id, contact.id)
    if lead is None:
        lead = Lead(tenant_id=conv.tenant_id, contact_id=contact.id, stage="triagem")
        s.add(lead)
        await s.flush()
    replies: list[str] = []

    # 1) cliente escolhendo um horário de consulta
    if conv.ai_state == "awaiting_slot":
        slots = ctx.get("slots", [])
        m = re.match(r"^\s*(\d+)\b", low)
        dept = await s.get(Department, conv.department_id) if conv.department_id else None
        if m and 1 <= int(m.group(1)) <= len(slots):
            slot = slots[int(m.group(1)) - 1]
            lawyer = await s.get(User, slot["lawyer_id"])
            try:
                appt = await agenda.book(
                    s, conv.tenant_id, lawyer, datetime.fromisoformat(slot["start"]), datetime.fromisoformat(slot["end"]),
                    department_id=conv.department_id, contact=contact, conversation_id=conv.id,
                    price_cents=dept.consult_price_cents if dept else None, note=lead.summary,
                )
                replies.append(
                    f"Pronto, consulta marcada para {agenda.label(appt.starts_at)} com {_who(lawyer)}. "
                    "Vou deixar aqui o lembrete. Se precisar remarcar, é só me avisar por aqui."
                )
                lead.stage = "consulta marcada"
                conv.ai_state = "done"
            except agenda.SlotTaken:
                fresh = await agenda.free_slots(s, await _members(s, dept)) if dept else []
                if fresh:
                    ctx["slots"] = fresh
                    replies.append("Esse horário acabou de ser ocupado. Estes ainda estão livres:\n" + _slots_text(fresh))
                else:
                    replies.append("Esse horário acabou de ser ocupado. A equipe vai te passar um novo horário por aqui.")
                    conv.ai_state = "done"
        elif low in ("0", "nao", "não") or low.startswith("0"):
            replies.append("Tudo bem! A nossa equipe continua com você por aqui em instantes.")
            conv.ai_state = "done"
        else:
            replies.append(f"Para confirmar, responda só com o número do horário (1 a {len(slots)}), ou 0 se preferir falar antes com a equipe.")
        return await _finish(s, conv, ctx, replies)

    # 2) cliente respondendo o menu de assuntos
    if conv.ai_state == "triage" and ctx.get("menu"):
        m = re.match(r"^\s*(\d+)\s*$", low)
        if m and 1 <= int(m.group(1)) <= len(ctx["menu"]):
            dept = by_slug.get(ctx["menu"][int(m.group(1)) - 1])
            if dept:
                await _route(s, conv, contact, lead, dept, "o cliente escolheu no menu", None, replies)
                return await _finish(s, conv, ctx, replies)

    # 3) IA decide
    history = [
        {"direction": m.direction, "body": m.body}
        for m in (
            await s.execute(select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at))
        ).scalars().all()
    ][:-1]  # sem a mensagem atual
    decision = await get_ai().decide(text, history, [dept_info(d) for d in depts], tenant.name)
    if decision.summary:
        lead.summary = decision.summary if not lead.summary else (lead.summary + " | " + decision.summary)[-600:]
    target = by_slug.get(decision.department) if decision.department else None
    default = next((d for d in depts if d.is_default), depts[0])

    if decision.intent == "humano":
        replies.append("Claro! Já estou chamando alguém da nossa equipe para falar com você.")
        await _route(s, conv, contact, lead, target or default, "o cliente pediu para falar com uma pessoa", decision, replies, silent=True)
    elif target and decision.confidence >= MIN_CONFIDENCE:
        await _route(s, conv, contact, lead, target, f"IA ({decision.source}): {decision.confidence:.0%} de confiança", decision, replies)
    else:
        menu, slugs = _menu(depts)
        if decision.reply:
            replies.append(decision.reply)
        elif decision.explanation:
            replies.append(decision.explanation + "\n\n" + DISCLAIMER)
        elif decision.intent == "saudacao" or conv.ai_state is None:
            replies.append(tenant.greeting or f"Olá! Aqui é o assistente do {tenant.name}. Vou te encaminhar para a pessoa certa.")
        replies.append("Sobre qual assunto você quer falar?\n" + menu + "\nResponda só com o número, ou me conta com as suas palavras.")
        conv.ai_state = "triage"
        ctx["menu"] = slugs
    return await _finish(s, conv, ctx, replies)


def _slots_text(slots: list[dict]) -> str:
    return "\n".join(f"{i}) {sl['label']} com {sl['lawyer_name']}" for i, sl in enumerate(slots, 1))


async def _route(s, conv, contact, lead, dept, reason, decision, replies, silent=False):
    conv.department_id = dept.id
    conv.routed_by = "ia"
    conv.route_note = f"{dept.name}: {reason}"[:300]
    conv.ai_state = "done"
    lead.stage = "encaminhado"
    if decision and not silent:
        if decision.reply and decision.intent in ("duvida", "caso", "agendar"):
            replies.append(decision.reply)
        elif decision.explanation:
            replies.append(decision.explanation + "\n\n" + DISCLAIMER)
    if dept.owner_user_id:  # responsável direto: a conversa vai para a fila pessoal dele
        owner = await s.get(User, dept.owner_user_id)
        conv.assigned_to, conv.status, conv.version = owner.id, "open", conv.version + 1
        if not silent:
            replies.append(f"Vou te passar agora para {_who(owner)}, que cuida de {dept.name.lower()} aqui no escritório e continua a conversa com você por aqui mesmo.")
        return
    wants_case = decision is not None and decision.intent in ("caso", "agendar")
    if dept.needs_consult and wants_case and not silent:
        slots = await agenda.free_slots(s, await _members(s, dept))
        if slots:
            conv.ai_state = "awaiting_slot"
            ctx = json.loads(conv.ai_context or "{}")
            ctx["slots"] = slots
            conv.ai_context = json.dumps(ctx)
            replies.append(
                f"Para analisar o seu caso com cuidado, fazemos uma consulta com um advogado de {dept.name}{_brl(dept.consult_price_cents)}. "
                "Tenho estes horários livres:\n" + _slots_text(slots) + "\nResponda com o número do horário, ou 0 se preferir falar antes com a equipe."
            )
            return
    if not silent:
        replies.append(f"Já te encaminhei para a equipe de {dept.name}. Em instantes alguém continua a conversa por aqui.")


async def _finish(s, conv, ctx, replies) -> list[Message]:
    saved = json.loads(conv.ai_context or "{}")
    saved.update(ctx)
    conv.ai_context = json.dumps(saved)
    msgs = [bot_message(conv, r) for r in replies if r]
    for m in msgs:
        s.add(m)
    await s.commit()
    return msgs
