from __future__ import annotations

import asyncio
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.admin_auth import SESSION_COOKIE, AdminSessionStore
from app.config import Settings
from app.control_plane_v1_patch import install_control_plane_v1
from app.main import create_app
from app.user_console_v104_patch import install_user_console_v104_patch
from app.web_chat_client_diagnostics_v1 import (
    ClientDiagnosticEvent,
    WebChatClientDiagnosticStore,
    _normalize_event,
)
from app.web_chat_integration_v1 import install_web_chat_features_v1


def _install_web_chat_test_features(app, tmp_path: Path) -> None:
    install_control_plane_v1(app)
    shared_key, _shared_token = asyncio.run(app.state.api_keys.create("diagnostics test shared key"))
    app.state.control_plane.update_client({"api_key_id": shared_key["key_id"]})
    install_user_console_v104_patch(app)
    app.state.admin_sessions = AdminSessionStore(
        app.state.settings.admin_username,
        "diagnostics-test-password",
        storage_path=tmp_path / "diagnostics-test-admin-sessions.json",
    )
    install_web_chat_features_v1(app)


def _event(event_id: str = "event_test_0001") -> ClientDiagnosticEvent:
    return ClientDiagnosticEvent(
        client_event_id=event_id,
        event="chat.request.failed",
        level="error",
        occurred_at=datetime.now(timezone.utc).isoformat(),
        session_id="session_test_0001",
        request_id="req_diagnostics_0001",
        model_id="gpt-6",
        phase="dispatch",
        http_status=502,
        error_name="NetworkError",
        error_message="Bearer sk-secret-value-must-not-be-stored",
        details={
            "phase": "dispatch",
            "model_id": "gpt-6",
            "request_id": "req_diagnostics_0001",
            "duration_ms": 1200,
            "prompt": "private prompt must not be stored",
            "api_key": "sk-secret-value-must-not-be-stored",
        },
    )


def test_diagnostic_store_persists_deduplicates_and_redacts_across_instances(tmp_path: Path) -> None:
    store = WebChatClientDiagnosticStore(tmp_path)
    row = _normalize_event(_event(), "usr_diagnostics")
    assert row["error_message"] == ""
    assert "prompt" not in row["details"]
    assert "api_key" not in row["details"]
    assert row["details"]["model_id"] == "gpt-6"

    assert asyncio.run(store.append_many([row])) == 1
    reopened = WebChatClientDiagnosticStore(tmp_path)
    assert asyncio.run(reopened.append_many([row])) == 0
    rows = asyncio.run(reopened.query(limit=10, user_id="usr_diagnostics"))
    assert len(rows) == 1
    assert rows[0]["event"] == "chat.request.failed"
    assert rows[0]["error_message"] == ""
    assert rows[0]["details"]["request_id"] == "req_diagnostics_0001"
    assert asyncio.run(reopened.retained_count()) == 1


def test_client_diagnostic_ingestion_admin_query_and_zip_export(tmp_path: Path) -> None:
    settings = Settings(
        CHAT2API_API_KEY="web-chat-diagnostics-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)
    _install_web_chat_test_features(app, tmp_path)

    with TestClient(app) as client:
        origin = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
        unauthenticated = client.post(
            "/api/user/chat/client-logs",
            headers=origin,
            json={"events": [_event().model_dump()]},
        )
        assert unauthenticated.status_code == 401

        registered = client.post(
            "/api/user/register",
            headers=origin,
            json={"email": "diagnostics-owner@example.test", "password": "diagnostics-owner-password"},
        )
        assert registered.status_code == 200, registered.text
        user_id = registered.json()["user"]["user_id"]

        event = _event()
        accepted = client.post(
            "/api/user/chat/client-logs",
            headers=origin,
            json={"events": [event.model_dump()]},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json() == {"accepted": 1}
        duplicate = client.post(
            "/api/user/chat/client-logs",
            headers=origin,
            json={"events": [event.model_dump()]},
        )
        assert duplicate.status_code == 200
        assert duplicate.json() == {"accepted": 0}

        cross_origin = client.post(
            "/api/user/chat/client-logs",
            headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
            json={"events": [_event("event_test_0002").model_dump()]},
        )
        assert cross_origin.status_code == 403

        oversized = client.post(
            "/api/user/chat/client-logs",
            headers={**origin, "Content-Type": "application/json"},
            content=b" " * (64 * 1024 + 1),
        )
        assert oversized.status_code == 413

        assert client.get("/api/admin/diagnostics/web-chat-client-logs").status_code == 401
        token = app.state.admin_sessions.create("admin")
        client.cookies.set(SESSION_COOKIE, token)

        query = client.get("/api/admin/diagnostics/web-chat-client-logs?limit=50")
        assert query.status_code == 200, query.text
        payload = query.json()
        assert payload["count"] == 1
        assert payload["retained"] == 1
        row = payload["data"][0]
        assert row["user_id"] == user_id
        assert row["model_id"] == "gpt-6"
        assert row["error_message"] == ""
        assert "sk-secret-value" not in query.text
        assert "private prompt" not in query.text

        now = datetime.now(timezone.utc)
        since = (now - timedelta(minutes=1)).isoformat()
        until = (now + timedelta(minutes=1)).isoformat()
        time_query = client.get(
            "/api/admin/diagnostics/web-chat-client-logs",
            params={"since": since, "until": until},
        )
        assert time_query.status_code == 200
        assert time_query.json()["count"] == 1

        count_export = client.get("/api/admin/diagnostics/web-chat-client-logs/export?limit=20")
        assert count_export.status_code == 200
        assert count_export.headers["content-type"].startswith("application/zip")
        with zipfile.ZipFile(io.BytesIO(count_export.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            events = archive.read("client-events.jsonl").decode("utf-8")
            assert manifest["count"] == 1
            assert manifest["filter"] == {"limit": 20}
            assert "private prompt" not in events
            assert "sk-secret-value" not in events

        range_export = client.get(
            "/api/admin/diagnostics/web-chat-client-logs/export",
            params={"since": since, "until": until},
        )
        assert range_export.status_code == 200
        with zipfile.ZipFile(io.BytesIO(range_export.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["count"] == 1
            assert "since" in manifest["filter"] and "until" in manifest["filter"]
