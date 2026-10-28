from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def test_v228_verified_eof_order_and_network_clone_error_facts():
    if not shutil.which("node"):
        return
    result = subprocess.run(
        ["node", "tests/network_clone_terminal_evidence_v228.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=35,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr

def test_v228_network_observer_keeps_one_terminal_owner():
    main = (ROOT / "chrome_extension/network_stream_main_v55.js").read_text()
    isolated = (ROOT / "chrome_extension/content_network_stream_recovery_v55.js").read_text()
    assert "network_stream_failure_stage" in isolated
    assert "network_stream_abort_signal" in isolated
    assert "done-with-verified-eof-proof" in isolated
    assert 'phase: "assistant-complete"' in main
    assert 'type: "chat.completed"' not in isolated
    assert 'type: "chat.snapshot"' not in isolated
