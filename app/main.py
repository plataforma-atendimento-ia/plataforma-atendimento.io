import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .channels import provider
from .config import settings
from .db import init_models
from .outbox import run_worker
from .realtime import hub
from .routers import auth, inbox, webhooks
from .security import decode_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    task = asyncio.create_task(run_worker(provider)) if settings.run_worker else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Plataforma de Atendimento", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(inbox.router)
app.include_router(webhooks.router)


@app.get("/health")
async def health():
    return {"status": "ok", "provider": settings.channel_provider}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = ""):
    claims = decode_token(token)
    if not claims:
        await ws.close(code=4401)
        return
    await hub.connect(claims["tid"], ws)
    try:
        while True:
            await ws.receive_text()  # mantém a conexão; o cliente pode enviar "ping"
    except WebSocketDisconnect:
        hub.disconnect(claims["tid"], ws)


app.mount("/app", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/app/")
