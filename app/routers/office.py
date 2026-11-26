"""Escritório: setores, equipe, agenda de consultas e Google Agenda."""
import json
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import agenda
from .. import google_calendar as gcal
from ..ai.knowledge import DEFAULT_DEPARTMENTS, norm
from ..config import settings
from ..db import utcnow
from ..deps import admin_only, current_user, get_db
from ..models import Appointment, Contact, Conversation, Department, DepartmentMember, GoogleCalendarLink, User
from ..security import decode_token, hash_password, make_token

router = APIRouter(tags=["office"])
HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


# ---------- setores ----------
async def ensure_default_departments(s: AsyncSession, tenant_id: str) -> None:
    have = (await s.execute(select(func.count()).select_from(Department).where(Department.tenant_id == tenant_id))).scalar_one()
    if have:
        return
    for i, d in enumerate(DEFAULT_DEPARTMENTS):
        s.add(Department(tenant_id=tenant_id, position=i, **d))
    await s.flush()


async def members_map(s: AsyncSession, tenant_id: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in (await s.execute(select(DepartmentMember).where(DepartmentMember.tenant_id == tenant_id))).scalars().all():
        out.setdefault(m.department_id, []).append(m.user_id)
    return out


def dept_out(d: Department, members: list[str]) -> dict:
    return {
        "id": d.id, "name": d.name, "slug": d.slug, "color": d.color, "position": d.position,
        "description": d.description, "keywords": d.keywords, "owner_user_id": d.owner_user_id,
        "needs_consult": d.needs_consult, "consult_price_cents": d.consult_price_cents,
        "is_default": d.is_default, "member_ids": members,
    }


class DeptIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    color: str = Field(default="#2b59c3", pattern=r"^#[0-9a-fA-F]{6}$")
    description: str = Field(default="", max_length=600)
    keywords: str = Field(default="", max_length=2000)
    owner_user_id: str | None = None
    needs_consult: bool = False
    consult_price_cents: int | None = Field(default=None, ge=0, le=10_000_000)
    member_ids: list[str] = Field(default_factory=list)
    position: int | None = None


async def _check_users(s: AsyncSession, tenant_id: str, ids: list[str]) -> None:
    if not ids:
        return
    found = (await s.execute(select(User.id).where(User.tenant_id == tenant_id, User.id.in_(ids)))).scalars().all()
    if set(found) != set(ids):
        raise HTTPException(422, "há pessoas que não são deste escritório")


async def _apply(s: AsyncSession, d: Department, body: DeptIn, tenant_id: str) -> None:
    members = list(dict.fromkeys(body.member_ids + ([body.owner_user_id] if body.owner_user_id else [])))
    await _check_users(s, tenant_id, members)
    d.name, d.color, d.description, d.keywords = body.name, body.color, body.description, body.keywords
    d.owner_user_id, d.needs_consult, d.consult_price_cents = body.owner_user_id, body.needs_consult, body.consult_price_cents
    if body.position is not None:
        d.position = body.position
    await s.execute(delete(DepartmentMember).where(DepartmentMember.department_id == d.id))
    for uid_ in members:
        s.add(DepartmentMember(department_id=d.id, user_id=uid_, tenant_id=tenant_id))


@router.get("/departments")
async def list_departments(user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    mm = await members_map(s, user.tenant_id)
    rows = (
        await s.execute(
            select(Department).where(Department.tenant_id == user.tenant_id, Department.active.is_(True)).order_by(Department.position)
        )
    ).scalars().all()
    return [dept_out(d, mm.get(d.id, [])) for d in rows]


@router.post("/departments", status_code=201)
async def create_department(body: DeptIn, admin: User = Depends(admin_only), s: AsyncSession = Depends(get_db)):
    slug = re.sub(r"[^a-z0-9]+", "-", norm(body.name)).strip("-")[:36] or "setor"
    taken = set((await s.execute(select(Department.slug).where(Department.tenant_id == admin.tenant_id))).scalars().all())
    base, n = slug, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    count = (await s.execute(select(func.count()).select_from(Department).where(Department.tenant_id == admin.tenant_id))).scalar_one()
    d = Department(tenant_id=admin.tenant_id, slug=slug, name=body.name, position=count)
    s.add(d)
    await s.flush()
    await _apply(s, d, body, admin.tenant_id)
    await s.commit()
    return dept_out(d, (await members_map(s, admin.tenant_id)).get(d.id, []))


async def _dept(s: AsyncSession, admin: User, dept_id: str) -> Department:
    d = (await s.execute(select(Department).where(Department.id == dept_id, Department.tenant_id == admin.tenant_id))).scalar_one_or_none()
    if d is None:
        raise HTTPException(404, "setor não encontrado")
    return d


@router.put("/departments/{dept_id}")
async def update_department(dept_id: str, body: DeptIn, admin: User = Depends(admin_only), s: AsyncSession = Depends(get_db)):
    d = await _dept(s, admin, dept_id)
    await _apply(s, d, body, admin.tenant_id)
    await s.commit()
    return dept_out(d, (await members_map(s, admin.tenant_id)).get(d.id, []))


@router.delete("/departments/{dept_id}")
async def archive_department(dept_id: str, admin: User = Depends(admin_only), s: AsyncSession = Depends(get_db)):
    d = await _dept(s, admin, dept_id)
    if d.is_default:
        raise HTTPException(409, "o atendimento geral não pode ser removido")
    d.active = False  # arquivado: o histórico das conversas continua
    await s.commit()
    return {"ok": True}


# ---------- equipe ----------
class UserPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    title: str | None = Field(default=None, max_length=20)
    position: str | None = Field(default=None, max_length=80)
    role: str | None = Field(default=None, pattern="^(admin|agent)$")
    work_hours: dict[str, list[str]] | None = None
    slot_minutes: int | None = Field(default=None, ge=15, le=240)
    max_concurrent: int | None = Field(default=None, ge=1, le=100)
    department_ids: list[str] | None = None


async def team_out(s: AsyncSession, tenant_id: str) -> list[dict]:
    users = (await s.execute(select(User).where(User.tenant_id == tenant_id).order_by(User.name))).scalars().all()
    mm = await members_map(s, tenant_id)
    by_user: dict[str, list[str]] = {}
    for dept_id, uids in mm.items():
        for u in uids:
            by_user.setdefault(u, []).append(dept_id)
    linked = set((await s.execute(select(GoogleCalendarLink.user_id).where(GoogleCalendarLink.tenant_id == tenant_id))).scalars().all())
    return [
        {
            "id": u.id, "name": u.name, "email": u.email, "role": u.role, "title": u.title, "position": u.position,
            "work_hours": agenda.hours_of(u), "slot_minutes": u.slot_minutes, "max_concurrent": u.max_concurrent,
            "department_ids": by_user.get(u.id, []), "google_connected": u.id in linked,
        }
        for u in users
    ]


@router.get("/team")
async def team(user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    return await team_out(s, user.tenant_id)


@router.put("/users/{user_id}")
async def update_user(user_id: str, body: UserPatch, me: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    u = await s.get(User, user_id)
    if u is None or u.tenant_id != me.tenant_id:
        raise HTTPException(404, "pessoa não encontrada")
    is_self_only = me.role != "admin"
    if is_self_only and (u.id != me.id or body.role is not None or body.department_ids is not None or body.max_concurrent is not None):
        raise HTTPException(403, "você só pode alterar os seus dados e horários")
    if body.work_hours is not None:
        for day, span in body.work_hours.items():
            if day not in {str(i) for i in range(7)} or len(span) != 2 or not all(re.match(HHMM, x) for x in span) or span[0] >= span[1]:
                raise HTTPException(422, "horário inválido: use {'0': ['09:00','18:00']} com 0 = segunda")
        u.work_hours = json.dumps(body.work_hours)
    for field in ("name", "title", "position", "role", "slot_minutes", "max_concurrent"):
        v = getattr(body, field)
        if v is not None:
            setattr(u, field, v)
    if body.department_ids is not None:
        valid = set((await s.execute(select(Department.id).where(Department.tenant_id == me.tenant_id))).scalars().all())
        if not set(body.department_ids) <= valid:
            raise HTTPException(422, "setor inválido")
        await s.execute(delete(DepartmentMember).where(DepartmentMember.user_id == u.id))
        for d in body.department_ids:
            s.add(DepartmentMember(department_id=d, user_id=u.id, tenant_id=me.tenant_id))
    await s.commit()
    return next(x for x in await team_out(s, me.tenant_id) if x["id"] == u.id)


# ---------- agenda ----------
def appt_out(a: Appointment, lawyer: User | None, contact: Contact | None, dept: Department | None) -> dict:
    return {
        "id": a.id, "starts_at": a.starts_at.isoformat(), "ends_at": a.ends_at.isoformat(), "label": agenda.label(a.starts_at),
        "status": a.status, "lawyer_id": a.lawyer_id, "lawyer_name": ((lawyer.title or "") + " " + lawyer.name).strip() if lawyer else None,
        "contact": {"name": contact.name, "phone": contact.phone} if contact else None,
        "department": {"id": dept.id, "name": dept.name, "color": dept.color} if dept else None,
        "conversation_id": a.conversation_id, "price_cents": a.price_cents, "in_google": bool(a.google_event_id),
        "created_by": a.created_by, "note": a.note,
    }


@router.get("/agenda")
async def list_agenda(days: int = Query(14, ge=1, le=90), user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    start = utcnow() - timedelta(days=1)
    rows = (
        await s.execute(
            select(Appointment, User, Contact, Department)
            .join(User, User.id == Appointment.lawyer_id)
            .outerjoin(Contact, Contact.id == Appointment.contact_id)
            .outerjoin(Department, Department.id == Appointment.department_id)
            .where(Appointment.tenant_id == user.tenant_id, Appointment.starts_at >= start, Appointment.starts_at < start + timedelta(days=days + 1))
            .order_by(Appointment.starts_at)
        )
    ).all()
    return [appt_out(a, lw, ct, d) for a, lw, ct, d in rows]


@router.get("/lawyers/{lawyer_id}/slots")
async def lawyer_slots(lawyer_id: str, n: int = Query(6, ge=1, le=20), user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    lw = await s.get(User, lawyer_id)
    if lw is None or lw.tenant_id != user.tenant_id:
        raise HTTPException(404, "pessoa não encontrada")
    return await agenda.free_slots(s, [lw], n=n)


class ApptIn(BaseModel):
    lawyer_id: str
    start: datetime  # UTC
    conversation_id: str | None = None
    note: str | None = Field(default=None, max_length=1000)


@router.post("/appointments", status_code=201)
async def create_appointment(body: ApptIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    lw = await s.get(User, body.lawyer_id)
    if lw is None or lw.tenant_id != user.tenant_id:
        raise HTTPException(404, "advogado não encontrado")
    conv = contact = None
    if body.conversation_id:
        conv = await s.get(Conversation, body.conversation_id)
        if conv is None or conv.tenant_id != user.tenant_id:
            raise HTTPException(404, "conversa não encontrada")
        contact = await s.get(Contact, conv.contact_id)
    start = body.start.replace(tzinfo=None) if body.start.tzinfo is None else agenda.to_utc(body.start)
    end = start + timedelta(minutes=lw.slot_minutes or 60)
    dept = await s.get(Department, conv.department_id) if conv and conv.department_id else None
    try:
        a = await agenda.book(
            s, user.tenant_id, lw, start, end, department_id=dept.id if dept else None, contact=contact,
            conversation_id=conv.id if conv else None, price_cents=dept.consult_price_cents if dept else None,
            note=body.note, created_by="usuario",
        )
    except agenda.SlotTaken as e:
        raise HTTPException(409, str(e))
    await s.commit()
    return appt_out(a, lw, contact, dept)


class ApptPatch(BaseModel):
    status: str = Field(pattern="^(marcada|realizada|cancelada)$")


@router.patch("/appointments/{appt_id}")
async def update_appointment(appt_id: str, body: ApptPatch, user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    a = await s.get(Appointment, appt_id)
    if a is None or a.tenant_id != user.tenant_id:
        raise HTTPException(404, "consulta não encontrada")
    a.status = body.status
    await s.commit()
    return {"id": a.id, "status": a.status}


# ---------- Google Agenda ----------
@router.get("/google/status")
async def google_status(user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    link = await s.get(GoogleCalendarLink, user.id)
    return {"enabled": gcal.enabled(), "connected": bool(link), "email": link.email if link else None, "redirect_uri": gcal.redirect_uri()}


@router.get("/google/connect")
async def google_connect(user: User = Depends(current_user)):
    if not gcal.enabled():
        raise HTTPException(503, "Google Agenda ainda não configurado: defina GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET (docs/AGENDA_GOOGLE.md)")
    return {"url": gcal.auth_url(make_token(user.id, user.tenant_id))}


@router.get("/google/callback", include_in_schema=False)
async def google_callback(code: str = "", state: str = "", error: str = "", s: AsyncSession = Depends(get_db)):
    claims = decode_token(state)
    if error or not code or not claims:
        return RedirectResponse("/app/#agenda?google=erro")
    user = await s.get(User, claims["sub"])
    if user is None:
        return RedirectResponse("/app/#agenda?google=erro")
    try:
        tok = await gcal.exchange_code(code)
    except gcal.GoogleError:
        return RedirectResponse("/app/#agenda?google=erro")
    link = await s.get(GoogleCalendarLink, user.id)
    if link is None:
        link = GoogleCalendarLink(user_id=user.id, tenant_id=user.tenant_id, refresh_token=tok["refresh_token"])
        s.add(link)
    link.refresh_token, link.email, link.connected_at = tok["refresh_token"], tok.get("email"), utcnow()
    await s.commit()
    return RedirectResponse("/app/#agenda?google=ok")


@router.post("/google/disconnect")
async def google_disconnect(user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    link = await s.get(GoogleCalendarLink, user.id)
    if link:
        await s.delete(link)
        await s.commit()
    return {"connected": False}


# ---------- escritório de demonstração ----------
DEMO_PASSWORD = "demo-escritorio-2026"  # só em TEST_MODE; contas de demonstração locais
DEMO_TEAM = [
    ("Pedro Albuquerque", "Dr.", "Advogado de precatórios", ["precatorios"], "precatorios"),
    ("Ana Ribeiro", "Dra.", "Advogada previdenciária", ["previdenciario"], None),
    ("Carla Mendes", "Dra.", "Advogada trabalhista", ["trabalhista"], None),
    ("Júlia Santos", "Dra.", "Advogada de família", ["familia"], None),
    ("Bruno Lima", None, "Atendimento de ações comuns", ["civel", "geral"], None),
]


@router.post("/demo/law-firm")
async def demo_law_firm(admin: User = Depends(admin_only), s: AsyncSession = Depends(get_db)):
    """Cria uma equipe de exemplo (Pedro nos precatórios etc.). Só em modo de teste."""
    if not settings.test_mode:
        raise HTTPException(404)
    await ensure_default_departments(s, admin.tenant_id)
    depts = {d.slug: d for d in (await s.execute(select(Department).where(Department.tenant_id == admin.tenant_id))).scalars().all()}
    suffix = admin.tenant_id[:6]
    created = []
    for name, title, position, slugs, owner_of in DEMO_TEAM:
        email = f"{norm(name).split(' ')[0]}.{suffix}@demo.example.com"  # domínio reservado para exemplos
        u = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if u is None:
            u = User(tenant_id=admin.tenant_id, email=email, name=name, title=title, position=position,
                     role="agent", password_hash=hash_password(DEMO_PASSWORD))
            s.add(u)
            await s.flush()
        for sl in slugs:
            if sl in depts and not (await s.get(DepartmentMember, (depts[sl].id, u.id))):
                s.add(DepartmentMember(department_id=depts[sl].id, user_id=u.id, tenant_id=admin.tenant_id))
        if owner_of and owner_of in depts:
            depts[owner_of].owner_user_id = u.id
        created.append({"name": ((title or "") + " " + name).strip(), "email": email, "sectors": slugs})
    await s.commit()
    return {"password": DEMO_PASSWORD, "users": created}
