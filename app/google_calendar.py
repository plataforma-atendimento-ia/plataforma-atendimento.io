"""Google Agenda pela API oficial (OAuth 2.0). Cada advogado conecta a própria agenda.

Precisa de GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET (Google Cloud Console > APIs e serviços > Credenciais >
ID do cliente OAuth do tipo "Aplicativo da Web"), com o endereço de retorno
{PUBLIC_BASE_URL}/google/callback cadastrado. Guia: docs/AGENDA_GOOGLE.md.
"""
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from .config import settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
API = "https://www.googleapis.com/calendar/v3"
SCOPES = "openid email https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.freebusy"


class GoogleError(Exception):
    pass


def enabled() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def redirect_uri() -> str:
    return settings.public_base_url.rstrip("/") + "/google/callback"


def auth_url(state: str) -> str:
    q = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",  # para receber o refresh token
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return AUTH_URL + "?" + urlencode(q)


async def _post_token(data: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(TOKEN_URL, data=data)
    if r.status_code != 200:
        raise GoogleError(f"Google recusou o token ({r.status_code}): {r.text[:200]}")
    return r.json()


async def exchange_code(code: str) -> dict:
    """Troca o código do login por tokens. Devolve {'refresh_token', 'access_token', 'email'}."""
    tok = await _post_token(
        {
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        }
    )
    if not tok.get("refresh_token"):
        raise GoogleError("o Google não enviou o refresh token; remova o acesso do app na conta Google e conecte de novo")
    email = None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(USERINFO_URL, headers={"Authorization": f"Bearer {tok['access_token']}"})
        email = r.json().get("email") if r.status_code == 200 else None
    except httpx.HTTPError:
        pass
    return {"refresh_token": tok["refresh_token"], "access_token": tok.get("access_token"), "email": email}


async def access_token(refresh_token: str) -> str:
    tok = await _post_token(
        {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    )
    return tok["access_token"]


def _iso(dt_utc_naive: datetime) -> str:
    return dt_utc_naive.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


async def freebusy(refresh_token: str, calendar_id: str, start_utc: datetime, end_utc: datetime) -> list[tuple[datetime, datetime]]:
    """Períodos ocupados da agenda (UTC, sem fuso)."""
    token = await access_token(refresh_token)
    body = {"timeMin": _iso(start_utc), "timeMax": _iso(end_utc), "items": [{"id": calendar_id}]}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{API}/freeBusy", json=body, headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        raise GoogleError(f"freeBusy falhou ({r.status_code})")
    busy = r.json().get("calendars", {}).get(calendar_id, {}).get("busy", [])
    return [(_parse(b["start"]), _parse(b["end"])) for b in busy]


async def create_event(refresh_token: str, calendar_id: str, summary: str, description: str, start_utc: datetime, end_utc: datetime) -> str:
    token = await access_token(refresh_token)
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": _iso(start_utc)},
        "end": {"dateTime": _iso(end_utc)},
        "reminders": {"useDefault": True},
    }
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{API}/calendars/{calendar_id}/events", json=body, headers={"Authorization": f"Bearer {token}"})
    if r.status_code not in (200, 201):
        raise GoogleError(f"não foi possível criar o evento ({r.status_code})")
    return r.json()["id"]
