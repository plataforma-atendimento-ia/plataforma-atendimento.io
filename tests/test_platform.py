import asyncio

from sqlalchemy import select

from app.channels import provider
from app.config import settings
from app.db import SessionLocal
from app.models import Message
from app.outbox import process_batch

from .conftest import inbound, new_agent, register


async def test_sales_site_and_app_are_served(client):
    site = await client.get("/")
    assert site.status_code == 200 and "Plataforma de Atendimento IA" in site.text
    assert 'href="/app/"' in site.text  # botão que leva ao sistema
    app_page = await client.get("/app/")
    assert app_page.status_code == 200 and "Atendimento" in app_page.text


async def test_buy_plan_connect_whatsapp_and_take_customer(client):
    """Fluxo completo do teste: comprar plano -> conectar número -> cliente chega -> assumir -> responder."""
    admin = await register(client)
    ob = (await client.get("/onboarding", headers=admin)).json()
    assert ob["plan"] is None and ob["channels"] == [] and ob["test_mode"] is True

    # sem número conectado, simular cliente é recusado
    r = await client.post("/dev/inbound", headers=admin, json={"phone": "5511900000001", "text": "oi"})
    assert r.status_code == 409

    r = await client.post("/billing/test-checkout", headers=admin, json={"plan": "pro"})
    assert r.json() == {"plan": "pro", "plan_name": "Pro", "charged": False}

    r = await client.post("/channels/test-connect", headers=admin, json={"phone": "+55 (27) 99999-1234"})
    assert r.status_code == 201 and r.json()["display_phone"] == "+5527999991234"

    conv = await inbound(client, admin, phone="5511900000001", text="Quero comprar")
    assert (await client.post(f"/conversations/{conv}/claim", headers=admin)).status_code == 200
    r = await client.post(f"/conversations/{conv}/messages", headers=admin, json={"body": "Claro!"})
    assert r.status_code == 201
    await process_batch(provider)
    assert provider.sent[-1]["body"] == "Claro!"


async def test_plan_limits_whatsapp_numbers(client):
    admin = await register(client)
    await client.post("/billing/test-checkout", headers=admin, json={"plan": "starter"})
    assert (await client.post("/channels/test-connect", headers=admin, json={"phone": "5527900000001"})).status_code == 201
    r = await client.post("/channels/test-connect", headers=admin, json={"phone": "5527900000002"})
    assert r.status_code == 409 and "plano" in r.json()["detail"]
    # plano Business permite 2
    await client.post("/billing/test-checkout", headers=admin, json={"plan": "business"})
    assert (await client.post("/channels/test-connect", headers=admin, json={"phone": "5527900000002"})).status_code == 201


async def test_connect_validation_and_permissions(client):
    admin = await register(client)
    agent = await new_agent(client, admin, "ag@a.com")
    assert (await client.post("/channels/test-connect", headers=agent, json={"phone": "5527900000001"})).status_code == 403
    assert (await client.post("/billing/test-checkout", headers=agent, json={"plan": "pro"})).status_code == 403
    assert (await client.post("/channels/test-connect", headers=admin, json={"phone": "abc12345"})).status_code == 422
    assert (await client.post("/billing/test-checkout", headers=admin, json={"plan": "gratis"})).status_code == 422


async def test_login_wrong_password(client):
    await register(client)
    r = await client.post("/auth/login", json={"email": "admin@a.com", "password": "errada-123"})
    assert r.status_code == 401


async def test_requires_auth(client):
    assert (await client.get("/conversations")).status_code == 401


async def test_tenant_isolation(client):
    a = await register(client, "Empresa A", "a@a.com")
    b = await register(client, "Empresa B", "b@b.com")
    conv_b = await inbound(client, b, text="segredo do B")

    assert (await client.get("/conversations", headers=a)).json() == []
    assert (await client.get(f"/conversations/{conv_b}/messages", headers=a)).status_code == 404
    assert (await client.post(f"/conversations/{conv_b}/claim", headers=a)).status_code == 404
    assert (await client.post(f"/conversations/{conv_b}/close", headers=a)).status_code == 404
    # o dono continua enxergando
    assert len((await client.get("/conversations", headers=b)).json()) == 1
    # usuários também são isolados
    assert len((await client.get("/users", headers=a)).json()) == 1


