from __future__ import annotations

from contextvars import ContextVar
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app import v13_patch
from app.model_capability_routing_patch import PATCH_ID, install_model_capability_routing_patch


class Registry:
    def __init__(self, rows: dict[str, tuple[str, list[Any]]]) -> None:
        self.clients = {
            client_id: SimpleNamespace(metadata={"account_type": account}, connection_enabled=True)
            for client_id, (account, _models) in rows.items()
        }
        self.models = {client_id: list(models) for client_id, (_account, models) in rows.items()}
        self.sockets = {client_id: object() for client_id in rows}
        self.busy_clients: set[str] = set()
        self.api_key_routes: dict[str, str] = {}
        self.routing_key_context: ContextVar[str | None] = ContextVar("test-routing-key", default="key_test")
        self.remembered: list[tuple[str | None, str]] = []

    def online_client_ids(self) -> list[str]:
        return list(self.sockets)

    def client_models(self, client_id: str) -> list[Any]:
        return list(self.models.get(client_id, []))

    def _remember_route(self, key_id: str | None, client_id: str) -> None:
        self.remembered.append((key_id, client_id))
        if key_id:
            self.api_key_routes[key_id] = client_id

    def resolve_client(self, requested: str | None) -> str:
        if requested:
            if requested not in self.sockets:
                raise ConnectionError("offline")
            return requested
        target = v13_patch._target_context.get() or {}
        if str(target.get("model") or "") == "gpt-5.5-mini":
            for client_id in self.online_client_ids():
                if self.clients[client_id].metadata.get("account_type") == "free":
                    return client_id
        return self.online_client_ids()[0]


def _app(rows: dict[str, tuple[str, list[Any]]]) -> tuple[FastAPI, Registry]:
    app = FastAPI()
    registry = Registry(rows)
    app.state.registry = registry

    @app.post("/v1/chat/completions")
    async def route(request: Request):
        requested = request.query_params.get("client_id")
        try:
            return {"client_id": registry.resolve_client(requested)}
        except (LookupError, ConnectionError) as error:
            return JSONResponse({"error": str(error)}, status_code=503)

    install_model_capability_routing_patch(app)
    return app, registry


def test_paid_model_never_reuses_free_sticky_route() -> None:
    app, registry = _app(
        {
            "ext_free": ("free", ["gpt-5.5-mini"]),
            "ext_paid": ("paid", ["gpt-5.6-sol", "gpt-5.5"]),
        }
    )
    registry.api_key_routes["key_test"] = "ext_free"
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json={"model": "gpt-5.6-sol", "messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 200
    assert response.json()["client_id"] == "ext_paid"
    assert registry.api_key_routes["key_test"] == "ext_paid"


def test_paid_model_fails_fast_when_only_free_worker_is_online() -> None:
    app, _ = _app({"ext_free": ("free", ["gpt-5.5-mini"])})
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json={"model": "gpt-5.6-sol", "messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 503
    assert "No online Worker is compatible with gpt-5.6-sol" in response.json()["error"]
    assert "ChatGPT Free Workers can serve gpt-5.5-mini only" in response.json()["error"]


def test_paid_worker_must_advertise_requested_family_when_catalog_is_present() -> None:
    app, _ = _app({"ext_paid": ("paid", ["gpt-5.5"])})
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json={"model": "gpt-5.6-sol", "messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 503
    assert "models=gpt-5.5" in response.json()["error"]


def test_explicit_free_worker_is_rejected_for_paid_model() -> None:
    app, _ = _app({"ext_free": ("free", ["gpt-5.5-mini"])})
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions?client_id=ext_free",
            json={"model": "gpt-5.6-sol", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 503
    assert "not compatible with gpt-5.6-sol" in response.json()["error"]


def test_mini_keeps_existing_free_preference_and_context_reaches_base_resolver() -> None:
    app, registry = _app(
        {
            "ext_paid": ("paid", ["gpt-5.6-sol", "gpt-5.5"]),
            "ext_free": ("free", ["gpt-5.5-mini"]),
        }
    )
    registry.api_key_routes["key_test"] = "ext_paid"
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json={"model": "gpt-5.5-mini", "messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 200
    assert response.json()["client_id"] == "ext_free"
    assert registry.api_key_routes["key_test"] == "ext_paid"
    assert app.state.model_capability_routing_patch_id == PATCH_ID


def test_unknown_linux_worker_with_structured_gpt55_catalog_routes_mini_vision() -> None:
    """Production ClientRegistry returns model dictionaries, not string ids."""

    app, registry = _app({"ext_linux": ("unknown", [])})
    registry.models["ext_linux"] = [
        {
            "id": "gpt-5.5",
            "label": "GPT-5.5",
            "capabilities": ["text", "vision", "file-understanding"],
            "selected": True,
        }
    ]
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-5.5-mini",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "describe"},
                            {"type": "image_url", "image_url": {"url": "https://example.test/image.png"}},
                        ],
                    }
                ],
            },
        )
    assert response.status_code == 200
    assert response.json()["client_id"] == "ext_linux"
