from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.control_plane_v1_patch import install_control_plane_v1
from app.main import create_app
from app.model_entitlement_v1_patch import install_model_entitlements
from app.tenant_membership import TenantMembershipStore
from app.user_console_v104_patch import install_user_console_v104_patch
from app.web_chat_integration_v1 import install_web_chat_features_v1


def test_web_chat_model_list_uses_available_models_and_labels_default_mapping(tmp_path: Path):
    app = create_app(Settings(CHAT2API_DATA_DIR=str(tmp_path)))
    install_control_plane_v1(app)
    app.state.control_plane.update_client({"web_chat_enabled": True})
    install_user_console_v104_patch(app)
    install_web_chat_features_v1(app)
    shared_public, _shared_token = asyncio.run(app.state.api_keys.create("web-chat-shared"))
    app.state.control_plane.update_client({"api_key_id": shared_public["key_id"]})

    async def available_models():
        return {
            "available_models": [
                {"id": "default", "label": "默认模型（实际型号不可见）", "is_default_only": True},
                {"id": "gpt-5.5", "label": "gpt-5.5"},
                {"id": "gpt-5.4", "label": "gpt-5.4"},
                {"id": "gpt-image", "label": "ChatGPT Images browser route"},
            ],
            "available_models_default_mapping": "gpt-5.6-sol",
        }

    app.state.worker_model_tasks_v147_snapshot = available_models
    app.state.worker_text_model_management_v146_snapshot = lambda: {"disabled": ["gpt-5.4"]}
    with TestClient(app) as client:
        registered = client.post("/api/user/register", json={
            "email": "model-catalog@example.com",
            "password": "web-chat-password-123",
        })
        assert registered.status_code == 200, registered.text

        response = client.get("/api/user/chat/models")
        assert response.status_code == 200, response.text
        rows = response.json()["data"]
        ids = [row["id"] for row in rows]
        assert ids == ["default", "gpt-5.5"]
        default = rows[0]
        assert default["label"] == "gpt-5.6-sol"
        assert default["mapped_model_id"] == "gpt-5.6-sol"
        assert "gpt-image" not in ids



def test_web_chat_admin_projection_does_not_restore_denied_models(tmp_path: Path):
    app = create_app(Settings(CHAT2API_DATA_DIR=str(tmp_path)))
    install_control_plane_v1(app)
    app.state.control_plane.update_client({"web_chat_enabled": True})
    install_user_console_v104_patch(app)
    app.state.tenant_memberships = TenantMembershipStore(tmp_path)
    install_model_entitlements(app)
    install_web_chat_features_v1(app)
    shared, _ = asyncio.run(app.state.api_keys.create("web-chat-shared"))
    app.state.control_plane.update_client({"api_key_id": shared["key_id"]})

    async def available_models():
        return {
            "available_models": [
                {"id": "default"},
                {"id": "gpt-5.5"},
                {"id": "gpt-5.6-sol"},
                {"id": "gpt-6-sol"},
            ],
            "available_models_default_mapping": "gpt-5.6-sol",
        }

    app.state.worker_model_tasks_v147_snapshot = available_models
    disabled = []
    app.state.worker_text_model_management_v146_snapshot = lambda: {
        "disabled": disabled,
        "legacy_minimum_model": "gpt-5.6-sol",
    }
    # Stub only the downstream adapter. If an invalid request reaches it,
    # the admission assertion below fails; model policies are real.
    from fastapi.responses import JSONResponse
    dispatched = []

    async def adapter(body, request, client_id, request_id, principal):
        dispatched.append(body.model)
        return JSONResponse({"model": body.model})

    app.state.chat_completions_adapter = adapter
    origin = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
    with TestClient(app) as client:
        registered = client.post("/api/user/register", json={
            "email": "model-entitlement@example.com",
            "password": "web-chat-password-123",
        })
        assert registered.status_code == 200, registered.text
        user_id = registered.json()["user"]["user_id"]
        store = app.state.model_entitlements

        def ids():
            response = client.get("/api/user/chat/models")
            assert response.status_code == 200, response.text
            return [row["id"] for row in response.json()["data"]]

        def send(model):
            return client.post("/api/user/chat/completions", headers=origin, json={
                "model": model,
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            })

        assert ids() == ["default", "gpt-5.5", "gpt-5.6-sol", "gpt-6-sol"]
        # The administrator toggle also applies to a previously cached default.
        disabled.append("gpt-5.6-sol")
        assert ids() == ["gpt-5.5", "gpt-6-sol"]
        blocked = send("default")
        assert blocked.status_code == 403, blocked.text
        assert blocked.json()["detail"]["reason"] == "admin_model_disabled"
        assert send("gpt-5.6-sol").status_code == 403
        assert dispatched == []
        disabled.clear()

        # A global allowlist does not prove the hidden page-default identity.
        store.upsert("global", "default", "allow", ["gpt-5.5", "gpt-5.6-sol", "gpt-6-sol"])
        store.upsert("key", shared["key_id"], "deny", ["gpt-5.5"])
        store.upsert("user", user_id, "deny", ["gpt-6-sol"])
        assert ids() == ["gpt-5.6-sol"]
        for model in ("default", "gpt-5.5", "gpt-6-sol"):
            response = send(model)
            assert response.status_code == 403, response.text
            assert response.json()["detail"]["code"] == "model_not_entitled"
        assert dispatched == []
        allowed = send("gpt-5.6-sol")
        assert allowed.status_code == 200, allowed.text
        assert dispatched == ["gpt-5.6-sol"]