async def test_claim_is_atomic(client):
    admin = await register(client)
    ag1 = await new_agent(client, admin, "x1@a.com")
    ag2 = await new_agent(client, admin, "x2@a.com")
    conv = await inbound(client, admin)

    r1, r2 = await asyncio.gather(
        client.post(f"/conversations/{conv}/claim", headers=ag1),
        client.post(f"/conversations/{conv}/claim", headers=ag2),
    )
    assert sorted([r1.status_code, r2.status_code]) == [200, 409]


async def test_claim_respects_concurrency_limit(client):
    admin = await register(client)
    await client.post(
        "/users",
        headers=admin,
        json={"name": "L", "email": "l@a.com", "password": "senha-forte-1", "max_concurrent": 1},
    )
    ag = (await client.post("/auth/login", json={"email": "l@a.com", "password": "senha-forte-1"})).json()
    h = {"Authorization": f"Bearer {ag['token']}"}
    c1 = await inbound(client, admin, phone="551100000001")
    await inbound(client, admin, phone="551100000002")
    convs = (await client.get("/conversations", headers=admin)).json()
    c2 = next(c["id"] for c in convs if c["id"] != c1)
    assert (await client.post(f"/conversations/{c1}/claim", headers=h)).status_code == 200
    assert (await client.post(f"/conversations/{c2}/claim", headers=h)).status_code == 409


async def test_must_claim_before_sending(client):
    admin = await register(client)
    conv = await inbound(client, admin)
    r = await client.post(f"/conversations/{conv}/messages", headers=admin, json={"body": "olá"})
    assert r.status_code == 403


async def test_outbox_sends_message(client):
    admin = await register(client)
    conv = await inbound(client, admin)
    await client.post(f"/conversations/{conv}/claim", headers=admin)
    r = await client.post(f"/conversations/{conv}/messages", headers=admin, json={"body": "Olá, tudo bem?"})
    assert r.json()["status"] == "queued"  # gravada antes de enviar

    await process_batch(provider)
    msgs = (await client.get(f"/conversations/{conv}/messages", headers=admin)).json()
    out = [m for m in msgs if m["direction"] == "out"][0]
    assert out["status"] == "sent"
    assert len(provider.sent) == 1 and provider.sent[0]["body"] == "Olá, tudo bem?"


async def test_outbox_retries_then_fails_then_resend(client):
    admin = await register(client)
    conv = await inbound(client, admin)
    await client.post(f"/conversations/{conv}/claim", headers=admin)
    mid = (await client.post(f"/conversations/{conv}/messages", headers=admin, json={"body": "oi"})).json()["id"]

    provider.fail_next = 999
    for _ in range(settings.outbox_max_attempts):
        await process_batch(provider)
    msgs = (await client.get(f"/conversations/{conv}/messages", headers=admin)).json()
    failed = next(m for m in msgs if m["id"] == mid)
    assert failed["status"] == "failed"
    assert failed["attempts"] == settings.outbox_max_attempts
    assert failed["error"]  # o atendente vê o motivo; nada some em silêncio

    provider.fail_next = 0
    assert (await client.post(f"/messages/{mid}/resend", headers=admin)).json()["status"] == "queued"
    await process_batch(provider)
    msgs = (await client.get(f"/conversations/{conv}/messages", headers=admin)).json()
    assert next(m for m in msgs if m["id"] == mid)["status"] == "sent"


async def test_non_retryable_error_fails_immediately(client):
    admin = await register(client)
    conv = await inbound(client, admin)
    await client.post(f"/conversations/{conv}/claim", headers=admin)
    await client.post(f"/conversations/{conv}/messages", headers=admin, json={"body": "oi"})
    provider.fail_next, provider.fail_retryable = 1, False
    await process_batch(provider)
    msgs = (await client.get(f"/conversations/{conv}/messages", headers=admin)).json()
    out = [m for m in msgs if m["direction"] == "out"][0]
    assert out["status"] == "failed" and out["attempts"] == 1


