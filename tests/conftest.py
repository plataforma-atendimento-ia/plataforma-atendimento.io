import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/test.db"
os.environ["RUN_WORKER"] = "false"
os.environ["CHANNEL_PROVIDER"] = "mock"

import httpx  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.channels import provider  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture
async def client():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    provider.reset()
    settings.outbox_backoff_seconds = [0]  # sem espera entre tentativas nos testes
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def register(client, company="Empresa", email="admin@a.com"):
    r = await client.post(
        "/auth/register", json={"company": company, "name": "Admin", "email": email, "password": "senha-forte-1"}
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def new_agent(client, admin_headers, email):
    r = await client.post(
        "/users", headers=admin_headers, json={"name": email, "email": email, "password": "senha-forte-1"}
    )
    assert r.status_code == 201, r.text
    r = await client.post("/auth/login", json={"email": email, "password": "senha-forte-1"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def inbound(client, headers, phone="5511999990000", text="oi", wamid=None):
    body = {"phone": phone, "text": text, "name": "Cliente"}
    if wamid:
        body["wamid"] = wamid
    r = await client.post("/dev/inbound", headers=headers, json=body)
    assert r.status_code == 201, r.text
    convs = (await client.get("/conversations", headers=headers)).json()
    return convs[0]["id"] if convs else None
