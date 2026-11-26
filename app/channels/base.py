from typing import Protocol

from ..models import Channel


class ChannelError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class NotSupported(ChannelError):
    """O canal não oferece este recurso (ex.: a API oficial da Meta não apaga nem edita mensagens)."""

    def __init__(self, message: str):
        super().__init__(message, retryable=False)


class ChannelProvider(Protocol):
    """Abstração de canal. Instagram/webchat/e-mail entram implementando esta interface.

    payload de send(): {"kind": text|image|video|audio|document, "text": str, "path": caminho absoluto do arquivo,
    "mime": str, "filename": str, "voice": bool, "key": chave de idempotência,
    "reply": {"id": wamid citado, "from_me": bool, "text": trecho} ou None}
    """

    async def send(self, channel: Channel, to: str, payload: dict) -> str: ...

    async def edit(self, channel: Channel, to: str, wamid: str, text: str) -> None: ...

    async def revoke(self, channel: Channel, to: str, wamid: str) -> None: ...

    async def react(self, channel: Channel, to: str, wamid: str, emoji: str, from_me: bool) -> None: ...
