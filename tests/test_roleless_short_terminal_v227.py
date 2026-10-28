from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def test_v227_roleless_short_reply_and_single_text_publisher() -> None:
    result = subprocess.run(
        ["node", str(ROOT / "tests/roleless_short_terminal_single_text_v227.mjs")],
        cwd=ROOT, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr

def test_v227_network_observer_is_evidence_not_a_terminal_owner() -> None:
    content = (ROOT / "chrome_extension/content_network_stream_recovery_v55.js").read_text()
    assert '"chat.completed"' not in content
    assert 'type: "chat.snapshot"' not in content
    assert "active.networkObservedText = value" in content
    assert "network_stream_error" in content
