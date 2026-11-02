"""Deployment 35ce1dc: native snapshot ACK and terminal diagnostic regressions."""
from pathlib import Path
import subprocess
from app.broker import _terminal_provenance, RequestBroker
import asyncio

ROOT=Path(__file__).resolve().parents[1]

def test_native_snapshot_ack_nested_revision_vm():
    result=subprocess.run(["node","tests/native_snapshot_ack_revision_v640.mjs"],
                          cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr

def test_terminal_native_diagnostics_allowlist_and_boundaries():
    event={"diagnostics":{
        "native_observer_loaded": True,
        "native_capture_attempted": True,
        "native_capture_failure": "structural-max-depth",
        "native_transport_ack_error": "invalid-native-envelope",
        "native_structural_failure": "max-depth",
        "native_capture_revision": 7,
        "native_chunk_complete": False,
        "native_identity_candidate_count": 3,
        "native_identity_verified_count": 1,
        "native_token": "DO_NOT_EXPOSE",
        "native_dump": {"url":"https://secret.example"},
    }}
    result=_terminal_provenance(event)
    for key in ("native_observer_loaded","native_capture_attempted",
                "native_capture_failure","native_transport_ack_error",
                "native_structural_failure","native_capture_revision",
                "native_chunk_complete","native_identity_candidate_count",
                "native_identity_verified_count"):
        assert result[key] == event["diagnostics"][key]
    assert "native_token" not in result and "native_dump" not in result
    event["diagnostics"]["native_transport_ack_error"]="A"*500
    assert len(_terminal_provenance(event)["native_transport_ack_error"]) <= 100

def test_native_diagnostics_survive_authoritative_completed_terminal():
    async def exercise():
        broker=RequestBroker()
        state=await broker.create("req_nativeackdiag001","ext_native001")
        accepted=await broker.publish(state.request_id,{
            "type":"chat.completed","request_id":state.request_id,
            "text":"assistant reply",
            "diagnostics":{"native_capture_failure":"native-transport-incomplete",
                "native_observer_loaded":True,"native_capture_attempted":True,
                "native_chunk_complete":False,"native_transport_ack_error":"invalid-native-envelope"}})
        assert accepted
        assert state.text=="assistant reply"
        assert state.terminal_event_type=="chat.completed"
        assert state.diagnostics["native_transport_ack_error"]=="invalid-native-envelope"
        await broker.release(state.request_id)
    asyncio.run(exercise())



def test_native_conversation_url_saved_only_for_real_chatgpt_history():
    good="https://chatgpt.com/c/6ae7dac6-8328-83e8-878d-fc3599d7b10b"
    payload={"diagnostics":{"native_chatgpt_conversation_url":good}}
    assert _terminal_provenance(payload)["native_chatgpt_conversation_url"]==good
    for bad in (
        good+"?token=hidden",good+"#anchor",
        "http://chatgpt.com/c/6ae7dac6-8328-83e8-878d-fc3599d7b10b",
        "https://evil.example/c/6ae7dac6-8328-83e8-878d-fc3599d7b10b",
        "https://chatgpt.com.evil.example/c/6ae7dac6-8328-83e8-878d-fc3599d7b10b",
        "https://chatgpt.com/",
        "https://user:password@chatgpt.com/c/6ae7dac6-8328-83e8-878d-fc3599d7b10b",
    ):
        assert "native_chatgpt_conversation_url" not in _terminal_provenance({
            "diagnostics":{"native_chatgpt_conversation_url":bad}})
    from pathlib import Path
    js=(ROOT/"chrome_extension/content_request_v6.js").read_text()
    assert "rememberNativeConversationUrl(active);" in js
    assert "active.nativeChatgptConversationUrl" in js
    assert "native_chatgpt_conversation_url:nativeConversationUrl" in js
    assert "chat.error" in js and "chat.cancelled" in js
