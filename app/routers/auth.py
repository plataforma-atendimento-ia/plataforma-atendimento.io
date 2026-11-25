from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..deps import admin_only, current_user, get_db
from ..models import Channel, Tenant, User
from ..security import hash_password, make_token, verify_password

router = APIRouter(tags=["auth"])


class RegisterIn(BaseModel):
    company: str = Field(min_length=2, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserIn(BaseModel):
    name: str
    email: EmailStr
    password: str = Field(min_length=8)
    role: str = Field(default="agent", pattern="^(admin|agent)$")
    max_concurrent: int = Field(default=5, ge=1, le=100)


class ChannelIn(BaseModel):
    phone_number_id: str
    access_token: str | None = None


def user_out(u: User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role, "tenant_id": u.tenant_id}


@router.post("/auth/register", status_code=201)
async def register(body: RegisterIn, s: AsyncSession = Depends(get_db)):
    tenant = Tenant(name=body.company)
    s.add(tenant)
    await s.flush()
    user = User(
        tenant_id=tenant.id,
        email=body.email.lower(),
        name=body.name,
        role="admin",
        password_hash=hash_password(body.password),
    )
    s.add(user)
    try:
        await s.commit()
    except IntegrityError:
        await s.rollback()
        raise HTTPException(409, "e-mail já cadastrado")
    return {"token": make_token(user.id, tenant.id), "user": user_out(user)}


@router.post("/auth/login")
async def login(body: LoginIn, s: AsyncSession = Depends(get_db)):
    user = (await s.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "e-mail ou senha incorretos")
    return {"token": make_token(user.id, user.tenant_id), "user": user_out(user)}


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return user_out(user)


@router.get("/users")
async def list_users(user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    rows = (await s.execute(select(User).where(User.tenant_id == user.tenant_id))).scalars().all()
    return [user_out(u) for u in rows]


@router.post("/users", status_code=201)
async def create_user(body: UserIn, admin: User = Depends(admin_only), s: AsyncSession = Depends(get_db)):
    u = User(
        tenant_id=admin.tenant_id,
        email=body.email.lower(),
        name=body.name,
        role=body.role,
        max_concurrent=body.max_concurrent,
        password_hash=hash_password(body.password),
    )
    s.add(u)
    try:
        await s.commit()
    except IntegrityError:
        await s.rollback()
        raise HTTPException(409, "e-mail já cadastrado")
    return user_out(u)


PLANS = {
    "starter": {"name": "Starter", "numbers": 1},
    "pro": {"name": "Pro", "numbers": 1},
    "business": {"name": "Business", "numbers": 2},
}


class PlanIn(BaseModel):
    plan: str = Field(pattern="^(starter|pro|business)$")


class TestConnectIn(BaseModel):
    phone: str = Field(min_length=8, max_length=30)


def _require_mock():
    if settings.channel_provider != "mock":
        raise HTTPException(404, "disponível apenas em modo de teste")


def _channel_out(c: Channel) -> dict:
    return {"id": c.id, "display_phone": c.display_phone or c.phone_number_id, "is_test": bool(c.is_test)}


@router.get("/onboarding")
async def onboarding(user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    tenant = await s.get(Tenant, user.tenant_id)
    chans = (await s.execute(select(Channel).where(Channel.tenant_id == user.tenant_id))).scalars().all()
    plan = PLANS.get(tenant.plan or "")
    return {
        "plan": tenant.plan,
        "plan_name": plan["name"] if plan else None,
        "number_limit": plan["numbers"] if plan else 1,
        "channels": [_channel_out(c) for c in chans],
        "test_mode": settings.channel_provider == "mock",
        "plans": {k: v["name"] for k, v in PLANS.items()},
    }


@router.post("/billing/test-checkout")
async def test_checkout(body: PlanIn, admin: User = Depends(admin_only), s: AsyncSession = Depends(get_db)):
    """COMPRA DE TESTE: apenas registra o plano, sem cobrança. O pagamento real (Stripe) é a Fase 4."""
    _require_mock()
    tenant = await s.get(Tenant, admin.tenant_id)
    tenant.plan = body.plan
    await s.commit()
    return {"plan": tenant.plan, "plan_name": PLANS[tenant.plan]["name"], "charged": False}


@router.post("/channels/test-connect", status_code=201)
async def test_connect(body: TestConnectIn, admin: User = Depends(admin_only), s: AsyncSession = Depends(get_db)):
    """Conecta um número SIMULADO (qualquer telefone). Não fala com a Meta."""
    _require_mock()
    digits = "".join(ch for ch in body.phone if ch.isdigit())
    if not 8 <= len(digits) <= 15:
        raise HTTPException(422, "informe um telefone com 8 a 15 dígitos")
    tenant = await s.get(Tenant, admin.tenant_id)
    limit = PLANS.get(tenant.plan or "", {"numbers": 1})["numbers"]
    existing = (await s.execute(select(Channel).where(Channel.tenant_id == admin.tenant_id))).scalars().all()
    if any((c.display_phone or "") == "+" + digits for c in existing):
        raise HTTPException(409, "este número já está conectado")
    if len(existing) >= limit:
        raise HTTPException(409, f"o plano permite {limit} número(s) de WhatsApp")
    ch = Channel(
        tenant_id=admin.tenant_id,
        phone_number_id=f"test-{admin.tenant_id}-{len(existing) + 1}",
        display_phone="+" + digits,
        is_test=True,
    )
    s.add(ch)
    await s.commit()
    return _channel_out(ch)


@router.post("/channels", status_code=201)
async def create_channel(body: ChannelIn, admin: User = Depends(admin_only), s: AsyncSession = Depends(get_db)):
    ch = Channel(tenant_id=admin.tenant_id, phone_number_id=body.phone_number_id, access_token=body.access_token)
    s.add(ch)
    try:
        await s.commit()
    except IntegrityError:
        await s.rollback()
        raise HTTPException(409, "phone_number_id já cadastrado")
    return {"id": ch.id, "phone_number_id": ch.phone_number_id}
