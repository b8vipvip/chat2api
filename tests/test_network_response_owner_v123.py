from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "chrome_extension"


def test_network_observer_uses_v6_identity_even_when_v5_active_disagrees():
    proc = subprocess.run(
        ["node", "tests/network_response_owner_v123.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_legacy_v5_handler_cannot_send_after_v6_installed():
    source = (EXT / "content_request_v5.js").read_text(encoding="utf-8")
    assert source.count("globalThis.__CHAT2API_REQUEST_CONTENT_V6__?.listener") >= 2


def test_enter_fallback_preserves_first_submit_timestamp():
    source = (EXT / "content_request_v6.js").read_text(encoding="utf-8")
    start = source.index("async function submitAndConfirm(active)")
    finish = source.index("async function updateCapturedText(active",start)
    body = source[start:finish]
    assert body.count("active.submitStartedAt = Date.now()") == 1
    assert 'reason: "network-terminal-evidence"' in source
    assert "submitAt > 0 && terminalText && terminalAt >= submitAt" in source
