from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.request_id_namespace_v136_patch import install_request_id_namespace_v136_patch


ROOT = Path(__file__).resolve().parents[1]


def test_route_recovery_vm_contract() -> None:
    result = subprocess.run(
        ["node", str(ROOT / "tests/qnbot_route_recovery_v136.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def _namespace_test_app() -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> dict[str, str]:
        return {"request_id": request.headers.get("x-chat2api-request-id", "")}

    @app.post("/v1/responses")
    async def responses(request: Request) -> dict[str, str]:
        return {"request_id": request.headers.get("x-chat2api-request-id", "")}

    install_request_id_namespace_v136_patch(app)
    return app


def test_cross_protocol_request_id_is_normalized_instead_of_400() -> None:
    client = TestClient(_namespace_test_app())
    response = client.post(
        "/v1/chat/completions",
        headers={"X-Chat2API-Request-ID": "resp_req_crossprotocol12345678"},
    )
    assert response.status_code == 200, response.text
    internal = response.json()["request_id"]
    assert internal.startswith("req_")
    assert not internal.startswith("resp_req_")
    assert response.headers["x-chat2api-internal-request-id"] == internal
    assert response.headers["x-chat2api-upstream-request-id"] == "resp_req_crossprotocol12345678"
    assert response.headers["x-chat2api-trace-id"].startswith("api_")


def test_responses_internal_id_is_always_resp_req_namespace() -> None:
    client = TestClient(_namespace_test_app())
    response = client.post(
        "/v1/responses",
        headers={"X-Chat2API-Request-ID": "req_chatfallback12345678"},
    )
    assert response.status_code == 200, response.text
    internal = response.json()["request_id"]
    assert internal.startswith("resp_req_")
    assert response.headers["x-chat2api-upstream-request-id"] == "req_chatfallback12345678"


def test_native_request_id_is_preserved_and_missing_id_is_generated() -> None:
    client = TestClient(_namespace_test_app())
    native = "req_native12345678"
    preserved = client.post("/v1/chat/completions", headers={"X-Chat2API-Request-ID": native})
    assert preserved.status_code == 200
    assert preserved.json()["request_id"] == native
    assert "x-chat2api-upstream-request-id" not in preserved.headers

    generated = client.post("/v1/responses")
    assert generated.status_code == 200
    assert generated.json()["request_id"].startswith("resp_req_")
    assert generated.headers["x-chat2api-internal-request-id"].startswith("resp_req_")


def test_invalid_same_namespace_id_remains_a_400() -> None:
    client = TestClient(_namespace_test_app())
    response = client.post(
        "/v1/chat/completions",
        headers={"X-Chat2API-Request-ID": "req_bad!"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid req_ request ID"
    assert response.headers["x-chat2api-trace-id"].startswith("api_")


def test_runtime_loads_recovery_after_final_window_refresh_and_installs_ingress_boundary() -> None:
    background = (ROOT / "chrome_extension/background_entry.js").read_text(encoding="utf-8")
    assert '"conversation_route_recovery_v136.js"' in background
    assert background.index('"conversation_route_recovery_v136.js"') > background.index('"background_window_refresh_v129.js"')

    entry = (ROOT / "app/entry.py").read_text(encoding="utf-8")
    assert "install_request_id_namespace_v136_patch(app)" in entry

    recovery = (ROOT / "chrome_extension/conversation_route_recovery_v136.js").read_text(encoding="utf-8")
    assert 'server-single-authority-scheduler-v58' in recovery
    assert 'server-authority-stale-inflight-v136' in recovery
    assert 'server-cancel-control-v136' in recovery
