from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.registry import ClientRegistry


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(dict(payload))


def test_registry_binds_every_broker_request_family_including_legacy_dictation(tmp_path: Path) -> None:
    async def scenario() -> None:
        registry = ClientRegistry(tmp_path)
        socket = FakeSocket()
        registry.clients["worker-a"] = SimpleNamespace(connection_enabled=True)
        registry.sockets["worker-a"] = socket
        registry.chatgpt_routing_ready = lambda _client_id: True
        request_types = (
            "chat.request",
            "image.request",
            "voice.request",
            "voice.live.start",
            "voice.live.request",
            "dictation.request",
        )
        states = {
            f"req_socket_binding_{index}": SimpleNamespace(
                client_id="worker-a",
                dispatch_socket=None,
                terminal_event_type=None,
                completed_mono=None,
            )
            for index, _ in enumerate(request_types)
        }
        registry.request_broker = SimpleNamespace(requests=states)

        for index, request_type in enumerate(request_types):
            await registry.send("worker-a", {
                "type": request_type,
                "request_id": f"req_socket_binding_{index}",
            })

        assert all(state.dispatch_socket is socket for state in states.values())
        assert [item["type"] for item in socket.sent] == list(request_types)

        # Legacy dictation uses dictation.cancel for its follow-up control
        # message; it must remain bound to the original dispatch socket too.
        await registry.send("worker-a", {
            "type": "dictation.cancel",
            "request_id": "req_socket_binding_5",
        })
        assert socket.sent[-1]["type"] == "dictation.cancel"

        replacement = FakeSocket()
        registry.sockets["worker-a"] = replacement
        with pytest.raises(RuntimeError, match="Request Worker connection changed"):
            await registry.send("worker-a", {
                "type": "dictation.cancel",
                "request_id": "req_socket_binding_5",
            })
        assert replacement.sent == []

    asyncio.run(scenario())
