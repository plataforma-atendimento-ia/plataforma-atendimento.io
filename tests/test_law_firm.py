"""Escritório de advocacia: encaminhamento pela IA, responsável direto, consultas, filas por setor e Google Agenda."""
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app import agenda
from app import google_calendar as gcal
from app.ai import DeptInfo, SimulatedLegalAI
from app.ai.claude import ClaudeLegalAI
from app.ai.knowledge import DEFAULT_DEPARTMENTS, DISCLAIMER
from app.channels import provider
from app.config import settings
from app.db import utcnow
from app.outbox import process_batch

from .conftest import register

PWD = "Demo-escritorio-2026"
DEPTS = [
    DeptInfo(slug=d["slug"], name=d["name"], description=d["description"],
             keywords=[k.strip() for k in d["keywords"].split(",")], needs_consult=d.get("needs_consult", False))
    for d in DEFAULT_DEPARTMENTS
]

# Mensagens como os clientes escrevem de verdade -> setor esperado
ROUTING_CASES = [
    ("Quero vender meu precatório", "precatorios"),
    ("tenho uma RPV para receber, quanto demora?", "precatorios"),
    ("a prefeitura me deve um dinheiro de processo antigo", "precatorios"),
    ("quero me aposentar, tenho 62 anos", "previdenciario"),
    ("o INSS negou meu auxílio doença", "previdenciario"),
    ("minha mãe tem 66 anos e nunca contribuiu, ela tem direito ao BPC?", "previdenciario"),
    ("quero pedir pensão por morte do meu marido", "previdenciario"),
    ("fui demitido e não recebi a rescisão", "trabalhista"),
    ("a empresa não depositou meu FGTS", "trabalhista"),
    ("trabalhei sem carteira assinada por 3 anos", "trabalhista"),
    ("fiz muitas horas extras e nunca recebi", "trabalhista"),
    ("quero me divorciar", "familia"),
    ("o pai do meu filho não paga a pensão alimentícia", "familia"),
    ("meu pai faleceu e precisamos fazer o inventário", "familia"),
    ("quero a guarda do meu filho", "familia"),
    ("o banco fez um empréstimo consignado que eu não pedi", "civel"),
    ("meu nome está negativado no Serasa por uma dívida que já paguei", "civel"),
    ("o plano de saúde negou minha cirurgia", "civel"),
    ("comprei um produto com defeito e a loja não quer trocar", "civel"),
    ("caí num golpe do pix", "civel"),
    ("a companhia aérea cancelou meu voo", "civel"),
    ("queria saber o andamento do meu processo", "geral"),
    # frases com palavras de dois setores: o assunto principal tem que vencer
    ("Quero me aposentar, trabalhei 30 anos de carteira assinada", "previdenciario"),
    ("quero me divorciar e o banco tomou meu carro", "familia"),
    ("tenho um precatório de uma ação trabalhista antiga", "precatorios"),
]


@pytest.mark.parametrize("text,expected", ROUTING_CASES)
async def test_simulated_ai_routes_real_client_messages(text, expected):
    d = await SimulatedLegalAI().decide(text, [], DEPTS, "Escritório Teste")
    assert d.department == expected and d.confidence >= 0.6, (text, d)


async def test_vague_message_is_not_guessed():
    for text in ["preciso de ajuda", "oi", "bom dia", "tenho um problema"]:
        d = await SimulatedLegalAI().decide(text, [], DEPTS, "X")
        assert d.department is None, text


async def test_message_that_fits_two_sectors_is_not_routed_blindly():
    # serve para trabalhista e previdenciário: confiança abaixo do mínimo, o assistente pergunta
    d = await SimulatedLegalAI().decide("fui demitido e quero saber se posso pedir o auxílio doença", [], DEPTS, "X")
    from app.assistant import MIN_CONFIDENCE

    assert d.confidence < MIN_CONFIDENCE


# ---------- helpers ----------
async def office(client):
    admin = await register(client, "Silva & Associados", automation=True)
    await client.post("/channels/test-connect", headers=admin, json={"phone": "5527900000001"})
    demo = (await client.post("/demo/law-firm", headers=admin)).json()
    people = {}
    for u in demo["users"]:
        r = await client.post("/auth/login", json={"email": u["email"], "password": PWD})
        people[u["name"].split(" ")[-2] if u["name"].startswith("Dr") else u["name"].split(" ")[0]] = {
            "Authorization": f"Bearer {r.json()['token']}", "id": r.json()["user"]["id"]
        }
    depts = {d["slug"]: d for d in (await client.get("/departments", headers=admin)).json()}
    return admin, people, depts


def h(person):
    return {"Authorization": person["Authorization"]}


async def say(client, admin, text, phone="5511955550000", name="Cliente"):
    r = await client.post("/dev/inbound", headers=admin, json={"phone": phone, "text": text, "name": name})
    assert r.status_code == 201, r.text
    await process_batch(provider)


