"""Conversa no estilo WhatsApp: anexos, áudio de voz, responder, editar, apagar para todos e reagir."""
import base64
import os
import subprocess
from datetime import timedelta

import pytest

from app import media
from app.channels import cloud, provider
from app.db import SessionLocal, utcnow
from app.models import Message
from app.outbox import process_batch

from .conftest import inbound, new_agent, register

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


async def ready(client):
    admin = await register(client)
    conv = await inbound(client, admin, text="Oi, tudo bem?")
    await client.post(f"/conversations/{conv}/claim", headers=admin)
    return admin, conv


async def msgs(client, h, conv):
    return (await client.get(f"/conversations/{conv}/messages", headers=h)).json()


def tok(h):
    return h["Authorization"].split(" ")[1]


async def test_send_image_with_caption_and_open_it_safely(client):
    admin, conv = await ready(client)
    r = await client.post(f"/conversations/{conv}/attachments", headers=admin,
                          files={"file": ("foto.png", PNG, "image/png")}, data={"caption": "Segue o documento"})
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["kind"] == "image" and m["body"] == "Segue o documento" and m["media_url"] == f"/media/{m['id']}"
    await process_batch(provider)
    assert provider.sent[-1]["kind"] == "image" and os.path.exists(provider.sent[-1]["path"])

    ok = await client.get(m["media_url"], params={"token": tok(admin)})
    assert ok.status_code == 200 and ok.content == PNG and ok.headers["content-type"] == "image/png"
    assert (await client.get(m["media_url"])).status_code == 401
    other = await register(client, "Outro", "o@o.com")
    assert (await client.get(m["media_url"], params={"token": tok(other)})).status_code == 404
    convs = (await client.get("/conversations", headers=admin)).json()
    assert convs[0]["last_preview"].startswith("📷 Foto")


async def test_rejects_files_whatsapp_does_not_accept(client):
    admin, conv = await ready(client)
    bad = await client.post(f"/conversations/{conv}/attachments", headers=admin, files={"file": ("x.exe", b"MZ", "application/x-msdownload")})
    assert bad.status_code == 422 and "não aceito" in bad.json()["detail"]
    big = await client.post(f"/conversations/{conv}/attachments", headers=admin,
                            files={"file": ("g.png", b"0" * (5 * 1024 * 1024 + 1), "image/png")})
    assert big.status_code == 422 and "5 MB" in big.json()["detail"]


