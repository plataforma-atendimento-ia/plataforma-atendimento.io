import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import SessionLocal
from ..deps import current_user, get_db
from ..models import Channel, Contact, Message, User
from ..realtime import hub
from ..services import STATUS_RANK, conv_out, handle_inbound, msg_out

router = APIRouter(tags=["webhooks"])


@router.get("/webhooks/whatsapp")
async def verify(
    mode: str = Query(alias="hub.mode", default=""),
    token: str = Query(alias="hub.verify_token", default=""),
    challenge: str = Query(alias="hub.challenge", default=""),
):
    if mode == "subscribe" and hmac.compare_digest(token, settings.whatsapp_verify_token):
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(403, "token de verificação inválido")


@router.post("/webhooks/whatsapp")
async def receive(request: Request):
    raw = await request.body()
    if settings.whatsapp_app_secret:
        sig = request.headers.get("x-hub-signature-256", "")
        expected = "sha256=" + hmac.new(settings.whatsapp_app_secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(401, "assinatura inválida")
    payload = json.loads(raw or b"{}")
    # Responde 200 sempre que a assinatura é válida; cada item é tratado de forma idempotente.
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            await _process_change(change.get("value", {}))
    return {"ok": True}


async def _process_change(value: dict) -> None:
    pnid = value.get("metadata", {}).get("phone_number_id")
    if not pnid:
        return
    async with SessionLocal() as s:
        channel = (await s.execute(select(Channel).where(Channel.phone_number_id == pnid))).scalar_one_or_none()
        if channel is None:
            return
        s.sync_session.info["tenant_id"] = channel.tenant_id
        names = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])}
        for m in value.get("messages", []):
            if m.get("type") == "text":
                text = m.get("text", {}).get("body", "")
            else:
                text = f"[{m.get('type', 'mensagem')} não suportado ainda]"
            await _store_inbound(s, channel, m["from"], names.get(m["from"]), text, m["id"])
        for st in value.get("statuses", []):
            await _apply_status(s, channel.tenant_id, st)


async def _store_inbound(s: AsyncSession, channel: Channel, phone, name, text, wamid) -> bool:
    res = await handle_inbound(s, channel, phone, name, text, wamid)
    if res is None:
        return False
    conv, msg = res
    contact = await s.get(Contact, conv.contact_id)
    await hub.publish(channel.tenant_id, {"type": "message.new", "message": msg_out(msg)})
    await hub.publish(
        channel.tenant_id, {"type": "conversation.updated", "conversation": conv_out(conv, contact)}
    )
    return True


async def _apply_status(s: AsyncSession, tenant_id: str, st: dict) -> None:
    msg = (
        await s.execute(select(Message).where(Message.wamid == st.get("id"), Message.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if msg is None:
        return
    new = st.get("status")
    if new == "failed":
        msg.status = "failed"
        errs = st.get("errors") or [{}]
        msg.error = str(errs[0].get("title") or errs[0].get("message") or "falha reportada pela Meta")[:500]
    elif new in STATUS_RANK and STATUS_RANK[new] > STATUS_RANK.get(msg.status, -1):
        msg.status = new  # nunca regride (ex.: read -> delivered)
    else:
        return
    await s.commit()
    await hub.publish(tenant_id, {"type": "message.status", "message": msg_out(msg)})


class SimulateIn(BaseModel):
    phone: str
    name: str | None = None
    text: str
    wamid: str | None = None


@router.post("/dev/inbound", status_code=201)
async def simulate_inbound(body: SimulateIn, user: User = Depends(current_user), s: AsyncSession = Depends(get_db)):
    """Simula um cliente escrevendo (somente com CHANNEL_PROVIDER=mock)."""
    if settings.channel_provider != "mock":
        raise HTTPException(404)
    channel = (
        await s.execute(select(Channel).where(Channel.tenant_id == user.tenant_id).limit(1))
    ).scalar_one_or_none()
    if channel is None:  # cria um canal simulado automaticamente
        channel = Channel(tenant_id=user.tenant_id, phone_number_id=f"mock-{user.tenant_id}")
        s.add(channel)
        await s.commit()
    from ..models import uid

    created = await _store_inbound(s, channel, body.phone, body.name, body.text, body.wamid or f"sim.{uid()}")
    return {"created": created}
