"""Qualificação automática de leads e conexão de número real (Meta)."""
from sqlalchemy import select

from app.channels import cloud, provider
from app.channels.base import ChannelError
from app.config import settings
from app.db import SessionLocal
from app.models import Channel, Lead
from app.outbox import process_batch

from .conftest import inbound, new_agent, register

PNID = "109876543210987"
TOKEN = "EAAB" + "x" * 40


async def say(client, headers, text, phone="5511911112222"):
    r = await client.post("/dev/inbound", headers=headers, json={"phone": phone, "text": text, "name": "Cli"})
    assert r.status_code == 201, r.text
    await process_batch(provider)


async def bot_texts(client, headers, conv):
    msgs = (await client.get(f"/conversations/{conv}/messages", headers=headers)).json()
    return [m["body"] for m in msgs if m["direction"] == "out" and m["sender_user_id"] is None]


async def setup(client):
    admin = await register(client, automation=True)
    await client.post("/channels/test-connect", headers=admin, json={"phone": "5527900000001"})
    return admin


async def test_lead_qualification_hot_lead(client):
    admin = await setup(client)
    await say(client, admin, "Oi")
    conv = (await client.get("/conversations", headers=admin)).json()[0]["id"]
    await say(client, admin, "Marina Souza")
    await say(client, admin, "Quero um plano para 5 atendentes")
    await say(client, admin, "1")  # hoje
    await say(client, admin, "3")  # acima de R$ 2.000

    lead = (await client.get("/conversations", headers=admin)).json()[0]["lead"]
    assert lead["name"] == "Marina Souza" and lead["interest"].startswith("Quero um plano")
    assert lead["urgency"] == "hoje" and lead["budget"] == "acima_2000"
    assert lead["score"] == "quente" and lead["stage"] == "qualificado" and lead["step"] == "done"

    texts = await bot_texts(client, admin, conv)
    assert len(texts) == 5 and "qual é o seu nome" in texts[0] and "Você está na fila" in texts[-1]
    # as respostas automáticas saíram de verdade pelo canal
    assert len(provider.sent) == 5
    # a conversa continua na fila para um atendente
    c = (await client.get("/conversations", headers=admin)).json()[0]
    assert c["status"] == "queue" and c["assigned_to"] is None


async def test_lead_score_cold_and_invalid_answer(client):
    admin = await setup(client)
    for t in ["Oi", "Paulo", "Só olhando preços"]:
        await say(client, admin, t)
    await say(client, admin, "talvez")  # resposta inválida: repete a pergunta
    conv = (await client.get("/conversations", headers=admin)).json()[0]["id"]
    assert "Responda 1, 2 ou 3" in (await bot_texts(client, admin, conv))[-1]
    await say(client, admin, "3")  # sem pressa
    await say(client, admin, "1")  # até R$ 500
    assert (await client.get("/conversations", headers=admin)).json()[0]["lead"]["score"] == "frio"


async def test_customer_asks_for_human_stops_automation(client):
    admin = await setup(client)
    await say(client, admin, "Oi")
    await say(client, admin, "Quero falar com um atendente")
    conv = (await client.get("/conversations", headers=admin)).json()[0]["id"]
    assert "chamando um atendente" in (await bot_texts(client, admin, conv))[-1]
    before = len(await bot_texts(client, admin, conv))
    await say(client, admin, "alô?")  # automação já encerrou: não responde mais
    assert len(await bot_texts(client, admin, conv)) == before


async def test_agent_claiming_stops_automation(client):
    admin = await setup(client)
    await say(client, admin, "Oi")
    conv = (await client.get("/conversations", headers=admin)).json()[0]["id"]
    assert (await client.post(f"/conversations/{conv}/claim", headers=admin)).status_code == 200
    before = len(await bot_texts(client, admin, conv))
    await say(client, admin, "Meu nome é Ana")
    assert len(await bot_texts(client, admin, conv)) == before


async def test_automation_can_be_turned_off_and_only_by_admin(client):
    admin = await setup(client)
    agent = await new_agent(client, admin, "ag@a.com")
    assert (await client.put("/automation", headers=agent, json={"enabled": False})).status_code == 403
    assert (await client.put("/automation", headers=admin, json={"enabled": False})).json() == {"automation_on": False}
    await say(client, admin, "Oi")
    conv = (await client.get("/conversations", headers=admin)).json()[0]["id"]
    assert await bot_texts(client, admin, conv) == []


