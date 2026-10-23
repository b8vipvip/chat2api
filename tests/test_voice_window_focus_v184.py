"""TX03 voice transport regression: a visible orb is not a usable audio session."""
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_chrome_voice_must_foreground_tab_and_window():
    process = subprocess.run(
        ["node", "tests/voice_window_focus_v184.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=15,
    )
    assert process.returncode == 0, process.stdout + process.stderr


def test_voice_workflows_share_focus_and_preserve_fatal_browser_error():
    routes = (ROOT / "chrome_extension/audio_routing_v4.js").read_text()
    live = (ROOT / "chrome_extension/audio_routing_live.js").read_text()
    server = (ROOT / "app/live_voice_patch.py").read_text()
    assert "globalThis.chat2apiActivateVoiceTabV184 = activateAudioTab" in routes
    assert "const activate = globalThis.chat2apiActivateVoiceTabV184" in live
    assert "finishLiveRoute(requestId)" in live
    assert "await finishLiveRoute(requestId)" in live
    assert "voice-live-session-ended-v189" in (ROOT / "chrome_extension/conversation_routing.js").read_text()
    assert 'if event.get("type") == "image.error":' in server
    assert 'error_message = str(event.get("error") or "ChatGPT Voice bridge failed")' in server
    assert '"browser_startup_error": error_message if error_message and not session_ready else None' in server
    for path in ("audio_routing_v4.js", "audio_routing_live.js", "content_voice_v2.js", "content_voice_live.js", "voice_main.js"):
        process = subprocess.run(["node", "--check", str(ROOT / "chrome_extension" / path)],
                                 capture_output=True, text=True, timeout=8)
        assert process.returncode == 0, process.stdout + process.stderr
