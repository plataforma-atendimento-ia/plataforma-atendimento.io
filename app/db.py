from datetime import datetime, timezone

from sqlalchemy import event, text  # noqa: F401
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


def _add_missing_columns(sync_conn) -> None:
    """Mini migração para SQLite de desenvolvimento: adiciona colunas novas em bancos já criados.
    Em produção (Postgres) usaremos Alembic na Fase 4."""
    if sync_conn.dialect.name != "sqlite":
        return
    from sqlalchemy import inspect

    insp = inspect(sync_conn)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in have:
                coltype = col.type.compile(sync_conn.dialect)
                default = " DEFAULT 0" if coltype.upper().startswith("BOOL") else ""
                sync_conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {coltype}{default}'))


async def init_models() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
