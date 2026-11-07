"""Request history badges use verified TestRunStore records, not model/prompt guesses."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.playground_request_evidence_v207 import (
    playground_request_badge, playground_link,
)
from app.playground_chat_patch import PlaygroundChatStart, _running_row


def test_run_badge_authority_and_safe_legacy_fallback():
    assert playground_request_badge(None) == ""
    assert playground_link(None)["is_playground_test"] is False
    for scene in "ABCD":
        # Every ABCD run has test_type=chat; scene MUST win over chat.
        run = {"run_id": "chatrun_1", "test_type": "chat",
               "quality": {"playground_chat": "playground-chat-v3",
                           "abcd_scene": scene}}
        assert playground_request_badge(run) == "测" + scene
        assert playground_link(run)["playground_request_badge"] == "测" + scene
    assert playground_request_badge({
        "run_id": "chatrun_abcd", "test_type": "chat",
        "quality": {"playground_chat": "playground-chat-v3"},
    }) == "测聊"
    assert playground_request_badge({
        "run_id": "testrun_abc", "test_type": "all",
        "quality": {"lifecycle": "playground-lifecycle-v1"},
    }) == "测自"
    assert playground_request_badge({
        "run_id": "testrun_def", "test_type": "text",
        "quality": {},
    }) == "测自"
    assert playground_request_badge({
        "run_id": "an_unverified_historical_test", "test_type": "chat",
    }) == "测"
    # The old ABCD report can recover a known scene only when it carries
    # a concrete valid A-D identity, not by parsing free-form text.
    assert playground_request_badge({
        "run_id": "chatrun_old", "test_type": "chat",
        "abcd_report": {"scene": "C"}, "quality": {},
    }) == "测C"


def test_admin_request_list_uses_exact_linked_request_ids(tmp_path):
    app = create_app(Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://localhost.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=1,
    ))
    h = {"Authorization": "Bearer master-key"}
    mapping = {
        "A": "测A", "B": "测B", "C": "测C", "D": "测D",
        "chat": "测聊", "automatic": "测自",
        "external": None,
    }
    with TestClient(app) as client:
        for idx, (kind, expected) in enumerate(mapping.items()):
            rid = "req_" + f"{idx+1:032x}"
            asyncio.run(app.state.telemetry.upsert({
                "request_id": rid, "status": "completed",
                "requested_model": "gpt-6",
            }))
            if kind in "ABCD" and len(kind) == 1:
                row = _running_row(PlaygroundChatStart(
                    request_id=rid, model="gpt-6", prompt="fixture",
                    abcd_scene=kind, transport_contract="abcd-real-sse-v293",
                ))
            elif kind == "chat":
                row = _running_row(PlaygroundChatStart(
                    request_id=rid, model="gpt-6", prompt="fixture",
                    transport_contract="server-detached-v292",
                ))
            elif kind == "automatic":
                row = {
                    "run_id": "testrun_" + f"{idx+1:032x}",
                    "request_id": rid, "request_ids": [rid], "test_type": "all",
                    "status": "running", "quality": {
                        "lifecycle": "playground-lifecycle-v1",
                    },
                }
            else:
                row = None
            if row:
                asyncio.run(app.state.test_runs.upsert(row))
        response = client.get("/api/admin/requests?limit=100", headers=h)
        assert response.status_code == 200, response.text
        rows = {row["request_id"]: row for row in response.json()["data"]}
        assert len(rows) == len(mapping)
        for idx, (kind, expected) in enumerate(mapping.items()):
            rid = "req_" + f"{idx+1:032x}"
            row = rows[rid]
            assert row["request_id"] == rid, "Never truncate the server ID"
            assert row.get("playground_request_badge") == expected
            assert row.get("is_playground_test") is (expected is not None)
        assert all(
            "playground_request_badge" not in row
            for row in app.state.telemetry.recent(100)
        ), "Badge is display-only; persisted request history stays unmodified"
        filtered = client.get(
            "/api/admin/requests?limit=100&source=playground", headers=h,
        )
        assert filtered.status_code == 200
        assert len(filtered.json()["data"]) == 6
        assert {row["playground_request_badge"] for row in filtered.json()["data"]} == {
            "测A", "测B", "测C", "测D", "测聊", "测自",
        }


def test_rendered_request_history_uses_server_badge_and_copy_full_id():
    from app.admin import admin_response
    from app.request_history_v94_patch import _normalize_admin_html
    html = _normalize_admin_html(admin_response().body.decode("utf-8"))
    for label in ("测聊", "测A", "测B", "测C", "测D", "测自"):
        assert label in html
    assert "r?.playground_request_badge" in html
    assert "requestHistoryContextCopy(" in html
    assert "requestHistoryCopyFullId(" in html
    assert "idCell.title=requestId" in html
