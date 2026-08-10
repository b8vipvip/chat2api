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
    # v4 has an earlier stopSyntheticMic call in its failure cleanup branch. Verify
    # the success-path stop that occurs after transcription-ready and before auto-send.
    transcription_at = source.index('diagnostic(active, "transcription-ready"')
    success_stop_at = source.index("await stopSyntheticMic(active)", transcription_at)
    autosend_at = source.index("await autoSend(active, text)", success_stop_at)
    completed_at = source.index('type: "image.completed"', autosend_at)
    assert transcription_at < success_stop_at < autosend_at < completed_at


def test_audio_router_targets_dictation_v4_only_for_new_requests() -> None:
    source = (EXTENSION / "audio_routing_v2.js").read_text(encoding="utf-8")
    assert "chat2api.dictation.request.v4" in source
    assert "chat2api.dictation.cancel.v4" in source
    assert "chat2api.dictation.request.v2" not in source
    assert "chat2api.dictation.request.v3" not in source
    assert '"dictation-auto-send"' in source
    assert "activateAudioTab" in source
