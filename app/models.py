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
    automation_on: Mapped[bool] = mapped_column(default=True)  # assistente automático ligado
    subscription_status: Mapped[str | None] = mapped_column(String(12), nullable=True)  # None|active (pagamento de teste)
    segment: Mapped[str | None] = mapped_column(String(20), nullable=True)  # nicho: "advocacia"
    onboarded: Mapped[bool] = mapped_column(default=False)  # já respondeu as perguntas do nicho
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
    title: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "Dr.", "Dra."
    position: Mapped[str | None] = mapped_column(String(80), nullable=True)  # cargo: "Advogado", "Atendimento"
    # horário de trabalho por dia da semana (0=segunda): {"0": ["09:00","18:00"], ...}
    work_hours: Mapped[str | None] = mapped_column(Text, nullable=True)
    slot_minutes: Mapped[int] = mapped_column(Integer, default=60)  # duração da consulta
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Department(Base):
    """Setor do escritório (ex.: Previdenciário). Cada setor tem a sua fila."""

    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("tenant_id", "slug"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    slug: Mapped[str] = mapped_column(String(40))
    color: Mapped[str] = mapped_column(String(9), default="#2b59c3")
    position: Mapped[int] = mapped_column(Integer, default=0)
    # o que o setor atende, em linguagem simples: a IA usa para encaminhar
    description: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[str] = mapped_column(Text, default="")  # palavras que o cliente costuma usar, separadas por vírgula
    # responsável direto: as conversas do setor vão direto para esta pessoa (ex.: Pedro em precatórios)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    needs_consult: Mapped[bool] = mapped_column(default=False)  # análise do caso exige consulta paga
    consult_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_default: Mapped[bool] = mapped_column(default=False)  # fila de atendimento geral
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DepartmentMember(Base):
    __tablename__ = "department_members"
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)


class Appointment(Base):
    """Consulta marcada (paga). Espelhada no Google Agenda do advogado quando ele conectou a agenda."""

    __tablename__ = "appointments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    lawyer_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"), nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # UTC
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(12), default="marcada")  # marcada|realizada|cancelada
    price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[str] = mapped_column(String(12), default="ia")  # ia|usuario
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class GoogleCalendarLink(Base):
    """Agenda Google conectada por um advogado (OAuth). TODO(fase 4): criptografar o refresh token."""

    __tablename__ = "google_links"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    calendar_id: Mapped[str] = mapped_column(String(200), default="primary")
    refresh_token: Mapped[str] = mapped_column(Text)
    connected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CustomPlanRequest(Base):
    """Pedido de plano personalizado (hoje só guarda o contato; no futuro leva ao atendimento comercial)."""

    __tablename__ = "custom_plan_requests"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    company: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # resumo do caso feito pela IA
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
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), nullable=True, index=True)
    routed_by: Mapped[str | None] = mapped_column(String(10), nullable=True)  # ia|usuario
    route_note: Mapped[str | None] = mapped_column(String(300), nullable=True)  # por que foi para este setor
    ai_state: Mapped[str | None] = mapped_column(String(16), nullable=True)  # triage|awaiting_slot|done
    ai_context: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: menu/horários oferecidos
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)  # nº do atendimento no escritório
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
