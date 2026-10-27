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


def headers():
    return {"Authorization": "Bearer master-key"}


def app_v8(tmp_path: Path):
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


def test_recent_n_download_preserves_single_request_report_and_status(tmp_path: Path):
    with TestClient(app_v8(tmp_path)) as client:
        store = client.app.state.telemetry
        for i, status in [(1, "completed"), (2, "error"), (3, "running")]:
            asyncio.run(store.upsert({
                "request_id": f"req_recent_{i}",
                "status": status,
                "requested_model": "gpt-6",
                "final_prompt": f"customer prompt {i}",
                "response_text": f"answer {i}",
            }))

        unauthorized = client.get("/api/admin/requests/logs/batch?limit=2")
        assert unauthorized.status_code == 401

        invalid = client.get("/api/admin/requests/logs/batch?limit=501", headers=headers())
        assert invalid.status_code == 422
        invalid_zero = client.get("/api/admin/requests/logs/batch?limit=0", headers=headers())
        assert invalid_zero.status_code == 422

        response = client.get("/api/admin/requests/logs/batch?limit=2", headers=headers())
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/zip")
        assert "attachment" in response.headers["content-disposition"]
        assert response.headers["x-chat2api-exported-count"] == "2"

        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            assert bundle.testzip() is None
            names = bundle.namelist()
            assert len(names) == 4
            index = json.loads(bundle.read("index.json"))
            assert index["requested_limit"] == 2
            assert index["exported_count"] == 2
            assert [r["request_id"] for r in index["requests"]] == [
                "req_recent_3", "req_recent_2",
            ]
            for row in index["requests"]:
                artifact = json.loads(bundle.read(row["filename"]))
                single = client.get(
                    f"/api/admin/requests/{row['request_id']}/log",
                    headers=headers(),
                ).json()
                assert artifact["report_type"] == "chat2api-request-diagnostic"
                assert artifact["request"] == single["request"]
                assert artifact["request"]["status"] == row["status"]
                assert artifact["worker_probe_evidence"] == single["worker_probe_evidence"]
                assert artifact["playground_diagnostics"] == single["playground_diagnostics"]
                assert artifact["http_trace"] == single["http_trace"]
                assert "extension_snapshot" in artifact
                assert "final_prompt" in artifact["request"]
            assert "只读" in bundle.read("README.txt").decode("utf-8")
        assert store.get("req_recent_3")["status"] == "running"


def test_recent_n_obeys_retention_and_rejects_empty_history(tmp_path: Path):
    with TestClient(app_v8(tmp_path)) as client:
        response = client.get("/api/admin/requests/logs/batch?limit=1", headers=headers())
        assert response.status_code == 404

        asyncio.run(client.app.state.telemetry.upsert({
            "request_id": "req_only", "status": "completed",
        }))
        response = client.get("/api/admin/requests/logs/batch?limit=500", headers=headers())
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            manifest = json.loads(bundle.read("index.json"))
            assert manifest["requested_limit"] == 500
            assert manifest["exported_count"] == 1


def test_canonical_request_history_has_single_batch_control():
    source = (Path(__file__).resolve().parents[1] / "app" /
              "request_history_v94_patch.py").read_text(encoding="utf-8")
    assert "rqBatchExportLogs" in source
    assert "rqBatchExportLimit" in source
    assert "合并打包导出请求日志" in source
    assert "/api/admin/requests/logs/batch?limit=" in source
    assert "limit.max='500'" in source
    assert "requestHistoryDownload(" in source
