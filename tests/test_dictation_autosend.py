from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_dictation_v4_auto_submits_after_graceful_finish() -> None:
    source = (EXTENSION / "content_dictation_v4.js").read_text(encoding="utf-8")
    assert "__CHAT2API_DICTATION_CONTENT_V4__" in source
    assert "chat2api.dictation.request.v4" in source
    assert "autoSend" in source
    assert "sendButton" in source
    assert 'diagnostic(active, "transcription-ready"' in source
    assert 'diagnostic(active, "send-triggered"' in source
    assert 'diagnostic(active, "send-confirmed"' in source
    assert "send_confirmed: true" in source
    assert "auto_send: true" in source
    assert 'sent: true' in source
    # v4 deliberately waits for ChatGPT to finish Dictation and publish the text
    # before stopping the synthetic microphone and auto-sending the transcription.
    assert source.index('diagnostic(active, "transcription-ready"') < source.index("await stopSyntheticMic(active)")
    assert source.index("await stopSyntheticMic(active)") < source.index("await autoSend(active, text)")
    assert source.index("await autoSend(active, text)") < source.index('type: "image.completed"')


def test_audio_router_targets_dictation_v4_only_for_new_requests() -> None:
    source = (EXTENSION / "audio_routing_v2.js").read_text(encoding="utf-8")
    assert "chat2api.dictation.request.v4" in source
    assert "chat2api.dictation.cancel.v4" in source
    assert "chat2api.dictation.request.v2" not in source
    assert "chat2api.dictation.request.v3" not in source
    assert '"dictation-auto-send"' in source
    assert "activateAudioTab" in source
