"""Behavioral tests for Worker auto-login dispatch, binding and acknowledgments."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin_auth import SESSION_COOKIE
from app.worker_auto_login_v153_patch import install_worker_auto_login_v153_patch


class FakeRegistry:
    def __init__(self) -> None:
        self.clients = {
            "windows-1": SimpleNamespace(connection_enabled=True, metadata={}),
            "linux-bridge-1": SimpleNamespace(connection_enabled=True, metadata={"linux_worker_id": "linux-1"}),
        }
        self.sockets = {
            name: SimpleNamespace(url=SimpleNamespace(scheme="wss"), client=SimpleNamespace(host="203.0.113.2"))
            for name in self.clients
        }
        self.sent: list[tuple[str, dict]] = []
        self.touched: list[tuple[str, dict | None]] = []

    def online_client_ids(self) -> list[str]:
        return list(self.sockets)

    async def send(self, client_id: str, payload: dict) -> None:
        self.sent.append((client_id, payload.copy()))

    async def touch(self, client_id: str, metadata: dict | None = None) -> None:
        self.touched.append((client_id, metadata))


class FakeLinuxWorkers:
    def __init__(self) -> None:
        self.data = {"workers": {"linux-1": {"worker_id": "linux-1", "extension_client_id": "linux-bridge-1"}}}

    def worker_for_extension(self, client_id: str) -> dict | None:
        if client_id == "linux-bridge-1":
            return self.data["workers"]["linux-1"]
        return None


def make_client(data_dir: Path) -> tuple[TestClient, FastAPI, FakeRegistry, FakeLinuxWorkers]:
    app = FastAPI()
    registry = FakeRegistry()
    linux = FakeLinuxWorkers()
    app.state.settings = SimpleNamespace(data_dir=data_dir)
    app.state.registry = registry
    app.state.linux_workers = linux
    app.state.admin_sessions = SimpleNamespace(authenticate=lambda token: token == "test-admin")
    install_worker_auto_login_v153_patch(app)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, "test-admin")
    return client, app, registry, linux


def configured(client: TestClient, worker_id: str, *, totp: bool = True) -> None:
    payload = {
        "username": "example@example.com", "password": "dummy-test-password", "enabled": True,
    }
    if totp:
        payload["totp_secret"] = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    response = client.put(f"/api/admin/worker-login/{worker_id}", json=payload)
    assert response.status_code == 200, response.text


def test_worker_login_secure_dispatch_and_secret_isolation(tmp_path: Path) -> None:
    client, _app, registry, _linux = make_client(tmp_path)
    configured(client, "windows-1")
    response = client.post("/api/admin/worker-login/windows-1/trigger")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "automating"
    target, message = registry.sent[-1]
    assert target == "windows-1"
    assert message["type"] == "worker.login.start.v154"
    assert message["username"] == "example@example.com"
    assert message["password"] == "dummy-test-password"
    assert "totp_secret" not in message
    assert "code" not in message
    assert "password" not in str(response.json())
    assert "totp_secret" not in str(client.get("/api/admin/worker-login/windows-1").json())
    attempt_id = message["attempt_id"]

    asyncio.run(registry.touch("windows-1", {
        "worker_login_attempt_id": attempt_id,
        "worker_login_recovery_state": "waiting_otp",
        "worker_login_totp_request_attempt_id": attempt_id,
    }))
    assert registry.sent[-1][1]["type"] == "worker.login.totp.v154"
    assert len(registry.sent[-1][1]["code"]) == 6
    assert "totp_secret" not in registry.sent[-1][1]
    state = client.get("/api/admin/worker-login/windows-1").json()
    assert state["runtime"] == "waiting_otp"

    # Wrong attempt ID must never be able to fetch another code.
    count = len(registry.sent)
    asyncio.run(registry.touch("windows-1", {
        "worker_login_totp_request_attempt_id": "wrong-id",
    }))
    assert len(registry.sent) == count


def test_remote_plaintext_socket_gets_manual_fallback_without_credentials(tmp_path: Path) -> None:
    client, _app, registry, _linux = make_client(tmp_path)
    registry.sockets["windows-1"] = SimpleNamespace(
        url=SimpleNamespace(scheme="ws"), client=SimpleNamespace(host="203.0.113.10")
    )
    configured(client, "windows-1")
    response = client.post("/api/admin/worker-login/windows-1/trigger")
    assert response.status_code == 200, response.text
    assert response.json()["transport_secure"] is False
    message = registry.sent[-1][1]
    assert message["type"] == "worker.login.open.v153"
    assert "username" not in message
    assert "password" not in message

    # Even manual fallback stays correlated to its real login task.
    asyncio.run(registry.touch("windows-1", {
        "worker_login_attempt_id": message["attempt_id"],
        "worker_login_recovery_state": "manual_required",
    }))
    assert client.get("/api/admin/worker-login/windows-1").json()["runtime"] == "manual_required"


def test_linux_binding_is_authoritative_before_sending_secrets(tmp_path: Path) -> None:
    client, _app, registry, linux = make_client(tmp_path)
    configured(client, "linux-1")
    ok = client.post("/api/admin/worker-login/linux-1/trigger")
    assert ok.status_code == 200
    assert registry.sent[-1][0] == "linux-bridge-1"

    # After a binding change, no account credentials may go to the old Bridge.
    registry.sent.clear()
    linux.data["workers"]["linux-1"]["extension_client_id"] = "windows-1"
    bad = client.post("/api/admin/worker-login/linux-1/trigger")
    assert bad.status_code == 409
    assert not registry.sent


def test_recovery_ignores_duplicate_login_probes_and_stale_success(tmp_path: Path) -> None:
    client, _app, registry, _linux = make_client(tmp_path)
    configured(client, "windows-1")
    checked = int(time.time() * 1000) - 100
    login_required = {
        "chatgpt_login_state": "login_required",
        "chatgpt_login_confidence": "high",
        "chatgpt_login_checked_at_ms": checked,
    }
    asyncio.run(registry.touch("windows-1", login_required))
    asyncio.run(registry.touch("windows-1", login_required))
    assert registry.sent == []

    asyncio.run(registry.touch("windows-1", {**login_required, "chatgpt_login_checked_at_ms": checked + 1}))
    assert registry.sent[-1][1]["type"] == "worker.login.start.v154"
    attempt_id = registry.sent[-1][1]["attempt_id"]
    started = registry.sent[-1][1]["started_at_ms"]
    # An old ready observation must not confirm a new attempt.
    asyncio.run(registry.touch("windows-1", {
        "chatgpt_login_state": "ready",
        "chatgpt_login_composer_ready": True,
        "chatgpt_login_checked_at_ms": started - 1,
    }))
    assert client.get("/api/admin/worker-login/windows-1").json()["runtime"] == "automating"
    asyncio.run(registry.touch("windows-1", {
        "chatgpt_login_state": "ready",
        "chatgpt_login_composer_ready": True,
        "chatgpt_login_checked_at_ms": int(time.time() * 1000),
    }))
    assert client.get("/api/admin/worker-login/windows-1").json()["runtime"] == "logged_in"


def test_three_failed_logins_pause_automatic_retry_but_manual_trigger_is_allowed(tmp_path: Path) -> None:
    client, _app, registry, _linux = make_client(tmp_path)
    configured(client, "windows-1")
    for _ in range(3):
        result = client.post("/api/admin/worker-login/windows-1/trigger")
        assert result.status_code == 200, result.text
        attempt_id = registry.sent[-1][1]["attempt_id"]
        asyncio.run(registry.touch("windows-1", {
            "worker_login_attempt_id": attempt_id,
            "worker_login_recovery_state": "failed",
        }))
    assert client.get("/api/admin/worker-login/windows-1").json()["recent_failures"] == 3
    count = len(registry.sent)
    checked = int(time.time() * 1000) - 100
    for i in range(2):
        asyncio.run(registry.touch("windows-1", {
            "chatgpt_login_state": "login_required",
            "chatgpt_login_confidence": "high",
            "chatgpt_login_checked_at_ms": checked + i,
        }))
    assert len(registry.sent) == count
    assert client.get("/api/admin/worker-login/windows-1").json()["runtime"] == "paused"
    # A deliberate administrator-triggered retry remains possible.
    forced = client.post("/api/admin/worker-login/windows-1/trigger")
    assert forced.status_code == 200
    assert forced.json()["queued"] is True
    assert len(registry.sent) == count + 1


def test_missing_totp_uses_interactive_challenge_fallback(tmp_path: Path) -> None:
    client, _app, registry, _linux = make_client(tmp_path)
    configured(client, "windows-1", totp=False)
    result = client.post("/api/admin/worker-login/windows-1/trigger")
    assert result.status_code == 200
    attempt = registry.sent[-1][1]["attempt_id"]
    asyncio.run(registry.touch("windows-1", {
        "worker_login_attempt_id": attempt,
        "worker_login_recovery_state": "waiting_otp",
        "worker_login_totp_request_attempt_id": attempt,
    }))
    client_id, message = registry.sent[-1]
    assert client_id == "windows-1"
    assert message == {"type": "worker.login.totp_unavailable.v154", "attempt_id": attempt}
