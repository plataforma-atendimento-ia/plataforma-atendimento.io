"""Conexão não oficial por QR Code: usa uma ponte FALSA (não fala com o WhatsApp)."""
import pytest
from sqlalchemy import select

from app.channels import bridge as bridge_mod
from app.channels import provider
from app.channels.base import ChannelError
from app.config import settings
from app.db import SessionLocal
from app.models import Channel, Contact
from app.outbox import process_batch
from app.routers import auth as auth_mod

from .conftest import new_agent, register

SECRET = {"x-bridge-secret": "segredo-da-ponte-so-para-testes"}


class FakeBridge:
    def __init__(self):
        self.calls = []
        self.session = {"status": "qr", "qr": "data:image/png;base64,AAAA", "phone": None}
        self.down = False
        self.send_error = None

    async def request(self, method, path, json=None, timeout=15):
        self.calls.append((method, path, json))
        if self.down:
            raise bridge_mod.BridgeDown("recusou conexão")
        if path.endswith("/send"):
            if self.send_error:
                raise self.send_error
            return {"id": "3EB0FAKE" + str(len(self.calls))}
        if method == "GET":
            return dict(self.session)
        return {"status": "starting"}

    async def up(self):
        return not self.down


@pytest.fixture
def fake(monkeypatch):
    f = FakeBridge()
    monkeypatch.setattr(bridge_mod, "request", f.request)
    monkeypatch.setattr(auth_mod, "bridge_request", f.request)
    monkeypatch.setattr(auth_mod, "bridge_up", f.up)
    return f


async def start_qr(client, admin):
    r = await client.post("/channels/qr/start", headers=admin)
    assert r.status_code == 201, r.text
    return r.json()


async def test_qr_flow_start_show_qr_then_connected(client, fake):
    admin = await register(client)
    ch = await start_qr(client, admin)
    assert ch["via"] == "qr" and ch["link_status"] == "pending" and ch["is_test"] is False
    assert ("POST", f"/sessions/{ch['id']}/start", None) in fake.calls

    st = (await client.get(f"/channels/{ch['id']}/qr", headers=admin)).json()
    assert st["status"] == "qr" and st["qr"].startswith("data:image/png")

    fake.session = {"status": "connected", "qr": None, "phone": "5527988887777"}
    st = (await client.get(f"/channels/{ch['id']}/qr", headers=admin)).json()
    assert st["status"] == "connected" and st["phone"] == "+5527988887777"
    ob = (await client.get("/onboarding", headers=admin)).json()
    assert ob["channels"][0]["link_status"] == "connected" and ob["qr_enabled"] is True and ob["bridge_up"] is True


async def test_start_reuses_pending_and_respects_plan(client, fake):
    admin = await register(client)
    a = await start_qr(client, admin)
    b = await start_qr(client, admin)  # tentativa ainda pendente: reaproveita
    assert a["id"] == b["id"]
    fake.session = {"status": "connected", "qr": None, "phone": "5527988887777"}
    await client.get(f"/channels/{a['id']}/qr", headers=admin)
    r = await client.post("/channels/qr/start", headers=admin)  # plano padrão: 1 número
    assert r.status_code == 409 and "plano" in r.json()["detail"]


async def test_start_fails_cleanly_when_bridge_is_down(client, fake):
    admin = await register(client)
    fake.down = True
    r = await client.post("/channels/qr/start", headers=admin)
    assert r.status_code == 503 and "ponte" in r.json()["detail"]
    fake.down = False
    assert (await client.get("/onboarding", headers=admin)).json()["channels"] == []  # nada ficou pela metade


async def test_only_admin_and_isolation(client, fake):
    a = await register(client, "Empresa A", "a@a.com")
    b = await register(client, "Empresa B", "b@b.com")
    agent = await new_agent(client, a, "ag@a.com")
    assert (await client.post("/channels/qr/start", headers=agent)).status_code == 403
    ch = await start_qr(client, a)
    assert (await client.get(f"/channels/{ch['id']}/qr", headers=b)).status_code == 404
    assert (await client.post(f"/channels/{ch['id']}/disconnect", headers=b)).status_code == 404


async def test_bridge_endpoints_require_secret(client, fake):
    admin = await register(client)
    ch = await start_qr(client, admin)
    body = {"channel_id": ch["id"], "jid": "5511955554444@s.whatsapp.net", "phone": "5511955554444", "text": "oi", "wamid": "A1"}
    assert (await client.post("/bridge/inbound", json=body)).status_code == 401
    assert (await client.post("/bridge/inbound", json=body, headers={"x-bridge-secret": "errado"})).status_code == 401
    assert (await client.post("/bridge/inbound", json=body, headers=SECRET)).status_code == 200


