from collections import defaultdict

from fastapi import WebSocket


class Hub:
    """Distribui eventos em tempo real por tenant via WebSocket (processo único no MVP;
    para várias instâncias, trocar por Redis pub/sub mantendo esta interface)."""

    def __init__(self) -> None:
        self._conns: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, tenant_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._conns[tenant_id].add(ws)

    def disconnect(self, tenant_id: str, ws: WebSocket) -> None:
        self._conns[tenant_id].discard(ws)

    async def publish(self, tenant_id: str, event: dict) -> None:
        for ws in list(self._conns.get(tenant_id, ())):
            try:
                await ws.send_json(event)
            except Exception:
                self._conns[tenant_id].discard(ws)


hub = Hub()
