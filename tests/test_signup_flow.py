"""Assinatura: plano -> conta -> pagamento (teste) -> perguntas do nicho -> plataforma configurada."""
from .conftest import register

LAW = {
    "segment": "advocacia",
    "areas": ["previdenciario", "precatorios", "trabalhista"],
    "consult_areas": ["previdenciario", "trabalhista"],
    "consult_price_cents": 25000,
    "work_hours": {"0": ["09:00", "17:00"], "2": ["13:00", "18:00"]},
}


async def test_full_signup_for_a_law_firm(client):
    admin = await register(client, "Moraes Advocacia")
    ob = (await client.get("/onboarding", headers=admin)).json()
    assert ob["subscription_status"] is None and ob["onboarded"] is False and ob["segment"] is None
    assert (await client.get("/departments", headers=admin)).json() == []  # nada configurado antes do nicho

    pay = await client.post("/billing/test-checkout", headers=admin, json={"plan": "pro"})
    assert pay.json()["subscription_status"] == "active" and pay.json()["charged"] is False

    r = await client.post("/onboarding/niche", headers=admin, json=LAW)
    assert r.status_code == 200, r.text
    depts = {d["slug"]: d for d in r.json()["departments"]}
    assert set(depts) == {"geral", "previdenciario", "precatorios", "trabalhista"}  # só as áreas escolhidas + geral
    assert depts["previdenciario"]["needs_consult"] and depts["previdenciario"]["consult_price_cents"] == 25000
    assert not depts["precatorios"]["needs_consult"] and depts["precatorios"]["consult_price_cents"] is None

    me = (await client.get("/me", headers=admin)).json()
    assert all(me["id"] in d["member_ids"] for d in depts.values())  # quem assinou já atende tudo
    team = {u["id"]: u for u in (await client.get("/team", headers=admin)).json()}
    assert team[me["id"]]["work_hours"] == LAW["work_hours"]
    ob = (await client.get("/onboarding", headers=admin)).json()
    assert ob["onboarded"] is True and ob["segment"] == "advocacia"


async def test_niche_can_be_answered_again_without_losing_sectors(client):
    admin = await register(client, "Moraes Advocacia")
    await client.post("/onboarding/niche", headers=admin, json=LAW)
    again = {**LAW, "areas": ["previdenciario", "familia"], "consult_areas": []}
    depts = {d["slug"] for d in (await client.post("/onboarding/niche", headers=admin, json=again)).json()["departments"]}
    assert depts == {"geral", "previdenciario", "familia"}  # precatórios e trabalhista foram arquivados, não apagados


async def test_niche_validation_and_permissions(client):
    admin = await register(client, "Moraes Advocacia")
    r = await client.post("/users", headers=admin, json={"name": "Ag", "email": "ag@a.com", "password": "Senha-forte-1"})
    tok = (await client.post("/auth/login", json={"email": "ag@a.com", "password": "Senha-forte-1"})).json()["token"]
    assert (await client.post("/onboarding/niche", headers={"Authorization": f"Bearer {tok}"}, json=LAW)).status_code == 403
    assert (await client.post("/onboarding/niche", headers=admin, json={**LAW, "segment": "clinica"})).status_code == 422
    assert (await client.post("/onboarding/niche", headers=admin, json={**LAW, "areas": ["astrologia"]})).status_code == 422
    assert (await client.post("/onboarding/niche", headers=admin, json={**LAW, "areas": []})).status_code == 422
    bad_hours = {**LAW, "work_hours": {"0": ["18:00", "09:00"]}}
    assert (await client.post("/onboarding/niche", headers=admin, json=bad_hours)).status_code == 422


async def test_custom_plan_request_is_public_and_validated(client):
    ok = await client.post("/sales/custom-plan", json={"name": "Rui", "company": "Rede Jurídica", "phone": "27999990000", "message": "10 unidades"})
    assert ok.status_code == 201
    assert (await client.post("/sales/custom-plan", json={"name": "R", "company": "X", "phone": "1"})).status_code == 422


BASE = {"company": "Moraes Advocacia", "name": "Beatriz", "email": "b@moraes.adv.br",
        "document": "11.222.333/0001-81", "phone": "(27) 99999-0000", "password": "Senha-forte-1"}


async def test_password_rules_on_signup(client):
    for weak, missing in [("Curta-1", "8 caracteres"), ("semmaiuscula-1", "maiúscula"), ("SemEspecial1", "especial")]:
        r = await client.post("/auth/register", json={**BASE, "password": weak})
        assert r.status_code == 422 and missing in r.json()["detail"], (weak, r.text)
    assert (await client.post("/auth/register", json=BASE)).status_code == 201


async def test_company_document_and_phone_are_validated(client):
    r = await client.post("/auth/register", json={**BASE, "document": "11.222.333/0001-82"})
    assert r.status_code == 422 and "CNPJ ou CPF" in r.json()["detail"]
    assert (await client.post("/auth/register", json={**BASE, "phone": "1234"})).status_code == 422
    ok_cpf = await client.post("/auth/register", json={**BASE, "document": "529.982.247-25", "email": "cpf@adv.br"})
    assert ok_cpf.status_code == 201  # advogado autônomo pode usar CPF


async def test_team_member_password_follows_the_same_rules(client):
    admin = await register(client)
    r = await client.post("/users", headers=admin, json={"name": "Ana", "email": "ana@a.com", "password": "fraca123"})
    assert r.status_code == 422 and "maiúscula" in r.json()["detail"]


async def test_old_accounts_can_still_log_in(client):
    # a regra vale para senhas novas; o login não recusa contas antigas
    assert (await client.post("/auth/register", json=BASE)).status_code == 201
    assert (await client.post("/auth/login", json={"email": BASE["email"], "password": BASE["password"]})).status_code == 200


async def test_survey_sets_consult_length_and_greeting_used_by_the_ai(client):
    from app.channels import provider
    from app.outbox import process_batch

    admin = await register(client, "Moraes Advocacia", automation=True)
    hello = "Bom dia! Você está falando com o Moraes Advocacia. Conte o que aconteceu."
    r = await client.post("/onboarding/niche", headers=admin, json={**LAW, "slot_minutes": 45, "greeting": hello})
    assert r.json()["greeting"] == hello
    me = (await client.get("/me", headers=admin)).json()
    team = {u["id"]: u for u in (await client.get("/team", headers=admin)).json()}
    assert team[me["id"]]["slot_minutes"] == 45
    await client.post("/channels/test-connect", headers=admin, json={"phone": "5527900000001"})
    await client.post("/dev/inbound", headers=admin, json={"phone": "5511911110000", "text": "Oi", "name": "Cli"})
    await process_batch(provider)
    assert provider.sent[0]["body"] == hello  # a primeira mensagem ao cliente é a do escritório
