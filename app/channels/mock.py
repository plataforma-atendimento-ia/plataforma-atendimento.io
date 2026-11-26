from ..models import Channel
from .base import ChannelError


class MockProvider:
    """Canal simulado para desenvolvimento e testes. Permite injetar falhas e registra tudo o que foi feito."""

    def __init__(self) -> None:
        self.reset()

    async def send(self, channel: Channel, to: str, payload: dict) -> str:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise ChannelError("falha simulada", retryable=self.fail_retryable)
        key = payload.get("key") or ""
        if key in self._by_key:  # idempotente: não duplica
            return self._by_key[key]
        wamid = f"mock.{key}"
        self._by_key[key] = wamid
        self.sent.append({"to": to, "body": payload.get("text", ""), "wamid": wamid, "kind": payload.get("kind", "text"),
                          "voice": payload.get("voice", False), "reply": payload.get("reply"), "path": payload.get("path")})
        return wamid

    async def send_text(self, channel: Channel, to: str, body: str, idempotency_key: str) -> str:
        return await self.send(channel, to, {"kind": "text", "text": body, "key": idempotency_key})

    async def edit(self, channel, to, wamid, text):
        self.actions.append(("edit", wamid, text))

    async def revoke(self, channel, to, wamid):
        self.actions.append(("revoke", wamid))

    async def react(self, channel, to, wamid, emoji, from_me):
        self.actions.append(("react", wamid, emoji))

    def reset(self) -> None:
        self.sent: list[dict] = []
        self.actions: list[tuple] = []
        self._by_key: dict[str, str] = {}
        self.fail_next = 0
        self.fail_retryable = True
