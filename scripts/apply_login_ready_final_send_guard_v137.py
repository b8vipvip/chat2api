from __future__ import annotations

from pathlib import Path


root = Path(__file__).resolve().parents[1]

registry_path = root / "app/registry.py"
text = registry_path.read_text(encoding="utf-8")
old = '''    async def send(self, client_id: str, payload: dict[str, Any]) -> None:
        client = self.clients.get(client_id)
        if not client or not client.connection_enabled:
            raise RuntimeError("Chrome extension connection is disabled")
        websocket = self.sockets.get(client_id)
'''
new = '''    async def send(self, client_id: str, payload: dict[str, Any]) -> None:
        client = self.clients.get(client_id)
        if not client or not client.connection_enabled:
            raise RuntimeError("Chrome extension connection is disabled")
        request_type = str(payload.get("type") or "") if isinstance(payload, dict) else ""
        if request_type in {
            "chat.request", "image.request", "voice.request", "voice.live.start", "voice.live.request",
        } and not self.chatgpt_routing_ready(client_id):
            raise RuntimeError(
                "Chrome extension is online but ChatGPT is not logged in or the composer is not ready"
            )
        websocket = self.sockets.get(client_id)
'''
if "request_type in {" not in text:
    if old not in text:
        raise SystemExit("registry.send patch anchor not found")
    registry_path.write_text(text.replace(old, new, 1), encoding="utf-8")

test_path = root / "tests/test_login_ready_admission_v137.py"
test = test_path.read_text(encoding="utf-8")
if "test_generation_send_is_guarded_at_the_last_transport_boundary" not in test:
    test = test.replace("from copy import deepcopy\n", "from copy import deepcopy\nimport asyncio\n", 1)
    test += r'''

class _Socket:
    def __init__(self) -> None:
        self.payloads = []

    async def send_json(self, payload) -> None:
        self.payloads.append(payload)


def test_generation_send_is_guarded_at_the_last_transport_boundary(tmp_path) -> None:
    registry = ClientRegistry(tmp_path)
    logged_out = _client("ext_logged_out", state="login_required", composer=False)
    registry.clients = {logged_out.client_id: logged_out}
    socket = _Socket()
    registry.sockets = {logged_out.client_id: socket}

    with pytest.raises(RuntimeError, match="ChatGPT is not logged in"):
        asyncio.run(registry.send("ext_logged_out", {"type": "chat.request", "request_id": "req_x"}))
    assert socket.payloads == []

    # Control traffic stays available so a transport-online Worker can still be
    # verified, remotely logged in and repaired while generation stays blocked.
    asyncio.run(registry.send("ext_logged_out", {"type": "window.manager.refresh"}))
    assert socket.payloads == [{"type": "window.manager.refresh"}]
'''
    test_path.write_text(test, encoding="utf-8")

print("final login send guard v137 prepared")
