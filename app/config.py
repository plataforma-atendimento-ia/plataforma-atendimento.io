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

    # WhatsApp Cloud API
    whatsapp_verify_token: str = "dev-verify"
    whatsapp_api_version: str = "v21.0"
    whatsapp_app_secret: str = ""  # se preenchido, valida X-Hub-Signature-256


settings = Settings()
