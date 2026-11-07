"""Regression for actual 2026-11-07 19:28 chat HTTP 500 traceback."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.user_console_v104_patch import install_user_console_v104_patch
from app.playground_chat_patch import PlaygroundChatStart, _running_row, _chat_run_id
from app.playground_stream_contract_v293 import ABCD_TRANSPORT

RID = "req_" + "d" * 32


def create(tmp_path):
    app = create_app(Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://localhost.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=1,
    ))
    # This exact patch wraps app.state.telemetry.upsert() in production.
    install_user_console_v104_patch(app)
    return app


def test_billing_upsert_preserves_diagnostics_keyword_and_only_accounts_once(tmp_path):
    app = create(tmp_path)
    records = []
    app.state.user_accounts.owner_for_key = lambda key: "owner" if key == "key_test" else None
    app.state.user_billing.record_charge = lambda owner, row, pricing: records.append(
        (owner, row["request_id"])
    )
    store = app.state.telemetry
    asyncio.run(store.upsert({
        "request_id": RID, "status": "running", "api_key_id": "key_test",
        "diagnostics": {"worker_message": "original"},
    }))
    asyncio.run(store.merge_diagnostics(RID, {
        "playground_transport_attribution_v293": {"kind": "abcd-real-sse"},
    }))
    row = store.get(RID)
    assert row["diagnostics"]["worker_message"] == "original"
    assert row["diagnostics"]["playground_transport_attribution_v293"]["kind"] == "abcd-real-sse"
    assert records == [], "passive diagnostic writes must not bill"
    asyncio.run(store.upsert({"request_id": RID, "status": "completed"}))
    assert records == [("owner", RID)]
    asyncio.run(store.merge_diagnostics(RID, {"transport_after_terminal": True}))
    assert records == [("owner", RID)], "late SSE evidence must not trigger a second charge"


def test_real_api_does_not_http_500_when_playground_origin_audit_runs(tmp_path):
    app = create(tmp_path)
    running = _running_row(PlaygroundChatStart(
        request_id=RID, model="gpt-5.6-sol", prompt="safe fixture",
        transport_contract=ABCD_TRANSPORT, abcd_scene="A",
    ))
    with TestClient(app) as client:
        asyncio.run(app.state.test_runs.upsert(running))
        response = client.post("/v1/chat/completions",
            json={"model": "gpt-5.6-sol", "stream": True,
                  "messages": [{"role": "user", "content": "safe fixture"}]},
            headers={
                "Authorization": "Bearer master-key",
                "X-Chat2API-Request-ID": RID,
                "X-Chat2API-Playground-Transport": ABCD_TRANSPORT,
                "Origin": "https://localhost.test",
                "Referer": "https://localhost.test/admin",
            })
        # No real Worker is registered; the API may return a normal
        # admission 409/503 but MUST NEVER throw metered_upsert TypeError/500.
        assert response.status_code != 500, response.text
        row = app.state.telemetry.get(RID)
        assert row is not None
        assert row["diagnostics"]["playground_transport_attribution_v293"]["kind"] == "abcd-real-sse"
        assert row["diagnostics"]["playground_transport_attribution_v293"]["scene"] == "A"
