from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.control_plane_v1_patch import install_control_plane_v1
from app.main import create_app
from app.user_console_v104_patch import install_user_console_v104_patch
from app.web_chat_integration_v1 import install_web_chat_features_v1


def test_web_chat_model_list_uses_available_models_and_labels_default_mapping(tmp_path: Path):
    app = create_app(Settings(CHAT2API_DATA_DIR=str(tmp_path)))
    install_control_plane_v1(app)
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
