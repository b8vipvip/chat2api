"""Live evidence: v0.22.291 manual chat d6de0387 still used browser stream=true.
Refuse only obsolete admin manual-browser transport before Worker admission;
current ABCD true streaming remains allowed by explicit transport marker.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app.config import Settings
from app.main import create_app
from app.playground_chat_patch import (
    PlaygroundChatStart, _chat_run_id, _running_row,
)
from app.playground_stream_contract_v293 import (
    ABCD_TRANSPORT, is_legacy_manual_playground_sse,
)

RID = "req_" + "a" * 32
BROWSER = {
    "origin": "https://chat2api.mv3.cn",
    "referer": "https://chat2api.mv3.cn/admin",
}
RUN = {"run_id": _chat_run_id(RID), "request_id": RID, "request_ids": [RID]}


class Runs:
    def __init__(self, value=RUN):
        self.value = value

    def get(self, run_id):
        return self.value if self.value and self.value["run_id"] == run_id else None


def detected(headers=BROWSER, stream=True, runs=None, rid=RID):
    return is_legacy_manual_playground_sse(
        request_id=rid, stream=stream,
        headers=Headers(headers),
        test_runs=runs or Runs(),
    )


def test_old_manual_browser_stream_is_detected_but_not_general_api():
    assert detected() is True
    assert detected({**BROWSER, "x-chat2api-playground-transport": ABCD_TRANSPORT}) is False
    assert detected(stream=False) is False
    assert detected(headers={}) is False
    assert detected(headers={"origin": BROWSER["origin"], "referer": "https://other.example/admin"}) is False
    assert detected(headers={"origin": BROWSER["origin"], "referer": "https://chat2api.mv3.cn/administration"}) is False
    assert detected(runs=Runs(None)) is False
    assert detected(rid="req_" + "b" * 32) is False
    assert detected(headers={**BROWSER, "x-chat2api-playground-transport": "legacy-direct-sse"}) is True


def test_migration_409_stops_outdated_browser_before_a_worker_is_scheduled(tmp_path: Path):
    settings = Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.mv3.cn",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        asyncio.run(app.state.test_runs.upsert(RUN))
        response = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-6", "stream": True,
                  "messages": [{"role": "user", "content": "safe probe"}]},
            headers={
                **BROWSER, "Authorization": "Bearer master-key",
                "X-Chat2API-Request-ID": RID,
            },
        )
        assert response.status_code == 409, response.text
        assert response.headers["x-chat2api-playground-refresh-required"] == "true"
        assert "Ctrl+F5" in response.json()["detail"]
        assert app.state.telemetry.get(RID) is None
        assert RID not in app.state.broker.requests


def test_new_runs_record_the_transport_without_changing_v3_schema():
    for kind in ("server-detached-v292", ABCD_TRANSPORT, "legacy-direct-sse"):
        body = PlaygroundChatStart(
            request_id=RID, model="gpt-6", prompt="probe",
            transport_contract=kind,
        )
        run = _running_row(body)
        assert run["quality"]["playground_chat"] == "playground-chat-v3"
        assert run["quality"]["transport_contract"] == kind
        assert run["request_id"] == RID
