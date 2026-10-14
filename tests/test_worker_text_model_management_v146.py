from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from app.worker_text_model_management_v146_patch import (
    _worker_snapshot,
    install_worker_text_model_management_v146_patch,
)


def test_worker_picker_models_do_not_include_legacy_or_free_defaults() -> None:
    meta = {
        "model_validation_state": "failed",
        "model_validation_error": "ChatGPT model picker was not found",
        "models": [{"id": "gpt-5.5-mini", "free_default": True}],
        "model_observed_choices": [{"id": "gpt-5.6-sol", "label": "GPT-5.6 Sol"}],
    }
    result = _worker_snapshot(meta)
    assert result["validation_state"] == "failed"
    assert result["validation_error"] == "ChatGPT model picker was not found"
    assert [m["id"] for m in result["observed"]] == ["gpt-5.6-sol"]
    assert result["validated"] == []


def test_persistent_admin_model_audit_auth_routing_and_worker_history(tmp_path: Path) -> None:
    worker = SimpleNamespace(
        name="TX03",
        metadata={"account_type": "paid", "model_validation_state": "pending"},
    )

    class Registry:
        def __init__(self):
            self.clients = {"ext_TX03": worker}
        def online_client_ids(self):
            return ["ext_TX03"]
        def chatgpt_routing_ready(self, client_id):
            return True
        def supports_model(self, client_id, model):
            return True
        def model_catalog(self, online_only=True):
            return [{"id": "gpt-5.6-sol", "clients": ["ext_TX03"]}]
        async def touch(self, client_id, metadata=None):
            self.clients[client_id].metadata.update(metadata or {})

    app = FastAPI()
    app.state.settings = SimpleNamespace(data_dir=tmp_path)
    app.state.registry = Registry()

    def require_admin(authorization: str | None = Header(default=None)):
        if authorization != "Bearer admin-token":
            raise HTTPException(401, "Administrator key required")

    @app.get("/api/admin/overview", dependencies=[Depends(require_admin)])
    def overview():
        return {"ok": True}

    install_worker_text_model_management_v146_patch(app)
    client = TestClient(app)
    assert client.get("/api/admin/text-models").status_code == 401
    auth = {"Authorization": "Bearer admin-token"}
    initial = client.get("/api/admin/text-models", headers=auth).json()
    assert initial["data"] == []

    import asyncio
    asyncio.run(app.state.registry.touch("ext_TX03", {
        "model_validation_state": "validated",
        "models": [{
            "id": "gpt-5.6-sol", "validated": True, "validation_revision": 145,
            "validation_method": "chatgpt-picker-current-selected-state",
        }],
        "model_observed_choices": [
            {"id": "gpt-5.6-sol", "label": "GPT 5.6 Sol"},
            {"id": "gpt-5.5", "label": "GPT 5.5"},
        ],
        "model_validation_validated_count": 1,
        "model_validation_candidate_count": 2,
    }))
    catalog = client.get("/api/admin/text-models", headers=auth).json()
    assert {x["id"] for x in catalog["data"]} == {"gpt-5.6-sol", "gpt-5.5"}
    rows = {row["id"]: row for row in catalog["data"]}
    assert rows["gpt-5.6-sol"]["state"] == "routable"
    assert rows["gpt-5.5"]["state"] == "observed-unverified"
    assert catalog["history"][0]["event"] == "worker-model-report"
    assert app.state.registry.supports_model("ext_TX03", "gpt-5.6-sol")

    result = client.patch("/api/admin/text-models/gpt-5.6-sol", json={"enabled": False}, headers=auth)
    assert result.status_code == 200
    assert not app.state.registry.supports_model("ext_TX03", "gpt-5.6-sol")
    assert app.state.registry.model_catalog() == []
    saved = json.loads((tmp_path / "worker_text_model_audit_v146.json").read_text())
    assert saved["disabled"] == ["gpt-5.6-sol"]
    assert any(x["event"] == "admin-model-toggle" for x in saved["history"])


def test_console_live_worker_dropdowns_have_no_static_fallback():
    root = Path(__file__).resolve().parents[1]
    admin = (root / "app" / "admin.py").read_text(encoding="utf-8")
    chat = (root / "app" / "admin_playground_chat_v69.js").read_text(encoding="utf-8")
    asset = (root / "app" / "admin_text_model_management_v146.js").read_text(encoding="utf-8")
    entry = (root / "app" / "entry.py").read_text(encoding="utf-8")
    browser = (root / "chrome_extension" / "content_model_library_v145.js").read_text(encoding="utf-8")
    assert "install_worker_text_model_management_v146_patch(app)" in entry
    assert "chatgpt-picker-current-selected-state" in browser
    assert "model_observed_choices" in (root / "chrome_extension" / "background_model_library_v145.js").read_text(encoding="utf-8")
    assert "api/admin/text-models" in asset
    assert 'syncSelect("testModel", usable)' in asset
    assert 'syncSelect("pgChatModel", usable)' in asset
    assert '|| "gpt-5.6-sol"' not in chat
    assert "__CHAT2API_TEXT_MODEL_MANAGEMENT_V146__" in admin

    import shutil
    import subprocess
    if shutil.which("node"):
        run = subprocess.run(
            ["node", "--check", str(root / "app" / "admin_text_model_management_v146.js")],
            capture_output=True, text=True, timeout=20, check=False,
        )
        assert run.returncode == 0, run.stderr