async def conv_of(client, admin, phone="5511955550000"):
    for c in (await client.get("/conversations?view=all", headers=admin)).json():
        if c["contact"]["phone"] == phone:
            return c
    return None


async def bot_texts(client, admin, conv_id):
    msgs = (await client.get(f"/conversations/{conv_id}/messages", headers=admin)).json()
    return [m["body"] for m in msgs if m["direction"] == "out" and m["sender_user_id"] is None]


# ---------- fluxo do assistente ----------
async def test_demo_office_has_sectors_people_and_pedro_owns_precatorios(client):
    admin, people, depts = await office(client)
    assert set(depts) == {"geral", "civel", "previdenciario", "trabalhista", "familia", "precatorios"}
    assert depts["precatorios"]["owner_user_id"] == people["Pedro"]["id"]
    assert people["Pedro"]["id"] in depts["precatorios"]["member_ids"]


async def test_precatorio_goes_straight_to_pedro_without_scheduling(client):
    admin, people, depts = await office(client)
    await say(client, admin, "Oi, quero vender meu precatório")
    c = await conv_of(client, admin)
    assert c["assigned_to"] == people["Pedro"]["id"] and c["status"] == "open"
    assert c["department_id"] == depts["precatorios"]["id"] and c["routed_by"] == "ia"
    texts = await bot_texts(client, admin, c["id"])
    assert "Dr. Pedro Albuquerque" in texts[-1] and "horário" not in " ".join(texts)
    mine = (await client.get("/conversations?view=mine", headers=h(people["Pedro"]))).json()
    assert [x["id"] for x in mine] == [c["id"]]  # está na fila pessoal do Pedro


async def test_general_doubt_is_explained_in_plain_words(client):
    admin, people, depts = await office(client)
    await say(client, admin, "O que é RPV?")
    c = await conv_of(client, admin)
    texts = await bot_texts(client, admin, c["id"])
    joined = " ".join(texts)
    assert "valores menores" in joined and DISCLAIMER in joined
    assert c["assigned_to"] == people["Pedro"]["id"]  # RPV também é com o Pedro


async def test_retirement_case_offers_paid_consult_and_books_the_slot(client):
    admin, people, depts = await office(client)
    await say(client, admin, "Quero me aposentar, trabalhei 30 anos de carteira assinada", name="Maria")
    c = await conv_of(client, admin)
    texts = await bot_texts(client, admin, c["id"])
    assert "consulta" in texts[-1] and "R$ 200,00" in texts[-1] and "1)" in texts[-1] and "Dra. Ana Ribeiro" in texts[-1]
    assert c["ai_state"] == "awaiting_slot" and c["department_id"] == depts["previdenciario"]["id"]

    await say(client, admin, "2", name="Maria")
    c = await conv_of(client, admin)
    assert c["ai_state"] == "done" and c["status"] == "queue"  # segue na fila do previdenciário
    assert "consulta marcada" in (await bot_texts(client, admin, c["id"]))[-1]
    ag = (await client.get("/agenda", headers=admin)).json()
    assert len(ag) == 1 and ag[0]["lawyer_id"] == people["Ana"]["id"] and ag[0]["price_cents"] == 20000
    assert ag[0]["contact"]["name"] == "Maria" and ag[0]["created_by"] == "ia"


async def test_invalid_slot_answer_asks_again_and_zero_hands_over(client):
    admin, people, depts = await office(client)
    await say(client, admin, "fui demitido e não recebi a rescisão")
    await say(client, admin, "talvez semana que vem")
    c = await conv_of(client, admin)
    assert "responda só com o número" in (await bot_texts(client, admin, c["id"]))[-1].lower()
    await say(client, admin, "0")
    c = await conv_of(client, admin)
    assert c["ai_state"] == "done" and (await client.get("/agenda", headers=admin)).json() == []


async def test_vague_message_shows_menu_and_number_routes(client):
    admin, people, depts = await office(client)
    await say(client, admin, "Oi")
    c = await conv_of(client, admin)
    assert c["ai_state"] == "triage" and c["department_id"] is None
    menu = (await bot_texts(client, admin, c["id"]))[-1]
    assert "3) Trabalhista" in menu
    await say(client, admin, "3")
    c = await conv_of(client, admin)
    assert c["department_id"] == depts["trabalhista"]["id"]


async def test_customer_asking_for_a_person_goes_to_general_queue(client):
    admin, people, depts = await office(client)
    await say(client, admin, "quero falar com um atendente")
    c = await conv_of(client, admin)
    assert c["department_id"] == depts["geral"]["id"] and c["status"] == "queue"
    assert "chamando alguém da nossa equipe" in (await bot_texts(client, admin, c["id"]))[-1]
    before = len(await bot_texts(client, admin, c["id"]))
    await say(client, admin, "alô?")
    assert len(await bot_texts(client, admin, c["id"])) == before  # depois de encaminhar, a IA não responde mais


