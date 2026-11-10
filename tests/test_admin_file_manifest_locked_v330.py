"""Degraded generated-file live manifest reads are forensic unknown, never 500.

The 2026-11-10 C request had four real SHA-verified ready downloads while
its immutable terminal request row still held processing statuses.
"""
import asyncio
import sqlite3

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


RID = "req_5ba6bebe8b0a4d808c69b2c1c49d9e42"
PENDING = (
    "https://chat2api.example/api/generated-files/pending/pgen_" +
    "a" * 32 + "/" + "B" * 24
)


def _app(tmp_path):
    return create_app(Settings(
        CHAT2API_API_KEY="test-key",
        CHAT2API_PAIRING_CODE="test-pair",
        CHAT2API_DATA_DIR=tmp_path,
    ))


def test_admin_request_detail_manifest_lock_does_not_raise_http_500(tmp_path, monkeypatch):
    app = _app(tmp_path)
    with TestClient(app) as client:
        asyncio.run(app.state.telemetry.upsert({
            "request_id": RID, "status": "completed",
            "response_text": f"[📎 plan.xlsx]({PENDING})",
            "response_generated_files": [{
                "filename": "plan.xlsx", "url": PENDING,
                "bytes": 1234, "status": "processing",
            }],
        }))
        def database_locked(*_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(app.state.deferred_generated_files, "status", database_locked)
        response = client.get(
            "/api/admin/requests/" + RID,
            headers={"Authorization": "Bearer test-key"},
        )
        assert response.status_code == 200, response.text
        value = response.json()
        assert value["status"] == "completed"
        assert value["response_generated_files_live_checked"] is True
        assert value["response_generated_files"][0]["status"] == "unavailable"
        # An observer failure must not overwrite immutable terminal history.
        saved = app.state.telemetry.get(RID)
        assert saved["status"] == "completed"
        assert saved["response_generated_files"][0]["status"] == "processing"


def test_admin_live_status_ready_is_observation_only(tmp_path, monkeypatch):
    app = _app(tmp_path)
    with TestClient(app) as client:
        asyncio.run(app.state.telemetry.upsert({
            "request_id": RID, "status": "completed",
            "response_generated_files": [{
                "filename": "plan.xlsx", "url": PENDING, "bytes": 1234,
                "status": "processing",
            }],
        }))
        monkeypatch.setattr(
            app.state.deferred_generated_files, "status",
            lambda *_args: {"status": "ready"},
        )
        auth = {"Authorization": "Bearer test-key"}
        out = client.get("/api/admin/requests/" + RID, headers=auth)
        assert out.status_code == 200
        assert out.json()["response_generated_files"][0]["status"] == "ready"
        assert app.state.telemetry.get(RID)["response_generated_files"][0]["status"] == "processing"
        assert app.state.telemetry.get(RID)["status"] == "completed"
