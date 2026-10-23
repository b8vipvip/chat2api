"""The real Voice JS state-machine must enforce audio transport evidence."""
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_voice_transport_evidence_stage_gates():
    result = subprocess.run(
        ["node", "tests/voice_transport_evidence_v191.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=25,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_voice_mic_cleanup_does_not_release_new_session_resources():
    result = subprocess.run(
        ["node", "tests/voice_mic_cleanup_v192.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_voice_has_a_single_authority_for_error_and_audio_delivery():
    content = (ROOT / "chrome_extension/content_voice_v2.js").read_text(encoding="utf-8")
    main = (ROOT / "chrome_extension/voice_main.js").read_text(encoding="utf-8")
    voice = (ROOT / "app/voice_patch.py").read_text(encoding="utf-8")
    playground = (ROOT / "app/playground_lifecycle_patch.py").read_text(encoding="utf-8")
    assert content.count('type: "image.completed"') == 1
    assert content.count('type: "image.error"') == 1
    assert content.count('type: "image.diagnostics"') >= 1
    assert 'VOICE_TRANSPORT_NOT_READY' in content
    assert 'VOICE_TEXT_SUBMISSION_UNVERIFIED' in content
    assert 'VOICE_REMOTE_TRACK_NOT_FOUND' in content
    assert 'VOICE_REMOTE_AUDIO_SILENT' in content
    assert 'async function armRecording(active)' in content
    assert 'postMain("voice.record.arm", active)' in content
    assert 'postMain("voice.mic.stop", active)' in content
    assert 'const sequence = Number(item._voice_sequence || 0)' in content
    assert 'if (sequence && sequence <= lastSeenSequence) continue' in content
    assert 'if (state.captureArmed) startCapture(track, ownerRequestId)' in main
    assert 'if (recordingState.discarded) return' in main

    assert 'voice_failure_code' in voice
    assert 'VOICE_SERVER_WAIT_TIMEOUT' in voice
    assert '"timeout": 115' in playground
    assert '"timeout": 300' not in playground[
        playground.index("    async def _voice_api("):
        playground.index("    async def _run_voice(")
    ]
    assert 'state.peerHookReady = globalThis.RTCPeerConnection === WrappedRTCPeerConnection' in main
    assert 'state.micHookReady = mediaDevices.getUserMedia === intercepted' in main
