"""Request source must be server-grounded and identical for table and ZIP."""
from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.v7_patch import install_v7_patch
from app.v8_patch import install_v8_patch
from app.voice_patch import install_voice_patch
from app.request_history_v94_patch import _normalize_admin_html
from app.admin import admin_response


def _client_app(tmp_path: Path):
    settings = Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )
    app = create_app(settings)
    install_voice_patch(app)
    install_v7_patch(app)
    install_v8_patch(app)
    return app


def _seed(client):
    store = client.app.state.telemetry
    runs = client.app.state.test_runs
    for rid, model in (
        ("req_playground_source", "gpt-5.6-sol"),
        ("req_external_source", "gpt-6"),
        ("req_other_test_source", "gpt-6"),
    ):
        asyncio.run(store.upsert({
            "request_id": rid, "status": "completed", "requested_model": model,
            "response_text": "ok", "final_prompt": "source test",
            "api_key_name": "howhub",
        }))
    asyncio.run(runs.upsert({
        "run_id": "chatrun_source_A",
        "request_id": "req_playground_source",
        "request_ids": ["req_playground_source"],
        "test_type": "chat", "status": "running",
    }))
    asyncio.run(runs.upsert({
        "run_id": "testrun_source_B",
        "request_ids": ["req_other_test_source"],
        "test_type": "abcd", "status": "completed",
    }))


def test_source_filter_and_zip_use_same_request_ids_and_preserve_original_rows(tmp_path):
    app = _client_app(tmp_path)
    h = {"Authorization": "Bearer master-key"}
    with TestClient(app) as client:
        _seed(client)
        all_rows = client.get("/api/admin/requests?limit=100", headers=h)
        assert all_rows.status_code == 200, all_rows.text
        sources = {r["request_id"]: r["request_source"] for r in all_rows.json()["data"]}
        assert sources == {
            "req_playground_source": "playground",
            "req_other_test_source": "playground",
            "req_external_source": "external",
        }
        for source, expected in (
            ("playground", {"req_playground_source", "req_other_test_source"}),
            ("external", {"req_external_source"}),
        ):
            listed = client.get("/api/admin/requests",
                params={"source": source, "limit": 100}, headers=h)
            assert listed.status_code == 200, listed.text
            assert listed.json()["total"] == len(expected)
            assert {r["request_id"] for r in listed.json()["data"]} == expected
            assert listed.json()["applied_filters"]["source"] == source
            zipped = client.get("/api/admin/requests/logs/batch",
                params={"source": source}, headers=h)
            assert zipped.status_code == 200, zipped.text
            with zipfile.ZipFile(io.BytesIO(zipped.content)) as archive:
                manifest = json.loads(archive.read("index.json"))
                assert manifest["filters"]["source"] == source
                assert {r["request_id"] for r in manifest["requests"]} == expected
                assert all(r["request_source"] == source
                    for r in manifest["requests"])
                for row in manifest["requests"]:
                    detailed = json.loads(archive.read(row["filename"]))
                    assert detailed["request"]["request_id"] == row["request_id"]

        both = client.get("/api/admin/requests",
            params={"source": "playground", "model": "gpt-6"}, headers=h)
        assert [r["request_id"] for r in both.json()["data"]] == [
            "req_other_test_source"
        ]
        assert client.get("/api/admin/requests",
            params={"source": "nonsense"}, headers=h).status_code == 422
        assert client.get("/api/admin/requests/logs/batch",
            params={"source": "nonsense"}, headers=h).status_code == 422
        assert "request_source" not in app.state.telemetry.get("req_external_source"), (
            "read-only source label must not contaminate stored request history"
        )


def test_served_request_history_replaces_model_field_with_one_smart_query():
    rendered = _normalize_admin_html(admin_response().body.decode("utf-8"))
    assert 'id="rqSearch"' in rendered
    assert 'request / error / client / model' in rendered
    assert 'id="rqModel"' not in rendered
    assert "function requestHistoryFilterParams()" in rendered
    assert "select.id='rqSource'" in rendered
    assert "['playground','测试场']" in rendered
    assert "['external','外部']" in rendered
    assert "query.set(isModel?'model':'q',input)" in rendered
    assert "query.set('source',source)" in rendered
    assert "warning.textContent=' · HTTP连接断开'" in rendered
