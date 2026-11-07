"""ABCD A has no report when SSE breaks: avoid misclassifying it as manual."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app.config import Settings
from app.main import create_app
from app.playground_chat_patch import (
    PlaygroundChatStart, _chat_run_id, _running_row, install_playground_chat_patch,
)
from app.playground_lifecycle_patch import install_playground_lifecycle_patch
from app.playground_stream_contract_v293 import (
    ABCD_TRANSPORT, DETACHED_TRANSPORT, playground_stream_attribution,
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


def observed(headers=BROWSER, stream=True, runs=None, rid=RID):
    return playground_stream_attribution(
        request_id=rid, stream=stream,
        headers=Headers(headers), test_runs=runs if runs is not None else Runs(),
    )


def test_unknown_browser_sse_is_not_misclassified_as_manual():
    # A failed ABCD run can be missing its final abcd_report. This must not
    # cause a 409, invented success or a confident manual-chat attribution.
    result = observed()
    assert result["kind"] == "unverified-browser-direct-sse"
    assert result["scene"] is None
    assert result["source"] == "matched-playground-run-v293"
    assert observed({**BROWSER, "x-chat2api-playground-transport": ABCD_TRANSPORT})[
        "kind"
    ] == "abcd-real-sse"
    abc = {**RUN, "quality": {
        "abcd_scene": "A", "transport_contract": ABCD_TRANSPORT,
    }}
    result = observed(runs=Runs(abc))
    assert result["kind"] == "abcd-scene-declared-sse"
    assert result["scene"] == "A"
    detached = observed(headers={
        "x-chat2api-playground-dispatch": DETACHED_TRANSPORT,
    }, stream=False)
    assert detached["kind"] == "manual-server-detached"
    assert observed(headers={}) is None
    assert observed(runs=Runs(None)) is None
    assert observed(rid="req_" + "b" * 32) is None
    assert observed(headers={**BROWSER, "referer": "https://elsewhere.invalid/admin"}) is None


def test_declared_scene_is_stored_before_dispatch_for_future_zip():
    for kind in ("server-detached-v292", ABCD_TRANSPORT, "legacy-direct-sse"):
        body = PlaygroundChatStart(
            request_id=RID, model="gpt-6", prompt="test",
            transport_contract=kind,
            abcd_scene="A" if kind == ABCD_TRANSPORT else None,
        )
        run = _running_row(body)
        assert run["quality"]["playground_chat"] == "playground-chat-v3"
        assert run["quality"]["transport_contract"] == kind
        assert run["quality"]["abcd_scene"] == (
            "A" if kind == ABCD_TRANSPORT else None
        )


def _app(tmp_path: Path):
    app = create_app(Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.mv3.cn",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    ))
    install_playground_lifecycle_patch(app)
    install_playground_chat_patch(app)
    return app


def test_client_sse_evidence_survives_missing_abcd_final_report(tmp_path: Path):
    app = _app(tmp_path)
    h = {"Authorization": "Bearer master-key"}
    with TestClient(app) as client:
        run = _running_row(PlaygroundChatStart(
            request_id=RID, model="gpt-5.6-sol", prompt="private prompt",
            abcd_scene="A", transport_contract=ABCD_TRANSPORT,
        ))
        asyncio.run(app.state.test_runs.upsert(run))
        path = "/api/admin/playground/abcd/transport-truth"
        payload = {"request_id": RID, "scene": "A", "failed": True,
            "client_sse_truth": {
                "response_headers_received": True, "protocol_done_seen": False,
                "reader_error_name": "TypeError", "read_elapsed_ms": 10750,
                "parsed_frames": 5, "private_prompt": "should never persist",
                "raw_error": "do not store raw exceptions",
            }}
        assert client.post(path, json=payload).status_code == 403
        assert client.post(path, headers=h,
                           json={**payload, "scene": "C"}).status_code == 409
        saved = client.post(path, headers=h, json=payload)
        assert saved.status_code == 200, saved.text
        assert saved.json()["facts"] == 5
        row = app.state.test_runs.get(run["run_id"])
        assert row["status"] == "running"
        assert row.get("abcd_report") is None
        assert row["quality"]["abcd_scene"] == "A"
        facts = row["quality"]["client_sse_truth_v293"]
        assert facts["read_elapsed_ms"] == 10750
        assert facts["reader_error_name"] == "TypeError"
        assert "private_prompt" not in facts and "raw_error" not in facts
