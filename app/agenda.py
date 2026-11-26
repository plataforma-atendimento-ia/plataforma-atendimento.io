"""Horários livres dos advogados e marcação de consultas.

Livre = dentro do horário de trabalho do advogado, sem consulta marcada no sistema e, se ele conectou o
Google Agenda, sem compromisso na agenda dele. Horários guardados em UTC; exibidos no horário de Brasília."""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import google_calendar as gcal
from .config import settings
from .db import utcnow
from .models import Appointment, GoogleCalendarLink, User

log = logging.getLogger("agenda")
TZ = timezone(timedelta(hours=settings.office_utc_offset_hours))
DEFAULT_HOURS = {str(d): ["09:00", "18:00"] for d in range(5)}  # segunda a sexta, 9h às 18h
WEEKDAYS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def hours_of(user: User) -> dict:
    try:
        h = json.loads(user.work_hours) if user.work_hours else None
    except ValueError:
        h = None
    return h if isinstance(h, dict) else DEFAULT_HOURS


def to_local(dt_utc: datetime) -> datetime:
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(TZ)


def to_utc(dt_local: datetime) -> datetime:
    return dt_local.astimezone(timezone.utc).replace(tzinfo=None)


def label(dt_utc: datetime) -> str:
    d = to_local(dt_utc)
    return f"{WEEKDAYS[d.weekday()]}, {d:%d/%m} às {d:%H:%M}"


async def busy_of(s: AsyncSession, lawyer: User, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    rows = (
        await s.execute(
            select(Appointment).where(
                Appointment.lawyer_id == lawyer.id,
                Appointment.status != "cancelada",
                Appointment.starts_at < end,
                Appointment.ends_at > start,
            )
        )
    ).scalars().all()
    busy = [(a.starts_at, a.ends_at) for a in rows]
    link = await s.get(GoogleCalendarLink, lawyer.id)
    if link and gcal.enabled():
        try:
            busy += await gcal.freebusy(link.refresh_token, link.calendar_id, start, end)
        except Exception as e:  # Google fora do ar: segue só com a agenda do sistema, mas registra
            log.warning("agenda Google de %s indisponível: %s", lawyer.id, e)
    return busy


def _overlaps(a0, a1, busy) -> bool:
    return any(a0 < b1 and a1 > b0 for b0, b1 in busy)


async def free_slots(
    s: AsyncSession, lawyers: list[User], n: int = 3, days: int = 14, now: datetime | None = None, notice_hours: int = 2
) -> list[dict]:
    """Os `n` próximos horários livres entre os advogados indicados (um por horário de início)."""
    now = now or utcnow()
    start = now + timedelta(hours=notice_hours)
    end = now + timedelta(days=days)
    candidates = []
    for lw in lawyers:
        busy = await busy_of(s, lw, now, end)
        hours = hours_of(lw)
        step = timedelta(minutes=lw.slot_minutes or 60)
        day = to_local(now).date()
        for i in range(days + 1):
            d = day + timedelta(days=i)
            span = hours.get(str(d.weekday()))
            if not span:
                continue
            h0, h1 = [datetime.strptime(x, "%H:%M").time() for x in span]
            t = datetime.combine(d, h0, tzinfo=TZ)
            stop = datetime.combine(d, h1, tzinfo=TZ)
            while t + step <= stop:
                s0, s1 = to_utc(t), to_utc(t + step)
                if s0 >= start and s1 <= end and not _overlaps(s0, s1, busy):
                    candidates.append({"lawyer_id": lw.id, "start": s0, "end": s1})
                t += step
    candidates.sort(key=lambda c: (c["start"], c["lawyer_id"]))
    picked, seen = [], set()
    for c in candidates:
        if c["start"] in seen:
            continue
        seen.add(c["start"])
        picked.append(c)
        if len(picked) == n:
            break
    names = {lw.id: lw for lw in lawyers}
    return [
        {
            "lawyer_id": c["lawyer_id"],
            "lawyer_name": ((names[c["lawyer_id"]].title or "") + " " + names[c["lawyer_id"]].name).strip(),
            "start": c["start"].isoformat(),
            "end": c["end"].isoformat(),
            "label": label(c["start"]),
        }
        for c in picked
    ]


class SlotTaken(Exception):
    pass


async def book(
    s: AsyncSession, tenant_id: str, lawyer: User, start: datetime, end: datetime, *, department_id=None,
    contact=None, conversation_id=None, price_cents=None, note=None, created_by="ia",
) -> Appointment:
    """Marca a consulta conferindo de novo se o horário continua livre (outro cliente pode ter pego)."""
    if _overlaps(start, end, await busy_of(s, lawyer, start - timedelta(minutes=1), end + timedelta(minutes=1))):
        raise SlotTaken("este horário acabou de ser ocupado")
    appt = Appointment(
        tenant_id=tenant_id, lawyer_id=lawyer.id, department_id=department_id, contact_id=contact.id if contact else None,
        conversation_id=conversation_id, starts_at=start, ends_at=end, price_cents=price_cents, note=note, created_by=created_by,
    )
    s.add(appt)
    await s.flush()
    link = await s.get(GoogleCalendarLink, lawyer.id)
    if link and gcal.enabled():
        who = (contact.name or contact.phone) if contact else "cliente"
        try:
            appt.google_event_id = await gcal.create_event(
                link.refresh_token, link.calendar_id, f"Consulta: {who}",
                f"Consulta marcada pelo atendimento.\nCliente: {who}\nTelefone: {contact.phone if contact else '-'}\n{note or ''}",
                start, end,
            )
        except Exception as e:
            log.warning("consulta marcada, mas não foi para o Google Agenda: %s", e)
    return appt