@pytest.mark.skipif(media.ffmpeg_path() is None, reason="FFmpeg não instalado")
async def test_recorded_audio_becomes_whatsapp_voice_note(client, tmp_path):
    admin, conv = await ready(client)
    src = tmp_path / "gravacao.webm"
    subprocess.run([media.ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-c:a", "libopus", str(src)], check=True)
    r = await client.post(f"/conversations/{conv}/attachments", headers=admin,
                          files={"file": ("gravacao.webm", src.read_bytes(), "audio/webm")}, data={"voice": "true"})
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["kind"] == "audio" and m["voice"] is True and m["media_mime"] == "audio/ogg"
    async with SessionLocal() as s:
        stored = await s.get(Message, m["id"])
        assert stored.media_path.endswith(".ogg") and open(media.abs_path(stored.media_path), "rb").read(4) == b"OggS"
    await process_batch(provider)
    assert provider.sent[-1]["kind"] == "audio" and provider.sent[-1]["voice"] is True


async def test_reply_quoting_the_customer_message(client):
    admin, conv = await ready(client)
    customer = [m for m in await msgs(client, admin, conv) if m["direction"] == "in"][0]
    r = await client.post(f"/conversations/{conv}/messages", headers=admin, json={"body": "Tudo ótimo!", "reply_to_id": customer["id"]})
    assert r.json()["reply_to_id"] == customer["id"]
    await process_batch(provider)
    async with SessionLocal() as s:
        wamid = (await s.get(Message, customer["id"])).wamid
    assert provider.sent[-1]["reply"] == {"id": wamid, "from_me": False, "text": "Oi, tudo bem?"}
    other_conv_msg = await client.post(f"/conversations/{conv}/messages", headers=admin, json={"body": "x", "reply_to_id": "nao-existe"})
    assert other_conv_msg.status_code == 422


async def test_edit_within_15_minutes_only_by_the_sender(client):
    admin, conv = await ready(client)
    m = (await client.post(f"/conversations/{conv}/messages", headers=admin, json={"body": "Consulta amanha 10h"})).json()
    await process_batch(provider)
    r = await client.patch(f"/messages/{m['id']}", headers=admin, json={"body": "Consulta amanhã às 10h"})
    assert r.status_code == 200 and r.json()["edited"] and r.json()["body"] == "Consulta amanhã às 10h"
    assert provider.actions[-1][0] == "edit"

    agent = await new_agent(client, admin, "ag@a.com")
    assert (await client.patch(f"/messages/{m['id']}", headers=agent, json={"body": "x"})).status_code == 403
    async with SessionLocal() as s:
        msg = await s.get(Message, m["id"])
        msg.created_at = utcnow() - timedelta(minutes=16)
        await s.commit()
    late = await client.patch(f"/messages/{m['id']}", headers=admin, json={"body": "y"})
    assert late.status_code == 409 and "15 minutos" in late.json()["detail"]


async def test_delete_for_everyone_and_cancel_unsent(client):
    admin, conv = await ready(client)
    sent = (await client.post(f"/conversations/{conv}/messages", headers=admin, json={"body": "mandei errado"})).json()
    await process_batch(provider)
    r = await client.delete(f"/messages/{sent['id']}", headers=admin)
    assert r.json()["deleted"] and r.json()["body"] == "" and provider.actions[-1][0] == "revoke"

    queued = (await client.post(f"/conversations/{conv}/messages", headers=admin, json={"body": "ainda na fila"})).json()
    await client.delete(f"/messages/{queued['id']}", headers=admin)
    before = len(provider.sent)
    await process_batch(provider)
    assert len(provider.sent) == before  # cancelada antes de sair: nunca é enviada
    customer = [m for m in await msgs(client, admin, conv) if m["direction"] == "in"][0]
    assert (await client.delete(f"/messages/{customer['id']}", headers=admin)).status_code == 403  # não apaga a do cliente


async def test_reactions(client):
    admin, conv = await ready(client)
    customer = [m for m in await msgs(client, admin, conv) if m["direction"] == "in"][0]
    r = await client.post(f"/messages/{customer['id']}/react", headers=admin, json={"emoji": "👍"})
    me = (await client.get("/me", headers=admin)).json()["id"]
    assert r.json()["reactions"] == {me: "👍"} and provider.actions[-1] == ("react", provider.actions[-1][1], "👍")
    assert (await client.post(f"/messages/{customer['id']}/react", headers=admin, json={"emoji": "💩"})).status_code == 422
    r = await client.post(f"/messages/{customer['id']}/react", headers=admin, json={"emoji": ""})
    assert r.json()["reactions"] == {}


async def test_customer_sends_photo_then_edits_reacts_and_deletes(client):
    admin, conv = await ready(client)
    r = await client.post("/dev/inbound", headers=admin, json={"phone": "5511999990000", "text": "olha a carta do INSS",
                                                               "media_b64": base64.b64encode(PNG).decode(), "media_mime": "image/png"})
    assert r.status_code == 201
    photo = [m for m in await msgs(client, admin, conv) if m["direction"] == "in"][-1]
    assert photo["kind"] == "image" and photo["media_url"]
    first = [m for m in await msgs(client, admin, conv) if m["direction"] == "in"][0]
    await client.post("/dev/customer-action", headers=admin, json={"message_id": first["id"], "action": "edit", "text": "Oi, bom dia!"})
    await client.post("/dev/customer-action", headers=admin, json={"message_id": photo["id"], "action": "react", "emoji": "🙏"})
    await client.post("/dev/customer-action", headers=admin, json={"message_id": photo["id"], "action": "revoke"})
    after = {m["id"]: m for m in await msgs(client, admin, conv)}
    assert after[first["id"]]["body"] == "Oi, bom dia!" and after[first["id"]]["edited"]
    assert after[photo["id"]]["deleted"] and after[photo["id"]]["media_url"] is None


async def test_meta_channel_cannot_edit_or_delete_and_says_so(client, monkeypatch):
    admin = await register(client)

    async def ok(pnid, token):
        return {"display_phone_number": "+5527988887777"}

    async def fake_send(channel, to, payload):
        return "wamid.META1"

    monkeypatch.setattr(cloud, "check_credentials", ok)
    monkeypatch.setattr(cloud, "send", fake_send)
    await client.post("/channels/connect-meta", headers=admin, json={"phone": "27988887777", "phone_number_id": "1234567890", "access_token": "EAAB" + "x" * 40})
    payload = {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "1234567890"},
               "messages": [{"from": "5511955554444", "id": "wamid.IN1", "type": "text", "text": {"body": "oi"}}]}}]}]}
    await client.post("/webhooks/whatsapp", json=payload)
    conv = (await client.get("/conversations", headers=admin)).json()[0]["id"]
    await client.post(f"/conversations/{conv}/claim", headers=admin)
    m = (await client.post(f"/conversations/{conv}/messages", headers=admin, json={"body": "texto"})).json()
    await process_batch(provider)
    r = await client.patch(f"/messages/{m['id']}", headers=admin, json={"body": "texto corrigido"})
    assert r.json()["edited"] and "não permite editar" in r.json()["remote_note"]
    r = await client.delete(f"/messages/{m['id']}", headers=admin)
    assert r.json()["deleted"] and "não permite apagar" in r.json()["remote_note"]


async def test_bridge_customer_media_and_revoke(client, monkeypatch):
    from app.channels import bridge as bridge_mod
    from app.routers import auth as auth_mod

    async def fake_req(method, path, json=None, timeout=15):
        return {"status": "connected", "qr": None, "phone": "5527900000000"} if method == "GET" else {"status": "starting"}

    monkeypatch.setattr(bridge_mod, "request", fake_req)
    monkeypatch.setattr(auth_mod, "bridge_request", fake_req)
    admin = await register(client)
    ch = (await client.post("/channels/qr/start", headers=admin)).json()
    secret = {"x-bridge-secret": "segredo-da-ponte-so-para-testes"}
    body = {"channel_id": ch["id"], "jid": "5511900001111@s.whatsapp.net", "phone": "5511900001111", "wamid": "B1",
            "text": "", "media_b64": base64.b64encode(PNG).decode(), "media_mime": "image/png"}
    assert (await client.post("/bridge/inbound", headers=secret, json=body)).json() == {"created": True}
    conv = (await client.get("/conversations", headers=admin)).json()[0]["id"]
    m = (await msgs(client, admin, conv))[0]
    assert m["kind"] == "image"
    assert (await client.post("/bridge/update", headers=secret, json={"channel_id": ch["id"], "wamid": "B1", "action": "revoke"})).json() == {"applied": True}
    assert (await msgs(client, admin, conv))[0]["deleted"]
