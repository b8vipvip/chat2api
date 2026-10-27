"""Run the v217 cancellation/next-request tab-ownership race regression."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_request_bound_cancel_dispatch_vm():
    result = subprocess.run(
        ["node", str(ROOT / "tests/cancel_control_dispatch_v217.mjs")],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=20,
    )
    assert result.returncode == 0, result.stderr or result.stdout

def test_worker_routes_cancel_only_through_serial_dispatch_chain():
    source = (ROOT / "chrome_extension/conversation_dispatch.js").read_text()
    assert 'const isTargetedControl = ["chat.cancel", "image.cancel", "voice.cancel"].includes(message?.type);' in source
    assert 'if (!target?.tabId) return baseHandleServerMessage(message)' not in source
    assert 'router.failRequest(requestId, "server-cancel-control-v136")' in source
