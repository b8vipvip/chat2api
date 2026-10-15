from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from app.worker_model_tasks_v147_patch import install_manual_worker_text_model_tasks_v147_patch


class Registry:
    def __init__(self):
        self.clients = {
            "ext_paid": SimpleNamespace(name="TX03", metadata={
                "model_validation_state": "discovered",
                "model_validation_revision": 145,
                "model_observed_choices": [{"id": "gpt-5.6-sol", "label": "Sol"}],
            }),
            "ext_free": SimpleNamespace(name="Free", metadata={}),
        }
        self.sent = []

    def online_client_ids(self):
        return ["ext_paid", "ext_free"]

    def chatgpt_routing_ready(self, client_id):
        return client_id == "ext_paid"

    def supports_model(self, client_id, model_id):
        return False

    def client_models(self, client_id):
        return []

    def model_catalog(self, online_only=True):
        return []

    async def touch(self, client_id, metadata=None):
        self.clients[client_id].metadata.update(metadata or {})

    async def send(self, client_id, payload):
        self.sent.append((client_id, payload))


def setup(tmp_path):
    app = FastAPI()
    app.state.settings = SimpleNamespace(data_dir=tmp_path)
    registry = Registry()
    app.state.registry = registry
    app.state.worker_text_model_management_v146_snapshot = lambda: {"disabled": []}

    def guard(authorization: str | None = Header(default=None)):
        if authorization != "Bearer admin-token":
            raise HTTPException(401, "Administrator credential required")

    @app.get("/api/admin/overview", dependencies=[Depends(guard)])
    def overview():
        return {"ok": True}

    install_manual_worker_text_model_tasks_v147_patch(app)
    return app, registry, TestClient(app)


def test_manual_model_discovery_targets_all_online_and_logs_are_scoped(tmp_path):
    app, registry, client = setup(tmp_path)
    assert client.post("/api/admin/text-model-tasks/discover").status_code == 401
    auth = {"Authorization": "Bearer admin-token"}
    response = client.post("/api/admin/text-model-tasks/discover", headers=auth)
    assert response.status_code == 200
    data = response.json()
    assert set(data["workers"]) == {"ext_paid", "ext_free"}
    assert {payload["type"] for _, payload in registry.sent} == {"model.task.v147"}
    assert all(payload["task_id"] == data["id"] for _, payload in registry.sent)
    assert client.get("/api/admin/text-model-tasks", headers=auth).json()["data"][0]["id"] == data["id"]

    asyncio.run(registry.touch("ext_paid", {"model_task_report_v147": {
        "task_id": data["id"], "state": "completed", "progress": 100,
        "models": ["gpt-5.6-sol"], "logs": ["Worker picker opened", "Found Sol"],
    }}))
    task = client.get("/api/admin/text-model-tasks", headers=auth).json()["data"][0]
    assert task["workers"]["ext_paid"]["state"] == "completed"
    assert task["workers"]["ext_paid"]["models"] == ["gpt-5.6-sol"]
    output = client.get(f"/api/admin/text-model-tasks/{data['id']}/logs", headers=auth)
    assert output.status_code == 200
    assert "Found Sol" in output.text
    scoped = client.get(f"/api/admin/text-model-tasks/{data['id']}/logs?worker=ext_paid", headers=auth)
    assert "ext_free" not in scoped.text
    assert client.get(f"/api/admin/text-model-tasks/{data['id']}/logs").status_code == 401
    assert (tmp_path/"worker_text_model_tasks_v147.json").exists()


def test_observed_model_is_callable_before_explicit_validation(tmp_path):
    app, registry, client = setup(tmp_path)
    assert registry.supports_model("ext_paid", "gpt-5.6-sol")
    assert not registry.supports_model("ext_free", "gpt-5.6-sol")
    rows = registry.model_catalog()
    assert len(rows) == 1
    assert rows[0]["validated"] is False
    assert rows[0]["source"] == "worker-picker-observed-unverified"
    registry.clients["ext_paid"].metadata["model_validation_state"] = "pending"
    assert not registry.supports_model("ext_paid", "gpt-5.6-sol")
    assert not registry.model_catalog()
    registry.clients["ext_paid"].metadata["model_validation_state"] = "discovered"

    auth = {"Authorization": "Bearer admin-token"}
    task = client.post("/api/admin/text-models/gpt-5.6-sol/validate", headers=auth)
    assert task.status_code == 200
    assert set(task.json()["workers"]) == {"ext_paid"}
    unknown = client.post("/api/admin/text-models/gpt-6/validate", headers=auth)
    assert unknown.status_code == 409


def test_manual_only_no_automatic_model_discovery_and_unified_id_column():
    root = Path(__file__).resolve().parents[1]
    bg = (root/"chrome_extension/background_model_library_v145.js").read_text("utf-8")
    router = (root/"chrome_extension/model_routing_v2.js").read_text("utf-8")
    ui = (root/"app/admin_text_model_tasks_v147.js").read_text("utf-8")
    unified = (root/"app/admin_unified_workers_v153.js").read_text("utf-8")
    assert "model.task.v147" in bg
    assert 'schedule("worker-restart"' not in bg
    assert 'schedule("worker-connect"' not in bg
    assert 'schedule("worker-enable"' not in bg
    assert 'schedule("model-validation-retry"' not in bg
    assert "modelObservedChoices" in router
    assert "发现模型" in ui
    assert "任务记录" in ui
    assert "Linux Worker 管理绑定：" in unified

    import shutil
    import subprocess
    if shutil.which("node"):
        for script in ("admin_text_model_tasks_v147.js",):
            p=root/"app"/script
            result=subprocess.run(["node","--check",str(p)],text=True,capture_output=True,timeout=20)
            assert result.returncode == 0, result.stderr
        for script in ("background_model_library_v145.js","model_routing_v2.js"):
            p=root/"chrome_extension"/script
            result=subprocess.run(["node","--check",str(p)],text=True,capture_output=True,timeout=20)
            assert result.returncode == 0, result.stderr
