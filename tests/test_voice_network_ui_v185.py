"""Actual TX03 voice UI gate regressions: onboarding, visible toast and ICE evidence."""
import subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def test_voice_welcome_gate_uses_real_evidence():
    result=subprocess.run(["node","tests/voice_network_ui_v185.mjs"],
        cwd=ROOT,capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr


def test_voice_error_does_not_get_swallowed():
    speech=(ROOT/"chrome_extension/content_voice_v2.js").read_text()
    live=(ROOT/"chrome_extension/content_voice_live.js").read_text()
    main=(ROOT/"chrome_extension/voice_main.js").read_text()
    routing=(ROOT/"chrome_extension/audio_routing_v4.js").read_text()
    live_routing=(ROOT/"chrome_extension/audio_routing_live.js").read_text()
    assert "page-hidden-before-voice" not in speech
    assert "VOICE_PAGE_HIDDEN" not in live
    assert "settleVoiceWelcome" in speech and "settleVoiceWelcome" in live
    assert 'VOICE_CHATGPT_CONNECT_FAILED' in (ROOT/"chrome_extension/content_voice_ui_gate_v185.js").read_text()
    assert "voice.transport.ice-error" in main
    assert "ice_candidate_errors" in main and "ice_connection_state" in main
    assert "content_voice_ui_gate_v185.js" in routing and "content_voice_ui_gate_v185.js" in live_routing
    for path in ["content_voice_ui_gate_v185.js","content_voice_v2.js","content_voice_live.js",
                 "audio_routing_live.js","audio_routing_v4.js","voice_main.js"]:
        proc=subprocess.run(["node","--check",str(ROOT/"chrome_extension"/path)],
                            capture_output=True,text=True,timeout=10)
        assert proc.returncode==0,proc.stdout+proc.stderr


def test_fast_voice_failure_includes_request_scoped_ice_snapshot():
    speech=(ROOT/"chrome_extension/content_voice_v2.js").read_text()
    live=(ROOT/"chrome_extension/content_voice_live.js").read_text()
    assert speech.count("await failChatgptVoiceConnection(active, failure)") == 2
    assert live.count("await failLiveVoiceConnection(failure)") == 2
    for source in (speech, live):
        assert 'postMain("voice.transport.snapshot", active)' in source
        assert 'item.type === "voice.transport.snapshot"' in source
        assert "ice_candidate_errors: Number(snapshot?.ice_candidate_errors || 0)" in source
        assert "transport_snapshot_received: Boolean(snapshot)" in source
    assert 'kind: "voice-live"' in live

def test_live_voice_final_telemetry_preserves_worker_ice_diagnostics():
    server=(ROOT/"app/live_voice_patch.py").read_text()
    assert "**state.diagnostics" in server
    assert '"browser_startup_error": error_message' in server
