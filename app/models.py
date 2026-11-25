import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, utcnow


def uid() -> str:
    return uuid.uuid4().hex


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    # starter | pro | business. Hoje só "compra de teste" (sem cobrança); Stripe entra na Fase 4.
    plan: Mapped[str | None] = mapped_column(String(20), nullable=True)
    automation_on: Mapped[bool] = mapped_column(default=True)  # roteiro automático de qualificação
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="agent")  # admin | agent
    password_hash: Mapped[str] = mapped_column(String(300))
    max_concurrent: Mapped[int] = mapped_column(Integer, default=5)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Channel(Base):
    __tablename__ = "channels"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    type: Mapped[str] = mapped_column(String(20), default="whatsapp")
    phone_number_id: Mapped[str] = mapped_column(String(64), unique=True)
    display_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)  # número mostrado ao usuário
    is_test: Mapped[bool] = mapped_column(default=False)  # canal simulado (sem Meta)
    via: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "qr" = ponte não oficial; vazio = Meta/teste
    link_status: Mapped[str | None] = mapped_column(String(14), nullable=True)  # pending|connected|disconnected (QR)
    # TODO(fase 4): criptografar em repouso
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("tenant_id", "phone"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    phone: Mapped[str] = mapped_column(String(32))
    wa_jid: Mapped[str | None] = mapped_column(String(64), nullable=True)  # endereço exato para responder (ponte QR)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Lead(Base):
    """Ficha de qualificação do contato, preenchida pelo roteiro automático (e editável pelo atendente depois)."""

    __tablename__ = "leads"
    __table_args__ = (UniqueConstraint("tenant_id", "contact_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), index=True)
    qual_step: Mapped[str | None] = mapped_column(String(12), nullable=True)  # name|interest|urgency|budget|done
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    interest: Mapped[str | None] = mapped_column(String(300), nullable=True)
    urgency: Mapped[str | None] = mapped_column(String(12), nullable=True)  # hoje|semana|sem_pressa
    budget: Mapped[str | None] = mapped_column(String(12), nullable=True)  # ate_500|500_2000|acima_2000
    score: Mapped[str | None] = mapped_column(String(8), nullable=True)  # quente|morno|frio
    stage: Mapped[str] = mapped_column(String(24), default="novo")  # novo|qualificando|qualificado|atendimento
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), index=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"))
    assigned_to: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="queue")  # queue | open | closed
    version: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_preview: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Message(Base):
    """Mensagem. Saída segue o padrão outbox: queued -> sending -> sent -> delivered -> read | failed."""

    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    direction: Mapped[str] = mapped_column(String(3))  # in | out
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), index=True)
    sender_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    wamid: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    idempotency_key: Mapped[str] = mapped_column(String(64), default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class InboundEvent(Base):
    """Garante idempotência da entrada: o mesmo wamid nunca é processado duas vezes."""

    __tablename__ = "inbound_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    wamid: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
