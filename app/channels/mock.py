from ..models import Channel
from .base import ChannelError


class MockProvider:
    """Canal simulado para desenvolvimento e testes. Permite injetar falhas."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.fail_next = 0
        self.fail_retryable = True
        self._by_key: dict[str, str] = {}

    async def send_text(self, channel: Channel, to: str, body: str, idempotency_key: str) -> str:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise ChannelError("falha simulada", retryable=self.fail_retryable)
        if idempotency_key in self._by_key:  # idempotente: não duplica
            return self._by_key[idempotency_key]
        wamid = f"mock.{idempotency_key}"
        self._by_key[idempotency_key] = wamid
        self.sent.append({"to": to, "body": body, "wamid": wamid})
        return wamid

    def reset(self) -> None:
        self.sent.clear()
        self._by_key.clear()
        self.fail_next = 0
        self.fail_retryable = True
