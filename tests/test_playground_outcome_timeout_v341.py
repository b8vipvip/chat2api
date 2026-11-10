"""The browser's same-ID outcome polling must not wait forever on one GET.

Only the secondary admin outcome GET may be aborted; neither the model POST
nor the Broker/Worker request terminal is owned by this timer.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_internal_admin_api_passes_explicit_abort_signal_to_fetch():
    source = (ROOT / "app/admin.py").read_text(encoding="utf-8")
    assert "keepalive:opt.keepalive===true,signal:opt.signal" in source


def test_abcd_recovery_get_has_per_attempt_deadline_and_cleanup():
    source = (ROOT / "app/admin_playground_abcd_v197.js").read_text()
    start = source.index("async function recoverDisconnectedStream(")
    end = source.index("\nasync function stream(", start)
    recovery = source[start:end]
    assert "new AbortController()" in recovery
    assert "outcomeController.abort(),8000" in recovery
    assert "{signal:outcomeController.signal}" in recovery
    assert "clearTimeout(outcomeTimeout)" in recovery
    assert "if(i<229) await new Promise(" in recovery
    # Request is looked up by original ID only, never newly dispatched.
    assert "/outcome?full_text=1" in recovery
    assert "/v1/chat/completions" not in recovery
