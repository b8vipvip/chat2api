"""Time-only combined request-log export: all retained rows from the requested instant."""
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


def batch(client, since):
    return client.get(
        "/api/admin/requests/logs/batch",
        params={"since": since},
        headers=headers(),
    )


def test_since_only_export_preserves_single_request_reports_and_status(tmp_path: Path):
    with TestClient(app_v8(tmp_path)) as client:
        store = client.app.state.telemetry
        for ident, status, recorded in (
            ("req_old", "completed", "2026-10-29T10:39:30+08:00"),
            ("req_start", "error", "2026-10-29T10:39:31+08:00"),
            ("req_new", "running", "2026-10-29T11:00:00+08:00"),
        ):
            asyncio.run(store.upsert({
                "request_id": ident, "status": status,
                "recorded_at": recorded, "requested_model": "gpt-6",
                "final_prompt": f"customer prompt {ident}",
                "response_text": f"answer {ident}",
            }))
        assert client.get("/api/admin/requests/logs/batch?since=2026-10-29T02:39:31Z").status_code == 401
        assert client.get("/api/admin/requests/logs/batch", headers=headers()).status_code == 422

        # Naive timestamp is Beijing, not the server's or the admin browser's timezone.
        response = batch(client, "2026-10-29 10:39:31")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/zip")
        assert response.headers["x-chat2api-exported-count"] == "2"
        assert "since" in response.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            assert bundle.testzip() is None
            manifest = json.loads(bundle.read("index.json"))
            assert manifest["selection_mode"] == "since"
            assert manifest["since"] == "2026-10-29T02:39:31+00:00"
            assert manifest["time_filter_field"] == "recorded_at"
            assert "requested_limit" not in manifest
            assert manifest["exported_count"] == 2
            assert [item["request_id"] for item in manifest["requests"]] == [
                "req_new", "req_start",
            ]
            for row in manifest["requests"]:
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
            readme = bundle.read("README.txt").decode("utf-8")
            assert "起始时间（含）" in readme
            assert "只读" in readme
        assert store.get("req_new")["status"] == "running"

        # Explicit offset and Z are the same UTC instant as the unzoned example.
        for raw in ("2026-10-29T10:39:31+08:00", "2026-10-29T02:39:31Z"):
            explicit = batch(client, raw)
            assert explicit.status_code == 200, explicit.text
            assert explicit.headers["x-chat2api-exported-count"] == "2"


def test_filter_includes_all_retained_newer_rows_and_rejects_invalid_dates(tmp_path: Path):
    with TestClient(app_v8(tmp_path)) as client:
        assert batch(client, "2026-10-29 00:00:00").status_code == 404
        store = client.app.state.telemetry
        for i in range(25):
            asyncio.run(store.upsert({
                "request_id": f"req_{i:03d}", "status": "completed",
                "recorded_at": "2026-10-29T03:00:00+08:00",
            }))
        response = batch(client, "2026-10-29 02:59:59")
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            assert json.loads(bundle.read("index.json"))["exported_count"] == 25

        # Old N is no longer a selection mechanism, even if a legacy caller supplies it.
        response = client.get(
            "/api/admin/requests/logs/batch",
            params={"since": "2026-10-29 02:59:59", "limit": 1},
            headers=headers(),
        )
        assert response.headers["x-chat2api-exported-count"] == "25"
        for invalid in ("bad-date", "2026-02-30 10:39:31",
                        "2026-10-29T11:28:29+15:00",
                        "2026-10-29 25:39:31", "", "2026-10-29"):
            assert batch(client, invalid).status_code == 422, invalid
        assert batch(client, "2030-01-01T00:00:00+08:00").status_code == 404


def test_request_history_exposes_only_pasteable_start_time_control():
    source = (Path(__file__).resolve().parents[1] / "app" /
              "request_history_v94_patch.py").read_text(encoding="utf-8")
    assert "rqBatchExportLogs" in source
    assert "rqBatchExportSince" in source
    assert "requestHistoryParseExportSince(value)" in source
    assert "since.type='text'" in source
    assert "since.type='datetime-local'" not in source
    assert "rqBatchExportLimit" not in source
    assert "query.set('since'" not in source  # single URLSearchParams construction
    assert "new URLSearchParams({since:parsed.iso})" in source
    assert "合并打包导出请求日志" in source
    assert "requestHistoryDownload(" in source
    assert "未标注时区按北京时间" in source
