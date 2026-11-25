from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .db import SessionLocal
from .models import User
from .security import decode_token

bearer = HTTPBearer(auto_error=False)


async def get_db():
    async with SessionLocal() as s:
        yield s


async def current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer), s: AsyncSession = Depends(get_db)
) -> User:
    claims = decode_token(cred.credentials) if cred else None
    if not claims:
        raise HTTPException(401, "não autenticado")
    s.sync_session.info["tenant_id"] = claims["tid"]  # usado pelo RLS no Postgres
    user = await s.get(User, claims["sub"])
    if user is None or user.tenant_id != claims["tid"]:
        raise HTTPException(401, "usuário inválido")
    return user


async def admin_only(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "somente administradores")
    return user
