from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.mode_resolver import ModeResolverMiddleware


def _app(tmp_path: Path) -> FastAPI:
    app = create_app(
        Settings(
            CHAT2API_API_KEY="mode-resolver-test-master-key",
            CHAT2API_DATA_DIR=str(tmp_path),
        )
    )

    @app.get("/v1/_mode_resolver_probe")
    async def api_probe(request: Request):
        return {
            "mode": getattr(request.state, "chat2api_mode", None),
            "source": getattr(request.state, "chat2api_mode_source", None),
            "entrypoint": getattr(request.state, "chat2api_entrypoint", None),
        }

    @app.get("/_mode_resolver_non_api_probe")
    async def non_api_probe(request: Request):
        return {"mode": getattr(request.state, "chat2api_mode", None)}

    @app.get("/v1/_mode_resolver_stream")
    async def stream_probe():
        async def chunks():
            yield b"first"
            yield b"-second"

        return StreamingResponse(chunks(), media_type="text/plain")

    @app.websocket("/_mode_resolver_ws")
    async def websocket_probe(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("ok")
        await websocket.close()

    return app


def test_unmarked_openai_api_request_defaults_to_mode2(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        response = client.get("/v1/_mode_resolver_probe")

    assert response.status_code == 200
    assert response.json() == {
        "mode": "mode2",
        "source": "default",
        "entrypoint": "openai_api",
    }


@pytest.mark.parametrize("value", ["api", "mode2", " API ", "Mode2"])
def test_explicit_mode2_aliases_resolve_to_mode2(tmp_path: Path, value: str) -> None:
    with TestClient(_app(tmp_path)) as client:
        response = client.get(
            "/v1/_mode_resolver_probe",
            headers={"X-Chat2API-Mode": value},
        )

    assert response.status_code == 200
    assert response.json() == {
        "mode": "mode2",
        "source": "header",
        "entrypoint": "openai_api",
    }


@pytest.mark.parametrize("value", ["mode1", "chat", " MODE1 "])
def test_mode1_on_openai_api_ingress_is_rejected_as_entrypoint_conflict(
    tmp_path: Path, value: str
) -> None:
    with TestClient(_app(tmp_path)) as client:
        response = client.get(
            "/v1/_mode_resolver_probe",
            headers={"X-Chat2API-Mode": value},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "mode_entrypoint_conflict"
    assert response.json()["error"]["type"] == "invalid_request_error"


@pytest.mark.parametrize("value", ["", "mode3", "proxy", "mode2,mode1", "二"])
def test_unknown_or_malformed_mode_values_are_rejected(tmp_path: Path, value: str) -> None:
    with TestClient(_app(tmp_path)) as client:
        response = client.get(
            "/v1/_mode_resolver_probe",
            headers={"X-Chat2API-Mode": value},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_mode"


def test_duplicate_mode_headers_are_rejected(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        response = client.get(
            "/v1/_mode_resolver_probe",
            headers=[
                ("X-Chat2API-Mode", "api"),
                ("X-Chat2API-Mode", "mode2"),
            ],
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "duplicate_mode_header"


def test_mode_header_is_not_a_generic_non_api_routing_switch(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        ordinary = client.get("/_mode_resolver_non_api_probe")
        attempted_switch = client.get(
            "/_mode_resolver_non_api_probe",
            headers={"X-Chat2API-Mode": "mode2"},
        )

    assert ordinary.status_code == 200
    assert ordinary.json() == {"mode": None}
    assert attempted_switch.status_code == 400
    assert attempted_switch.json()["error"]["code"] == "mode_header_not_supported_for_entrypoint"


def test_mode_resolver_does_not_consume_or_rewrite_streaming_bodies(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        response = client.get("/v1/_mode_resolver_stream")

    assert response.status_code == 200
    assert response.content == b"first-second"


def test_mode_resolver_does_not_intercept_websocket_transport(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        with client.websocket_connect("/_mode_resolver_ws") as websocket:
            assert websocket.receive_text() == "ok"


def test_mode_resolver_middleware_is_installed_on_the_base_application(tmp_path: Path) -> None:
    app = _app(tmp_path)
    assert any(middleware.cls is ModeResolverMiddleware for middleware in app.user_middleware)
