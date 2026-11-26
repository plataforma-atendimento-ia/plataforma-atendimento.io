from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./dev.db"
    jwt_secret: str = "dev-only-secret-change-me-in-production-0000"
    jwt_expire_minutes: int = 720

    # Modo de teste: libera compra de plano simulada, número de teste e cliente simulado.
    # Em produção com clientes reais, definir TEST_MODE=false.
    test_mode: bool = True

    # Outbox / worker
    run_worker: bool = True
    outbox_poll_seconds: float = 0.5
    outbox_max_attempts: int = 6
    outbox_backoff_seconds: list[int] = [1, 5, 30, 120, 600]
    sending_timeout_seconds: int = 120

    # Ponte NÃO OFICIAL por QR Code (pasta bridge/). Sem BRIDGE_SECRET o recurso fica desligado.
    bridge_url: str = "http://127.0.0.1:8788"
    bridge_secret: str = ""

    # IA (Claude). Vazio = IA simulada por regras (para testes e para começar sem custo).
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"

    # Google Agenda (OAuth). Criar credenciais em console.cloud.google.com (ver docs/AGENDA_GOOGLE.md).
    google_client_id: str = ""
    google_client_secret: str = ""
    public_base_url: str = "http://127.0.0.1:8000"  # usado no endereço de retorno do login do Google
    office_utc_offset_hours: int = -3  # horário de Brasília (sem horário de verão desde 2019)

    # WhatsApp Cloud API
    whatsapp_verify_token: str = "dev-verify"
    whatsapp_api_version: str = "v21.0"
    whatsapp_app_secret: str = ""  # se preenchido, valida X-Hub-Signature-256


settings = Settings()
