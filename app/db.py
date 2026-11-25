from datetime import datetime, timezone

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session

from .config import settings

_connect_args = {"timeout": 30} if settings.database_url.startswith("sqlite") else {}
engine = create_async_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """UTC ingênuo, consistente entre SQLite e Postgres."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@event.listens_for(Session, "after_begin")
def _set_tenant_for_rls(session, transaction, connection):
    """Em Postgres, informa o tenant à transação para as policies de RLS (db/rls_postgres.sql)."""
    tid = session.info.get("tenant_id")
    if tid and connection.dialect.name == "postgresql":
        connection.execute(text("select set_config('app.tenant_id', :t, true)"), {"t": tid})


async def init_models() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
