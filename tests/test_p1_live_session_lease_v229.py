"""GPT-Live per-session physical window authority and generated-file batch safety."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def test_v229_gptlive_session_window_lifecycle():
    if not shutil.which("node"):
        return
    completed = subprocess.run(
        ["node", "tests/live_session_route_lease_v229.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=25,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr

def test_v229_live_controls_are_not_request_terminals():
    router = (ROOT / "chrome_extension/conversation_routing.js").read_text()
    audio = (ROOT / "chrome_extension/audio_routing_live.js").read_text()
    pool = (ROOT / "chrome_extension/conversation_persistent_pool_v132.js").read_text()
    assert 'message?.type === "voice.live.start"' in router
    assert 'route.live_session = liveSession;' in router
    assert 'await scheduleClose(route);' in router
    assert 'state.liveTabForRequest = liveTabForRequest;' in router
    assert 'state.finishLiveSession = finishLiveSession;' in router
    assert 'return `${value}::live::${requestId}`' in pool
    assert 'await releaseLiveSession(requestId, "voice-live-stop");' in audio
    assert 'if (type === "voice.live.stop")' in audio