async def test_inbound_from_bridge_and_reply_goes_to_exact_jid(client, fake):
    admin = await register(client)  # automação desligada
    ch = await start_qr(client, admin)
    # cliente com endereço novo (LID): o telefone vem separado do endereço de resposta
    body = {"channel_id": ch["id"], "jid": "98765432109876@lid", "phone": "5511955554444", "name": "Bia", "text": "Olá!", "wamid": "A1"}
    assert (await client.post("/bridge/inbound", json=body, headers=SECRET)).json() == {"created": True}
    assert (await client.post("/bridge/inbound", json=body, headers=SECRET)).json() == {"created": False}  # duplicada

    conv = (await client.get("/conversations", headers=admin)).json()[0]
    assert conv["contact"]["phone"] == "5511955554444" and conv["contact"]["name"] == "Bia"
    await client.post(f"/conversations/{conv['id']}/claim", headers=admin)
    await client.post(f"/conversations/{conv['id']}/messages", headers=admin, json={"body": "Oi, Bia!"})
    await process_batch(provider)

    sends = [c for c in fake.calls if c[1].endswith("/send")]
    assert len(sends) == 1
    assert sends[0][2]["to"] == "98765432109876@lid" and sends[0][2]["text"] == "Oi, Bia!"
    assert provider.sent == []  # nada foi para o canal simulado
    msgs = (await client.get(f"/conversations/{conv['id']}/messages", headers=admin)).json()
    assert [m["status"] for m in msgs if m["direction"] == "out"] == ["sent"]


async def test_status_updates_from_bridge_never_regress(client, fake):
    admin = await register(client)
    ch = await start_qr(client, admin)
    await client.post("/bridge/inbound", headers=SECRET, json={"channel_id": ch["id"], "jid": "5511900001111@s.whatsapp.net", "phone": "5511900001111", "text": "oi", "wamid": "B1"})
    conv = (await client.get("/conversations", headers=admin)).json()[0]["id"]
    await client.post(f"/conversations/{conv}/claim", headers=admin)
    await client.post(f"/conversations/{conv}/messages", headers=admin, json={"body": "resposta"})
    await process_batch(provider)
    wamid = next(c for c in fake.calls if c[1].endswith("/send")) and "3EB0FAKE" + str(len(fake.calls))

    async def status_of():
        msgs = (await client.get(f"/conversations/{conv}/messages", headers=admin)).json()
        return next(m for m in msgs if m["direction"] == "out")["status"]

    for st, expected in [("read", "read"), ("delivered", "read")]:
        await client.post("/bridge/status", headers=SECRET, json={"channel_id": ch["id"], "wamid": wamid, "status": st})
        assert await status_of() == expected


async def test_send_errors_are_classified(client, fake):
    admin = await register(client)
    ch = await start_qr(client, admin)
    await client.post("/bridge/inbound", headers=SECRET, json={"channel_id": ch["id"], "jid": "5511900001111@s.whatsapp.net", "phone": "5511900001111", "text": "oi", "wamid": "C1"})
    conv = (await client.get("/conversations", headers=admin)).json()[0]["id"]
    await client.post(f"/conversations/{conv}/claim", headers=admin)
    await client.post(f"/conversations/{conv}/messages", headers=admin, json={"body": "x"})

    fake.send_error = ChannelError("WhatsApp não está conectado", retryable=True)
    settings.outbox_backoff_seconds = [0]
    await process_batch(provider)
    msg = (await client.get(f"/conversations/{conv}/messages", headers=admin)).json()[-1]
    assert msg["status"] == "queued" and msg["attempts"] == 1  # erro temporário: tenta de novo

    fake.send_error = None
    await process_batch(provider)
    assert (await client.get(f"/conversations/{conv}/messages", headers=admin)).json()[-1]["status"] == "sent"


async def test_disconnect_frees_the_plan_slot_and_stops_inbound(client, fake):
    admin = await register(client)
    ch = await start_qr(client, admin)
    r = await client.post(f"/channels/{ch['id']}/disconnect", headers=admin)
    assert r.json()["link_status"] == "disconnected"
    assert (await client.get("/onboarding", headers=admin)).json()["channels"] == []
    body = {"channel_id": ch["id"], "jid": "5511900001111@s.whatsapp.net", "phone": "5511900001111", "text": "oi", "wamid": "D1"}
    assert (await client.post("/bridge/inbound", json=body, headers=SECRET)).json() == {"created": False}
    assert (await client.post("/channels/qr/start", headers=admin)).status_code == 201  # vaga liberada
    async with SessionLocal() as s:
        assert len((await s.execute(select(Channel))).scalars().all()) == 2
        assert (await s.execute(select(Contact))).scalars().all() == []