# ---------- filas e permissões ----------
async def test_each_person_sees_only_their_sector_queues_and_their_own(client):
    admin, people, depts = await office(client)
    await say(client, admin, "a empresa não depositou meu FGTS", phone="5511900000001")
    await say(client, admin, "quero me divorciar", phone="5511900000002")
    trab = await conv_of(client, admin, "5511900000001")

    q = (await client.get("/queues", headers=h(people["Carla"]))).json()
    assert [x["name"] for x in q["sectors"]] == ["Trabalhista"] and q["sectors"][0]["waiting"] == 1

    julia = h(people["Júlia"])
    seen = [c["id"] for c in (await client.get("/conversations?view=all", headers=julia)).json()]
    assert trab["id"] not in seen
    assert (await client.get(f"/conversations?view=dept:{depts['trabalhista']['id']}", headers=julia)).status_code == 403
    assert (await client.post(f"/conversations/{trab['id']}/claim", headers=julia)).status_code == 403

    carla = h(people["Carla"])
    assert (await client.post(f"/conversations/{trab['id']}/claim", headers=carla)).status_code == 200
    mine = (await client.get("/conversations?view=mine", headers=carla)).json()
    assert [c["id"] for c in mine] == [trab["id"]]
    assert (await client.get(f"/conversations?view=dept:{depts['trabalhista']['id']}", headers=carla)).json() == []


async def test_transfer_to_sector_with_owner_lands_with_pedro(client):
    admin, people, depts = await office(client)
    await say(client, admin, "meu nome está negativado no Serasa")
    c = await conv_of(client, admin)
    bruno = h(people["Bruno"])
    assert (await client.post(f"/conversations/{c['id']}/claim", headers=bruno)).status_code == 200
    r = await client.post(f"/conversations/{c['id']}/transfer", headers=bruno, json={"department_id": depts["precatorios"]["id"]})
    assert r.json()["assigned_to"] == people["Pedro"]["id"] and r.json()["routed_by"] == "usuario"


async def test_admin_manages_sectors_agents_cannot(client):
    admin, people, depts = await office(client)
    body = {"name": "Tributário", "description": "Impostos e dívidas com a Receita", "keywords": "imposto, receita federal, ir",
            "member_ids": [people["Bruno"]["id"]], "needs_consult": True, "consult_price_cents": 30000}
    assert (await client.post("/departments", headers=h(people["Bruno"]), json=body)).status_code == 403
    r = await client.post("/departments", headers=admin, json=body)
    assert r.status_code == 201 and r.json()["slug"] == "tributario" and r.json()["member_ids"] == [people["Bruno"]["id"]]
    other = await register(client, "Outro Escritório", "x@outro.com")
    other_id = (await client.get("/me", headers=other)).json()["id"]
    bad = {**body, "name": "Outro", "owner_user_id": other_id}
    assert (await client.post("/departments", headers=admin, json=bad)).status_code == 422
    assert (await client.delete(f"/departments/{depts['geral']['id']}", headers=admin)).status_code == 409


# ---------- agenda ----------
async def test_slots_skip_booked_and_google_busy_times(client, monkeypatch):
    admin, people, depts = await office(client)
    r = await client.get(f"/lawyers/{people['Ana']['id']}/slots?n=3", headers=admin)
    first = r.json()
    assert len(first) == 3
    start = first[0]["start"]
    await client.post("/appointments", headers=admin, json={"lawyer_id": people["Ana"]["id"], "start": start})
    after = (await client.get(f"/lawyers/{people['Ana']['id']}/slots?n=3", headers=admin)).json()
    assert start not in [x["start"] for x in after]
    again = await client.post("/appointments", headers=admin, json={"lawyer_id": people["Ana"]["id"], "start": start})
    assert again.status_code == 409  # não deixa marcar duas consultas no mesmo horário

    # agenda Google conectada: compromissos dela também bloqueiam
    settings.google_client_id, settings.google_client_secret = "cid", "secret"
    try:
        from app.db import SessionLocal
        from app.models import GoogleCalendarLink

        async with SessionLocal() as s:
            s.add(GoogleCalendarLink(user_id=people["Ana"]["id"], tenant_id=(await client.get("/me", headers=admin)).json()["tenant_id"], refresh_token="rt"))
            await s.commit()
        from datetime import datetime

        blocked = datetime.fromisoformat(after[0]["start"])

        async def fake_busy(rt, cal, a, b):
            return [(blocked, blocked + timedelta(minutes=60))]

        created = {}

        async def fake_event(rt, cal, summary, desc, a, b):
            created.update(summary=summary, start=a)
            return "evt123"

        monkeypatch.setattr(gcal, "freebusy", fake_busy)
        monkeypatch.setattr(gcal, "create_event", fake_event)
        slots = (await client.get(f"/lawyers/{people['Ana']['id']}/slots?n=3", headers=admin)).json()
        assert after[0]["start"] not in [x["start"] for x in slots]
        r = await client.post("/appointments", headers=admin, json={"lawyer_id": people["Ana"]["id"], "start": slots[0]["start"]})
        assert r.json()["in_google"] is True and created["start"] == datetime.fromisoformat(slots[0]["start"])
    finally:
        settings.google_client_id, settings.google_client_secret = "", ""


