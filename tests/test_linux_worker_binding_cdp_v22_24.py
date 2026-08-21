import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import linux_worker_remote_login as remote  # noqa: E402


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return json.dumps(self.payload).encode("utf-8")


class _Socket:
    def __init__(self):
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def send(self, value):
        self.sent.append(value)

    def recv(self, timeout=None):
        assert timeout is not None and timeout > 0
        return json.dumps({"id": 1, "result": {"frameId": "frame-1"}})


def test_worker_binding_creates_blank_target_then_sends_secret_only_over_loopback_cdp_websocket(monkeypatch):
    ticket = "wbind_test-secret-123"
    server = "https://chat2api.example"
    opened = []
    socket = _Socket()

    def fake_urlopen(request, timeout):
        opened.append((request.full_url, request.method, timeout))
        assert ticket not in request.full_url
        assert server not in request.full_url
        return _Response({
            "id": "target-1",
            "url": "about:blank",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/target-1",
        })

    def fake_connect(url, **kwargs):
        assert url == "ws://127.0.0.1:9222/devtools/page/target-1"
        assert kwargs["open_timeout"] == 4
        assert kwargs["close_timeout"] == 1
        return socket

    monkeypatch.setattr(remote, "urlopen", fake_urlopen)
    monkeypatch.setattr(remote, "websocket_connect", fake_connect)
    with remote.ACTION_LOCK:
        remote.SESSION.close()

    result = remote.inject_worker_binding(ticket, server)

    assert result == {"ok": True, "method": "cdp-page-navigate", "target_id": "target-1"}
    assert opened == [("http://127.0.0.1:9222/json/new?about:blank", "PUT", 4)]
    assert len(socket.sent) == 1
    command = json.loads(socket.sent[0])
    assert command["method"] == "Page.navigate"
    assert command["params"]["url"].startswith(f"about:blank#chat2api-worker-bind={ticket}&")
    assert "chat2api-server=https%3A%2F%2Fchat2api.example" in command["params"]["url"]


def test_worker_binding_rejects_non_loopback_debugger_websocket(monkeypatch):
    def fake_urlopen(_request, _timeout):
        return _Response({
            "id": "target-1",
            "webSocketDebuggerUrl": "ws://198.51.100.10:9222/devtools/page/target-1",
        })

    monkeypatch.setattr(remote, "urlopen", fake_urlopen)
    monkeypatch.setattr(remote, "websocket_connect", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not connect")))
    with remote.ACTION_LOCK:
        remote.SESSION.close()

    result = remote.inject_worker_binding("wbind_test-secret-456", "https://chat2api.example")

    assert result["ok"] is False
    assert result["error"] == "binding_injection_failed"
    assert result["detail"] == "cdp_debugger_not_loopback"