async def test_connect_real_number_validates_credentials(client, monkeypatch):
    admin = await register(client)
    await client.post("/billing/test-checkout", headers=admin, json={"plan": "pro"})

    async def fake_ok(pnid, token):
        assert pnid == PNID and token == TOKEN
        return {"display_phone_number": "+55 27 98888-7777", "verified_name": "Minha Loja"}

    monkeypatch.setattr(cloud, "check_credentials", fake_ok)
    r = await client.post(
        "/channels/connect-meta", headers=admin, json={"phone": "27988887777", "phone_number_id": PNID, "access_token": TOKEN}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["display_phone"] == "+5527988887777" and body["is_test"] is False and body["verified_name"] == "Minha Loja"
    assert "access_token" not in r.text  # o token nunca volta para o navegador

    ob = (await client.get("/onboarding", headers=admin)).json()
    assert ob["channels"][0]["is_test"] is False and ob["verify_token"] == settings.whatsapp_verify_token


async def test_connect_real_number_rejects_bad_credentials_and_respects_plan(client, monkeypatch):
    admin = await register(client)

    async def fake_bad(pnid, token):
        raise ChannelError("a Meta recusou as credenciais: token inválido", retryable=False)

    monkeypatch.setattr(cloud, "check_credentials", fake_bad)
    r = await client.post(
        "/channels/connect-meta", headers=admin, json={"phone": "27988887777", "phone_number_id": PNID, "access_token": TOKEN}
    )
    assert r.status_code == 422 and "recusou" in r.json()["detail"]
    assert (await client.get("/onboarding", headers=admin)).json()["channels"] == []  # nada foi salvo

    async def fake_ok(pnid, token):
        return {"display_phone_number": "+5527988887777"}

    monkeypatch.setattr(cloud, "check_credentials", fake_ok)
    assert (await client.post("/channels/connect-meta", headers=admin, json={"phone": "27988887777", "phone_number_id": PNID, "access_token": TOKEN})).status_code == 201
    r = await client.post("/channels/connect-meta", headers=admin, json={"phone": "27911112222", "phone_number_id": "222222222222", "access_token": TOKEN})
    assert r.status_code == 409 and "plano" in r.json()["detail"]


async def test_real_number_receives_via_webhook_and_replies_through_meta(client, monkeypatch):
    """Número real: mensagem chega pelo webhook da Meta e a resposta sai pelo provedor da Meta."""
    admin = await register(client)  # automação desligada

    async def fake_ok(pnid, token):
        return {"display_phone_number": "+5527988887777"}

    monkeypatch.setattr(cloud, "check_credentials", fake_ok)
    await client.post("/channels/connect-meta", headers=admin, json={"phone": "27988887777", "phone_number_id": PNID, "access_token": TOKEN})

    payload = {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": PNID},
        "contacts": [{"profile": {"name": "Cliente Real"}, "wa_id": "5511955554444"}],
        "messages": [{"from": "5511955554444", "id": "wamid.REAL1", "type": "text", "text": {"body": "Olá, vocês atendem hoje?"}}],
    }}]}]}
    assert (await client.post("/webhooks/whatsapp", json=payload)).status_code == 200
    conv = (await client.get("/conversations", headers=admin)).json()[0]
    assert conv["contact"]["name"] == "Cliente Real"

    sent = {}

    async def fake_send(channel, to, body, key):
        sent.update(channel_is_test=channel.is_test, to=to, body=body, token=channel.access_token)
        return "wamid.OUT1"

    monkeypatch.setattr(cloud, "send_text", fake_send)
    await client.post(f"/conversations/{conv['id']}/claim", headers=admin)
    await client.post(f"/conversations/{conv['id']}/messages", headers=admin, json={"body": "Atendemos sim!"})
    await process_batch(provider)
    assert sent == {"channel_is_test": False, "to": "5511955554444", "body": "Atendemos sim!", "token": TOKEN}
    assert provider.sent == []  # nada vazou para o canal simulado


async def test_simulator_never_writes_to_real_number(client, monkeypatch):
    admin = await register(client)

    async def fake_ok(pnid, token):
        return {"display_phone_number": "+5527988887777"}

    monkeypatch.setattr(cloud, "check_credentials", fake_ok)
    await client.post("/channels/connect-meta", headers=admin, json={"phone": "27988887777", "phone_number_id": PNID, "access_token": TOKEN})
    r = await client.post("/dev/inbound", headers=admin, json={"phone": "5511900000000", "text": "oi"})
    assert r.status_code == 409  # só existe número real; simulador exige um número de teste