async def test_slots_follow_work_hours(client):
    admin, people, depts = await office(client)
    ana = people["Ana"]
    hours = {"2": ["14:00", "16:00"]}  # só quarta, 14h às 16h
    assert (await client.put(f"/users/{ana['id']}", headers=admin, json={"work_hours": hours})).status_code == 200
    slots = (await client.get(f"/lawyers/{ana['id']}/slots?n=6", headers=admin)).json()
    for sl in slots:
        local = agenda.to_local(__import__("datetime").datetime.fromisoformat(sl["start"]))
        assert local.weekday() == 2 and local.hour in (14, 15)
    bad = await client.put(f"/users/{ana['id']}", headers=admin, json={"work_hours": {"9": ["10:00", "11:00"]}})
    assert bad.status_code == 422
    # atendente pode mudar o próprio horário, mas não o de outra pessoa
    assert (await client.put(f"/users/{ana['id']}", headers=h(ana), json={"work_hours": hours})).status_code == 200
    assert (await client.put(f"/users/{people['Pedro']['id']}", headers=h(ana), json={"work_hours": hours})).status_code == 403


async def test_google_connect_flow(client, monkeypatch):
    admin, people, depts = await office(client)
    assert (await client.get("/google/connect", headers=admin)).status_code == 503  # sem credenciais configuradas
    settings.google_client_id, settings.google_client_secret = "cid", "secret"
    try:
        url = (await client.get("/google/connect", headers=admin)).json()["url"]
        assert url.startswith("https://accounts.google.com/") and "access_type=offline" in url
        state = url.split("state=")[1]

        async def fake_exchange(code):
            assert code == "abc"
            return {"refresh_token": "rt-1", "access_token": "at", "email": "advogado@gmail.com"}

        monkeypatch.setattr(gcal, "exchange_code", fake_exchange)
        r = await client.get(f"/google/callback?code=abc&state={state}", follow_redirects=False)
        assert r.status_code in (302, 307) and "google=ok" in r.headers["location"]
        st = (await client.get("/google/status", headers=admin)).json()
        assert st["connected"] is True and st["email"] == "advogado@gmail.com"
        bad = await client.get("/google/callback?code=abc&state=forjado", follow_redirects=False)
        assert "google=erro" in bad.headers["location"]
    finally:
        settings.google_client_id, settings.google_client_secret = "", ""


# ---------- IA real (Claude) com cliente falso ----------
class FakeClaude:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.calls = payload, error, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    async def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])


async def test_claude_decision_is_parsed_and_request_is_well_formed():
    fake = FakeClaude({"intencao": "duvida", "setor": "precatorios", "confianca": 0.93,
                       "explicacao": "É uma dívida do governo...", "resposta": "Precatório é...", "resumo": "Quer entender precatório"})
    ai = ClaudeLegalAI("chave", fallback=SimulatedLegalAI(), client=fake)
    d = await ai.decide("o que é precatório?", [{"direction": "out", "body": "Olá!"}], DEPTS, "Silva")
    assert (d.source, d.intent, d.department, d.confidence) == ("claude", "duvida", "precatorios", 0.93)
    kw = fake.calls[0]
    assert kw["model"] == settings.anthropic_model and kw["output_config"]["effort"] == "low"
    assert kw["output_config"]["format"]["schema"]["properties"]["setor"]["enum"][-1] == "indefinido"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"} and "OAB" in kw["system"][0]["text"]
    assert kw["messages"][0]["role"] == "user"  # histórico começando pelo escritório é descartado


async def test_claude_failure_or_bad_output_falls_back_to_simulated():
    ai = ClaudeLegalAI("chave", fallback=SimulatedLegalAI(), client=FakeClaude(error=RuntimeError("timeout")))
    d = await ai.decide("quero vender meu precatório", [], DEPTS, "Silva")
    assert d.source == "simulada" and d.department == "precatorios"
    ai = ClaudeLegalAI("chave", fallback=SimulatedLegalAI(), client=FakeClaude({"intencao": "inventada"}))
    assert (await ai.decide("quero me divorciar", [], DEPTS, "Silva")).department == "familia"
