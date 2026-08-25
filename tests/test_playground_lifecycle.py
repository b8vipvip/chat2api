from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.playground_lifecycle_patch import install_playground_lifecycle_patch
from app import request_stall_patch
from app.request_stall_patch import install_request_stall_patch
from app.test_runs import TestRunStore as _TestRunStore
from app.v21_patch import install_v21_patch


ROOT = Path(__file__).resolve().parents[1]


def settings(tmp_path: Path) -> Settings:
    return Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )


def admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer master-key"}


def make_app(tmp_path: Path):
    app = create_app(settings(tmp_path))
    install_v21_patch(app)
    install_request_stall_patch(app)
    install_playground_lifecycle_patch(app)
    return app


def create_key(client: TestClient) -> dict:
    response = client.post(
        "/api/admin/keys",
        headers=admin_headers(),
        json={"name": "Playground Key"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def pair(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/api/extensions/register",
        headers={"X-Pairing-Code": "pair-code"},
        json={"name": "Chrome", "version": "0.8.1"},
    )
    assert response.status_code == 200, response.text
    value = response.json()
    return value["client_id"], value["token"]


def start_text_run(client: TestClient, key_id: str) -> dict:
    response = client.post(
        "/api/admin/playground/runs",
        headers=admin_headers(),
        json={
            "test_type": "text",
            "model": "gpt-5.5",
            "reasoning_effort": "medium",
            "api_key_id": key_id,
            "files": [],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["run"]


def wait_for_run(client: TestClient, run_id: str, status: str, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/admin/tests/{run_id}", headers=admin_headers())
        assert response.status_code == 200, response.text
        last = response.json()
        if last.get("status") == status:
            return last
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not become {status}: {last}")


def wait_for_capacity(app, client_id: str, used: int, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if app.state.broker.capacity_snapshot(client_id)["used_units"] == used:
            return
        time.sleep(0.01)
    raise AssertionError(app.state.broker.capacity_snapshot(client_id))


def test_running_run_is_persisted_immediately_and_survives_store_reload(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        key = create_key(client)
        client_id, token = pair(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()
            run = start_text_run(client, key["key"]["key_id"])
            assert run["status"] == "running"
            assert run["request_id"].startswith("req_")
            listing = client.get("/api/admin/tests", headers=admin_headers()).json()["data"]
            assert listing[0]["run_id"] == run["run_id"]
            assert listing[0]["status"] == "running"
            request = websocket.receive_json()
            assert request["request_id"] == run["request_id"]
            request_rows = client.get("/api/admin/requests", headers=admin_headers()).json()["data"]
            assert request_rows[0]["request_id"] == run["request_id"]
            assert request_rows[0]["status"] == "running"

            reloaded_while_running = _TestRunStore(tmp_path)
            asyncio.run(reloaded_while_running.load())
            restored_running = reloaded_while_running.get(run["run_id"])
            assert restored_running is not None
            assert restored_running["status"] == "running"
            assert key["token"] not in (tmp_path / "test_runs.jsonl").read_text(encoding="utf-8")
            assert key["token"] not in (tmp_path / "request_history.jsonl").read_text(encoding="utf-8")

            websocket.send_json(
                {"type": "chat.error", "request_id": request["request_id"], "error": "test cleanup"}
            )
            wait_for_run(client, run["run_id"], "failed")

    reloaded = _TestRunStore(tmp_path)
    asyncio.run(reloaded.load())
    restored = reloaded.get(run["run_id"])
    assert restored is not None
    assert restored["status"] == "failed"


def test_playground_completion_updates_one_request_and_releases_capacity(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    create_calls: list[str] = []
    original_create = app.state.broker.create

    async def counted_create(request_id: str, client_id: str):
        create_calls.append(request_id)
        return await original_create(request_id, client_id)

    app.state.broker.create = counted_create
    with TestClient(app) as client:
        key = create_key(client)
        client_id, token = pair(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()
            run = start_text_run(client, key["key"]["key_id"])
            message = websocket.receive_json()
            wait_for_capacity(app, client_id, 1)
            websocket.send_json({"type": "chat.started", "request_id": message["request_id"]})
            websocket.send_json(
                {"type": "chat.delta", "request_id": message["request_id"], "delta": "chat2api 文本测试成功"}
            )
            websocket.send_json(
                {
                    "type": "chat.completed",
                    "request_id": message["request_id"],
                    "text": "chat2api 文本测试成功",
                }
            )
            completed = wait_for_run(client, run["run_id"], "passed")
            wait_for_capacity(app, client_id, 0)

    assert create_calls == [run["request_id"]]
    assert completed["request_ids"] == [run["request_id"]]
    assert completed["results"][0]["request_id"] == run["request_id"]
    request_row = app.state.telemetry.get(run["request_id"])
    assert request_row is not None
    assert request_row["status"] == "completed"
    assert run["request_id"] not in app.state.broker.requests


def test_playground_failure_is_durable_and_releases_capacity(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        key = create_key(client)
        client_id, token = pair(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()
            run = start_text_run(client, key["key"]["key_id"])
            message = websocket.receive_json()
            websocket.send_json(
                {"type": "chat.error", "request_id": message["request_id"], "error": "browser failed"}
            )
            failed = wait_for_run(client, run["run_id"], "failed")
            wait_for_capacity(app, client_id, 0)
    assert failed["error"] == "browser failed"
    assert app.state.telemetry.get(run["request_id"])["status"] == "error"


def test_playground_generation_stall_marks_both_histories_and_releases_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(request_stall_patch, "DISPATCH_ACK_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(request_stall_patch, "SUBMIT_ACK_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(request_stall_patch, "POST_SUBMIT_START_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(request_stall_patch, "GENERATION_ACTIVITY_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(request_stall_patch, "ORPHAN_RELEASE_GRACE_SECONDS", 0.02)
    app = make_app(tmp_path)
    with TestClient(app) as client:
        key = create_key(client)
        client_id, token = pair(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()
            run = start_text_run(client, key["key"]["key_id"])
            request = websocket.receive_json()
            websocket.send_json({"type": "chat.started", "request_id": request["request_id"]})
            stalled = wait_for_run(client, run["run_id"], "stalled")
            wait_for_capacity(app, client_id, 0)

    request_row = app.state.telemetry.get(run["request_id"])
    assert stalled["results"][0]["status"] == "stalled"
    assert request_row is not None
    assert request_row["status"] == "stalled"
    assert request_row["diagnostics"]["generation_activity_watchdog_fired"] is True
    assert run["request_id"] not in app.state.broker.requests


def test_playground_cancel_marks_both_histories_and_releases_exact_request(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        key = create_key(client)
        client_id, token = pair(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()
            run = start_text_run(client, key["key"]["key_id"])
            request = websocket.receive_json()
            wait_for_capacity(app, client_id, 1)
            cancelled = client.post(
                f"/api/admin/playground/runs/{run['run_id']}/cancel",
                headers=admin_headers(),
            )
            assert cancelled.status_code == 200, cancelled.text
            assert cancelled.json()["run"]["status"] == "cancelled"
            cancel_message = websocket.receive_json()
            assert cancel_message == {"type": "chat.cancel", "request_id": request["request_id"]}
            final = wait_for_run(client, run["run_id"], "cancelled")
            wait_for_capacity(app, client_id, 0)
    assert final["results"][0]["status"] == "cancelled"
    assert app.state.telemetry.get(run["request_id"])["status"] == "cancelled"
    assert run["request_id"] not in app.state.broker.requests


def test_console_restores_running_state_and_prevents_duplicate_start() -> None:
    script = (ROOT / "app" / "admin_playground_lifecycle.js").read_text(encoding="utf-8")
    assert 'runs.find(run => RUNNING_STATUSES.has' in script
    assert 'if (starting || activeRunId) return' in script
    assert 'startButton.disabled = starting || running' in script
    assert '"/api/admin/playground/runs"' in script
    assert '/cancel' in script


def test_production_entry_installs_playground_after_request_stall_guard() -> None:
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "install_playground_lifecycle_patch(app)" in entry
    assert entry.index("install_request_stall_patch(app)") < entry.index("install_playground_lifecycle_patch(app)")
    assert entry.index("install_playground_lifecycle_patch(app)") < entry.index("install_runtime_contract(app)")