async def test_stuck_sending_is_requeued(client):
    admin = await register(client)
    conv = await inbound(client, admin)
    await client.post(f"/conversations/{conv}/claim", headers=admin)
    mid = (await client.post(f"/conversations/{conv}/messages", headers=admin, json={"body": "oi"})).json()["id"]
    from datetime import timedelta

    from app.db import utcnow

    async with SessionLocal() as s:  # simula worker que caiu no meio do envio
        m = await s.get(Message, mid)
        m.status, m.updated_at = "sending", utcnow() - timedelta(seconds=settings.sending_timeout_seconds + 5)
        await s.commit()
    await process_batch(provider)
    async with SessionLocal() as s:
        assert (await s.get(Message, mid)).status == "sent"


async def test_client_idempotency_no_duplicate(client):
    admin = await register(client)
    conv = await inbound(client, admin)
    await client.post(f"/conversations/{conv}/claim", headers=admin)
    body = {"body": "uma vez só", "client_id": "abc-1"}
    r1 = await client.post(f"/conversations/{conv}/messages", headers=admin, json=body)
    r2 = await client.post(f"/conversations/{conv}/messages", headers=admin, json=body)
    assert r1.json()["id"] == r2.json()["id"]
    async with SessionLocal() as s:
        rows = (await s.execute(select(Message).where(Message.direction == "out"))).scalars().all()
        assert len(rows) == 1


def wa_payload(wamid="wamid.1", text="oi", phone="5511988887777", pnid="PNID1"):
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": pnid},
                            "contacts": [{"profile": {"name": "Ana"}, "wa_id": phone}],
                            "messages": [{"from": phone, "id": wamid, "type": "text", "text": {"body": text}}],
                        }
                    }
                ]
            }
        ]
    }


async def test_webhook_inbound_is_idempotent(client):
    admin = await register(client)
    await client.post("/channels", headers=admin, json={"phone_number_id": "PNID1"})
    for _ in range(3):  # a Meta reenvia webhooks; só pode haver 1 mensagem
        assert (await client.post("/webhooks/whatsapp", json=wa_payload())).status_code == 200
    convs = (await client.get("/conversations", headers=admin)).json()
    assert len(convs) == 1 and convs[0]["contact"]["name"] == "Ana"
    msgs = (await client.get(f"/conversations/{convs[0]['id']}/messages", headers=admin)).json()
    assert len(msgs) == 1


async def test_webhook_status_progression_never_regresses(client):
    admin = await register(client)
    await client.post("/channels", headers=admin, json={"phone_number_id": "PNID1"})
    await client.post("/webhooks/whatsapp", json=wa_payload())
    conv = (await client.get("/conversations", headers=admin)).json()[0]["id"]
    await client.post(f"/conversations/{conv}/claim", headers=admin)
    await client.post(f"/conversations/{conv}/messages", headers=admin, json={"body": "resposta"})
    await process_batch(provider)
    wamid = next(m for m in (await client.get(f"/conversations/{conv}/messages", headers=admin)).json() if m["direction"] == "out")
    async with SessionLocal() as s:
        wamid = (await s.get(Message, wamid["id"])).wamid

    def status(st):
        return {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "PNID1"}, "statuses": [{"id": wamid, "status": st}]}}]}]}

    async def current():
        msgs = (await client.get(f"/conversations/{conv}/messages", headers=admin)).json()
        return next(m for m in msgs if m["direction"] == "out")["status"]

    await client.post("/webhooks/whatsapp", json=status("read"))
    assert await current() == "read"
    await client.post("/webhooks/whatsapp", json=status("delivered"))  # chegou fora de ordem
    assert await current() == "read"


async def test_webhook_verification(client):
    ok = await client.get(
        "/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "dev-verify", "hub.challenge": "123"}
    )
    assert ok.status_code == 200 and ok.text == "123"
    bad = await client.get(
        "/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "123"}
    )
    assert bad.status_code == 403
