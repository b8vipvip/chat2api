from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.registry import ClientRegistry

ROOT = Path(__file__).resolve().parents[1]


def test_distinct_api_key_requests_are_bound_to_their_actual_dispatch_socket(tmp_path):
    async def scenario():
        registry = ClientRegistry(tmp_path)
        registry.clients["ext"] = SimpleNamespace(connection_enabled=True)
        registry.chatgpt_routing_ready = lambda _client: True
        old, new = SimpleNamespace(sent=[]), SimpleNamespace(sent=[])
        async def old_send(payload): old.sent.append(payload)
        async def new_send(payload): new.sent.append(payload)
        old.send_json, new.send_json = old_send, new_send
        states = {rid: SimpleNamespace(request_id=rid, client_id="ext") for rid in ("a", "b", "c")}
        registry.request_broker = SimpleNamespace(requests=states)
        registry.sockets["ext"] = old
        for rid in ("a", "b"):
            await registry.send("ext", {"type":"chat.request","request_id":rid})
        registry.sockets["ext"] = new
        await registry.send("ext", {"type":"chat.request","request_id":"c"})
        assert states["a"].dispatch_socket is old
        assert states["b"].dispatch_socket is old
        assert states["c"].dispatch_socket is new
        assert [x["request_id"] for x in old.sent] == ["a","b"]
        assert [x["request_id"] for x in new.sent] == ["c"]
    asyncio.run(scenario())


def test_ws_disconnect_fans_out_to_origin_socket_not_single_legacy_request():
    source = (ROOT/"app"/"main.py").read_text()
    assert "lost_requests = [" in source
    assert 'getattr(state, "dispatch_socket", None) is websocket' in source
    assert "await asyncio.gather(*(broker.publish(request_id" in source
    assert "active_request = broker.client_requests.get(client_id)" not in source
    assert "registry.request_broker = broker" in source
