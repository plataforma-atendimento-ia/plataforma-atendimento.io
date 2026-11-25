from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..channels import cloud
from ..channels.base import ChannelError
from ..channels.bridge import BridgeDown, bridge_up
from ..channels.bridge import request as bridge_request
from ..config import settings
from ..deps import admin_only, current_user, get_db
from ..models import Channel, Tenant, User, uid
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


class MetaConnectIn(BaseModel):
    phone: str = Field(min_length=8, max_length=30)
    phone_number_id: str = Field(pattern=r"^\d{5,30}$")
    access_token: str = Field(min_length=20, max_length=1000)


class AutomationIn(BaseModel):
    enabled: bool


def _require_mock():
    if not settings.test_mode:
        raise HTTPException(404, "disponível apenas em modo de teste")


def _channel_out(c: Channel) -> dict:
    return {
        "id": c.id,
        "display_phone": c.display_phone or ("aguardando leitura do QR" if c.via == "qr" else c.phone_number_id),
        "is_test": bool(c.is_test),
        "via": c.via,
        "link_status": c.link_status,
    }


def _active(channels) -> list:
    return [c for c in channels if c.link_status != "disconnected"]


@router.get("/onboarding")
async def onboarding(user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    tenant = await s.get(Tenant, user.tenant_id)
    chans = (await s.execute(select(Channel).where(Channel.tenant_id == user.tenant_id))).scalars().all()
    plan = PLANS.get(tenant.plan or "")
    return {
        "plan": tenant.plan,
        "plan_name": plan["name"] if plan else None,
        "number_limit": plan["numbers"] if plan else 1,
        "channels": [_channel_out(c) for c in _active(chans)],
        "qr_enabled": bool(settings.bridge_secret),
        "bridge_up": await bridge_up(),
        "test_mode": settings.test_mode,
        "automation_on": bool(tenant.automation_on),
        "is_admin": user.role == "admin",
        # dados para configurar o webhook na Meta (só para administradores)
        "webhook_path": "/webhooks/whatsapp",
        "verify_token": settings.whatsapp_verify_token if user.role == "admin" else None,
        "plans": {k: v["name"] for k, v in PLANS.items()},
    }


@router.put("/automation")
async def set_automation(body: AutomationIn, admin: User = Depends(admin_only), s: AsyncSession = Depends(get_db)):
    tenant = await s.get(Tenant, admin.tenant_id)
    tenant.automation_on = body.enabled
    await s.commit()
    return {"automation_on": tenant.automation_on}


@router.post("/channels/connect-meta", status_code=201)
async def connect_meta(body: MetaConnectIn, admin: User = Depends(admin_only), s: AsyncSession = Depends(get_db)):
    """Conecta um número REAL pela API oficial da Meta. As credenciais são conferidas antes de salvar."""
    digits = "".join(ch for ch in body.phone if ch.isdigit())
    if not 8 <= len(digits) <= 15:
        raise HTTPException(422, "informe o telefone com 8 a 15 dígitos")
    tenant = await s.get(Tenant, admin.tenant_id)
    limit = PLANS.get(tenant.plan or "", {"numbers": 1})["numbers"]
    existing = (await s.execute(select(Channel).where(Channel.tenant_id == admin.tenant_id))).scalars().all()
    if len(_active(existing)) >= limit:
        raise HTTPException(409, f"o plano permite {limit} número(s) de WhatsApp")
    try:
        info = await cloud.check_credentials(body.phone_number_id, body.access_token)
    except ChannelError as e:
        raise HTTPException(422, str(e))
    shown = "".join(ch for ch in str(info.get("display_phone_number", "")) if ch.isdigit()) or digits
    ch = Channel(
        tenant_id=admin.tenant_id,
        phone_number_id=body.phone_number_id,
        access_token=body.access_token,
        display_phone="+" + shown,
        is_test=False,
    )
    s.add(ch)
    try:
        await s.commit()
    except IntegrityError:
        await s.rollback()
        raise HTTPException(409, "este Phone number ID já está conectado")
    return {**_channel_out(ch), "verified_name": info.get("verified_name")}


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
    if len(_active(existing)) >= limit:
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


def _require_bridge():
    if not settings.bridge_secret:
        raise HTTPException(503, "a conexão por QR Code está desligada (defina BRIDGE_SECRET no .env)")


async def _qr_channel_of(s: AsyncSession, admin: User, channel_id: str) -> Channel:
    ch = (
        await s.execute(
            select(Channel).where(Channel.id == channel_id, Channel.tenant_id == admin.tenant_id, Channel.via == "qr")
        )
    ).scalar_one_or_none()
    if ch is None:
        raise HTTPException(404, "canal não encontrado")
    return ch


@router.post("/channels/qr/start", status_code=201)
async def qr_start(admin: User = Depends(admin_only), s: AsyncSession = Depends(get_db)):
    """Conexão NÃO OFICIAL por QR Code (viola os termos do WhatsApp; risco de banimento do número)."""
    _require_bridge()
    tenant = await s.get(Tenant, admin.tenant_id)
    limit = PLANS.get(tenant.plan or "", {"numbers": 1})["numbers"]
    existing = (await s.execute(select(Channel).where(Channel.tenant_id == admin.tenant_id))).scalars().all()
    pending = [c for c in existing if c.via == "qr" and c.link_status == "pending"]
    if pending:  # reaproveita a tentativa em andamento (evita criar vários canais)
        ch = pending[0]
    else:
        if len(_active(existing)) >= limit:
            raise HTTPException(409, f"o plano permite {limit} número(s) de WhatsApp")
        ch = Channel(tenant_id=admin.tenant_id, phone_number_id=f"qr-{uid()}", via="qr", link_status="pending")
        s.add(ch)
        await s.commit()
    try:
        await bridge_request("POST", f"/sessions/{ch.id}/start")
    except BridgeDown:
        if not pending:
            await s.delete(ch)
            await s.commit()
        raise HTTPException(503, "a ponte do WhatsApp não está rodando. Abra o sistema com .\\run.ps1 (ele liga a ponte).")
    return _channel_out(ch)


@router.get("/channels/{channel_id}/qr")
async def qr_status(channel_id: str, admin: User = Depends(admin_only), s: AsyncSession = Depends(get_db)):
    ch = await _qr_channel_of(s, admin, channel_id)
    try:
        info = await bridge_request("GET", f"/sessions/{ch.id}", timeout=5)
    except BridgeDown:
        raise HTTPException(503, "a ponte do WhatsApp não está rodando")
    if info.get("status") == "connected" and ch.link_status != "connected":
        ch.link_status = "connected"
        if info.get("phone"):
            ch.display_phone = "+" + "".join(c for c in str(info["phone"]) if c.isdigit())
        await s.commit()
    return {"status": info.get("status"), "qr": info.get("qr"), "phone": ch.display_phone, "channel": _channel_out(ch)}


@router.post("/channels/{channel_id}/disconnect")
async def qr_disconnect(channel_id: str, admin: User = Depends(admin_only), s: AsyncSession = Depends(get_db)):
    ch = await _qr_channel_of(s, admin, channel_id)
    try:
        await bridge_request("POST", f"/sessions/{ch.id}/stop")
    except BridgeDown:
        pass  # a ponte já estava parada; marcamos como desconectado mesmo assim
    ch.link_status = "disconnected"
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
