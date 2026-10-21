from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.playground_chat_patch import _chat_run_id
from app.playground_truth_v170 import reconcile_playground_runs


def _app(tmp_path: Path):
    return create_app(Settings(
        CHAT2API_API_KEY="test-key",
        CHAT2API_PAIRING_CODE="test-pair",
        CHAT2API_DATA_DIR=tmp_path,
    ))


def test_browser_failed_fetch_does_not_overwrite_authoritative_success(tmp_path: Path):
    app = _app(tmp_path)
    rid = "req_89abcdf012345678"
    with TestClient(app) as client:
        async def seed():
            await app.state.test_runs.upsert({
                "run_id": _chat_run_id(rid), "request_id": rid, "request_ids": [rid],
                "test_type": "chat", "status": "running",
                "quality": {"source": "manual-playground-chat"},
                "results": [{"kind": "chat", "request_id": rid, "status": "running"}],
            })
            await app.state.telemetry.upsert({
                "request_id": rid, "status": "completed", "request_type": "text",
                "completion_chars": 2498104, "requested_model": "gpt-6",
                "response_text": "Completed by backend",
            })
        asyncio.run(seed())
        auth = {"Authorization": "Bearer test-key"}
        assert client.get("/api/admin/tests").status_code == 401
        data = client.get("/api/admin/tests", headers=auth).json()["data"]
        run = next(row for row in data if row["request_id"] == rid)
        assert run["status"] == "passed"
        assert run["results"][0]["authoritative_request_status"] == "completed"
        assert run["results"][0]["response_chars"] == 2498104
        # Simulate a late browser record marking Failed to fetch after HTTP success.
        response = client.post("/api/admin/playground/chat-records", json={
            "request_id": rid, "model": "gpt-6",
            "status": "failed", "prompt": "图片",
            "error": "Failed to fetch", "duration_ms": 89991,
        })
        assert response.status_code == 200
        latest = client.get("/api/admin/tests/" + _chat_run_id(rid), headers=auth).json()
        assert latest["status"] == "passed", "The backend terminal is the single status authority"


def test_long_image_running_not_falsely_finalized(tmp_path: Path):
    app = _app(tmp_path)
    rid = "imgreq_1234567890abc"
    run_id = "testrun_abcdef1234567890"
    with TestClient(app) as client:
        async def seed():
            await app.state.test_runs.upsert({
                "run_id": run_id, "request_id": rid, "request_ids": [rid],
                "test_type": "image_generation", "status": "running",
                "quality": {"lifecycle": "playground-lifecycle-v1"},
                "results": [{"kind": "image_generation", "request_id": rid, "status": "running"}],
            })
            await app.state.telemetry.upsert({
                "request_id": rid, "status": "running", "request_type": "image_generation",
                "requested_model": "gpt-image",
            })
        asyncio.run(seed())
        auth = {"Authorization": "Bearer test-key"}
        assert client.get("/api/admin/tests/" + run_id, headers=auth).json()["status"] == "running"
        asyncio.run(app.state.telemetry.upsert({
            "request_id": rid, "status": "error",
            "error": "Timed out waiting for ChatGPT Images",
        }))
        run = client.get("/api/admin/tests/" + run_id, headers=auth).json()
        assert run["status"] == "stalled"
        assert run["results"][0]["status"] == "stalled"


def test_image_recovery_is_compact_authenticated_and_file_scoped(tmp_path: Path):
    app = _app(tmp_path)
    rid = "req_abcdef01234567890"
    sample = b"\x89PNG\r\n\x1a\n" + b"\x00" * 96
    encoded = base64.b64encode(sample).decode()
    with TestClient(app) as client:
        asyncio.run(app.state.telemetry.upsert({
            "request_id": rid, "status": "completed", "request_type": "text",
            "api_key_id": "key_123456", "response_text": f"![avatar](data:image/png;base64,{encoded})",
            "completion_chars": len(encoded) + 40,
        }))
        url = f"/api/admin/playground/requests/{rid}/outcome"
        assert client.get(url).status_code == 401
        auth = {"Authorization": "Bearer test-key"}
        response = client.get(url, headers=auth)
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "completed"
        assert result["response_media_count"] == 1
        assert "base64," not in json.dumps(result)
        assert len(json.dumps(result)) < 6000
        file_id = result["response_media"][0]["file_id"]
        stored = app.state.telemetry.get(rid)
        assert "base64," not in stored["response_text"]
        media = client.get(f"/api/admin/requests/{rid}/media/{file_id}", headers=auth)
        assert media.status_code == 200 and media.content == sample
        # Calling the same endpoint again must not archive another copy.
        again = client.get(url, headers=auth).json()
        assert again["response_media"][0]["file_id"] == file_id
        assert client.get(
            f"/api/admin/requests/req_different0000/media/{file_id}", headers=auth,
        ).status_code == 404


def test_probe_has_long_job_checkpoints_without_second_terminal_owner():
    root = Path(__file__).resolve().parents[1]
    js = (root / "chrome_extension/content_probe_supervisor_v1.js").read_text()
    python = (root / "app/worker_probe_store.py").read_text()
    assert "progress_checkpoint" in js and "progress_checkpoint" in python
    assert "[60000, 120000, 240000, 480000]" in js
    assert "chat.completed" not in js and "image.completed" not in js
