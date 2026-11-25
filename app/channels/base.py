from typing import Protocol

from ..models import Channel


class ChannelError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class ChannelProvider(Protocol):
    """Abstração de canal. Instagram/webchat/e-mail entram implementando esta interface."""

    async def send_text(self, channel: Channel, to: str, body: str, idempotency_key: str) -> str:
        """Envia texto e devolve o id externo (wamid). Levanta ChannelError em falha."""
        ...
