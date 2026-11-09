"""2026-11-09 req_4ab3: HTTP peer vanished at 2829ms despite recent
ASGI keepalive, while the one Worker reply completed after 214 seconds.

This suite only verifies bounded client-page lifecycle *facts*. It must not
make pagehide evidence another stream terminal or mark progress as failure.
"""
from __future__ import annotations

import asyncio
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.playground_chat_patch import PlaygroundChatStart, _running_row, _chat_run_id

RID="req_"+"a"*32

def test_v312_browser_pagehide_is_sticky_and_does_not_mark_progress_failed(tmp_path):
    app=create_app(Settings(
        CHAT2API_API_KEY="master-key", CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://localhost.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=1,
    ))
    run=_running_row(PlaygroundChatStart(
        request_id=RID,model="gpt-5.6-sol",prompt="safe fixture",
        transport_contract="abcd-real-sse-v293",abcd_scene="A",
    ))
    asyncio.run(app.state.test_runs.upsert(run))
    route="/api/admin/playground/abcd/transport-truth"
    def body(facts,phase="progress",failed=False):
        return {"request_id":RID,"scene":"A","phase":phase,
                "failed":failed,"client_sse_truth":facts}
    with TestClient(app) as client:
        headers={"Authorization":"Bearer master-key"}
        # Browser uses a separate authenticated *small* keepalive POST
        # during pagehide. The API must not infer completion or failure.
        page=client.post(route,headers=headers,json=body({
            "response_headers_received":True,
            "response_headers_ms":662,
            "client_pagehide_seen":True,
            "client_pagehide_after_ms":2829,
            "client_pagehide_persisted":False,
            "client_visibility_hidden_seen":True,
            "client_visibility_hidden_after_ms":2770,
            "reader_cancel_requested":False,
            "protocol_done_seen":False,
            "untrusted_user_prompt":"DO NOT LOG",
        }))
        assert page.status_code==200,page.text
        quality=app.state.test_runs.get(_chat_run_id(RID))["quality"]
        assert quality["client_sse_failed"] is False
        assert quality["client_sse_truth_phase"]=="progress"
        facts=quality["client_sse_truth_v293"]
        assert facts["client_pagehide_seen"] is True
        assert facts["client_pagehide_after_ms"]==2829
        assert facts["client_visibility_hidden_seen"] is True
        assert "untrusted_user_prompt" not in facts
        # A delayed earlier 664ms progress checkpoint must not erase
        # lifecycle truth or mislabel a healthy stream as failed.
        old=client.post(route,headers=headers,json=body({
            "response_headers_received":True,"read_elapsed_ms":664,
            "client_pagehide_seen":False,
            "client_visibility_hidden_seen":False,
            "sse_body_chunks":1,"parsed_frames":1,
        }))
        assert old.status_code==200
        quality=app.state.test_runs.get(_chat_run_id(RID))["quality"]
        facts=quality["client_sse_truth_v293"]
        assert quality["client_sse_failed"] is False
        assert facts["client_pagehide_seen"] is True
        assert facts["client_pagehide_after_ms"]==2829
        assert facts["client_visibility_hidden_after_ms"]==2770
        # An actual reader error is still a failure of browser transport,
        # not a Broker chat.error terminal; a later progress cannot undo it.
        incomplete=client.post(route,headers=headers,json=body({
            "response_headers_received":True,"reader_cancel_requested":True,
            "reader_error_name":"AbortError","protocol_done_seen":False
        },phase="incomplete",failed=True))
        assert incomplete.status_code==200
        quality=app.state.test_runs.get(_chat_run_id(RID))["quality"]
        assert quality["client_sse_failed"] is True
        assert quality["client_sse_truth_phase"]=="incomplete"
        assert quality["client_sse_truth_v293"]["client_pagehide_seen"] is True
        ignored=client.post(route,headers=headers,json=body({
            "client_pagehide_seen":False,
            "reader_cancel_requested":False
        }))
        assert ignored.status_code==200
        assert ignored.json()["saved"] is False
        assert app.state.test_runs.get(_chat_run_id(RID))["quality"][
            "client_sse_truth_v293"]["client_pagehide_seen"] is True

def test_v312_pagehide_evidence_requires_authorized_ABCD_run(tmp_path):
    app=create_app(Settings(
        CHAT2API_API_KEY="master-key",CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://localhost.test",
        CHAT2API_DATA_DIR=tmp_path,
    ))
    with TestClient(app) as client:
        route="/api/admin/playground/abcd/transport-truth"
        payload={"request_id":RID,"scene":"A","phase":"progress",
                 "client_sse_truth":{"client_pagehide_seen":True}}
        assert client.post(route,json=payload).status_code==403
        assert client.post(route,json=payload,
            headers={"Authorization":"Bearer master-key"}).status_code==409
