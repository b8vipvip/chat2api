from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_dictation_v3_auto_submits_transcription_and_ends_mic() -> None:
    source = (EXTENSION / "content_dictation_v3.js").read_text(encoding="utf-8")
    assert "__CHAT2API_DICTATION_CONTENT_V3__" in source
    assert "chat2api.dictation.request.v3" in source
    assert "autoSendTranscription" in source
    assert "sendButton" in source
    assert 'postMain("voice.mic.stop"' in source
    assert 'diagnostic(active, "mic-ended"' in source
    assert 'diagnostic(active, "transcription-ready"' in source
    assert 'diagnostic(active, "send-triggered"' in source
    assert 'diagnostic(active, "send-confirmed"' in source
    assert "send_confirmed: true" in source
    assert "auto_send: true" in source
    assert 'sent: true' in source
    assert source.index('diagnostic(active, "transcription-ready"') < source.index("await autoSendTranscription(active, text)")
    assert source.index("await autoSendTranscription(active, text)") < source.index('type: "image.completed"')


def test_audio_router_targets_dictation_v3_only() -> None:
    source = (EXTENSION / "audio_routing_v2.js").read_text(encoding="utf-8")
    assert "chat2api.dictation.request.v3" in source
    assert "chat2api.dictation.cancel.v3" in source
    assert "chat2api.dictation.request.v2" not in source
    assert '"dictation-auto-send"' in source
