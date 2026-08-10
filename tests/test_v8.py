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


def settings(tmp_path: Path) -> Settings:
    return Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )


def headers() -> dict[str, str]:
    return {"Authorization": "Bearer master-key"}


def app_v8(tmp_path: Path):
    app = create_app(settings(tmp_path))
    install_voice_patch(app)
    install_v7_patch(app)
    install_v8_patch(app)
    return app


def test_v8_version_and_diagnostic_capabilities(tmp_path: Path) -> None:
    with TestClient(app_v8(tmp_path)) as client:
        assert client.get("/").json()["version"] == "0.8.0"
        assert client.get("/healthz").json()["version"] == "0.8.0"
        overview = client.get("/api/admin/overview", headers=headers()).json()
        assert overview["version"] == "0.8.0"
        assert overview["capabilities"]["diagnostic_export"] is True
        assert overview["capabilities"]["request_trace"] is True


def test_failed_api_request_gets_trace_and_downloadable_report(tmp_path: Path) -> None:
    with TestClient(app_v8(tmp_path)) as client:
        failed = client.post(
            "/v1/chat/completions",
            headers={**headers(), "User-Agent": "external-test-client/1.0"},
            json={
                "model": "default",
                "messages": [{"role": "user", "content": "this prompt must not be exported"}],
                "stream": False,
            },
        )
        assert failed.status_code == 503
        trace_id = failed.headers.get("x-chat2api-trace-id")
        assert trace_id and trace_id.startswith("trace_")

        rows = client.get("/api/admin/requests?limit=20", headers=headers()).json()["data"]
        row = next(item for item in rows if item.get("trace_id") == trace_id)
        assert row["status"] == "error"
        request_id = row["request_id"]

        report_response = client.get(f"/api/admin/requests/{request_id}/log", headers=headers())
        assert report_response.status_code == 200
        assert "attachment" in report_response.headers["content-disposition"]
        report = report_response.json()
        assert report["request"]["trace_id"] == trace_id
        assert report["http_trace"]
        event = next(item for item in report["http_trace"] if item["path"] == "/v1/chat/completions")
        assert event["status_code"] == 503
        assert event["request"]["model"] == "default"
        assert event["request"]["message_count"] == 1
        serialized = json.dumps(report, ensure_ascii=False)
        assert "master-key" not in serialized
        assert "this prompt must not be exported" not in serialized


def test_diagnostic_zip_contains_sanitized_failure_context(tmp_path: Path) -> None:
    with TestClient(app_v8(tmp_path)) as client:
        client.post(
            "/v1/chat/completions",
            headers=headers(),
            json={"model": "default", "messages": [{"role": "user", "content": "private diagnostic prompt"}]},
        )
        response = client.get("/api/admin/diagnostics/export?limit=50", headers=headers())
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/zip")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = set(archive.namelist())
            assert {"summary.json", "requests.json", "server_events.json", "failures.json", "extensions.json", "models.json", "README.txt"}.issubset(names)
            summary = json.loads(archive.read("summary.json"))
            assert summary["server_version"] == "0.8.0"
            assert summary["privacy"]["api_keys_included"] is False
            combined = b"\n".join(archive.read(name) for name in names if name.endswith((".json", ".txt")))
            assert b"master-key" not in combined
            assert b"private diagnostic prompt" not in combined


def test_admin_v8_adds_global_and_per_request_log_downloads() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "admin_v8.js").read_text(encoding="utf-8")
    assert "下载诊断日志包" in source
    assert "data-request-log" in source
    assert "/api/admin/diagnostics/export" in source
    assert "/log" in source
