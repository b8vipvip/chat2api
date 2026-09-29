from __future__ import annotations

from starlette.websockets import WebSocketDisconnect

from .registry import ClientRegistry

PATCH_ID = "registry-transport-health-v149"


if not getattr(ClientRegistry, "_chat2api_transport_health_v149", False):
    _original_send = ClientRegistry.send

    async def _send_with_transport_health(self: ClientRegistry, client_id: str, payload: dict) -> None:
        # Capture the exact socket before dispatch. A newer reconnect is allowed to
        # replace it while this await is in flight, so cleanup must be identity-safe.
        websocket = self.sockets.get(client_id)
        try:
            await _original_send(self, client_id, payload)
        except WebSocketDisconnect as exc:
            if websocket is not None:
                await self.detach(client_id, websocket)
            raise RuntimeError("Chrome extension is offline") from exc

    ClientRegistry.send = _send_with_transport_health  # type: ignore[assignment]
    ClientRegistry._chat2api_transport_health_v149 = True  # type: ignore[attr-defined]
