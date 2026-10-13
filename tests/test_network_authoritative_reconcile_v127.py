from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_authoritative_sse_rewrite_and_strict_json():
    if not shutil.which("node"):
        return
    run = subprocess.run(["node", "tests/network_authoritative_reconcile_v127.mjs"], cwd=ROOT,
                         text=True, capture_output=True, timeout=25)
    assert run.returncode == 0, run.stdout + run.stderr


def test_completion_wait_is_scoped_to_strict_json():
    request = (ROOT / "chrome_extension/content_request_v6.js").read_text(encoding="utf-8")
    network = (ROOT / "chrome_extension/content_network_stream_recovery_v55.js").read_text(encoding="utf-8")
    assert "await waitFor(chooseValid, 6000, 160)" in request
    assert 'response_terminal_strict_json_valid:' in request
    assert "response_terminal_reconciled_source:" in request
    assert "detail.completion_hint === true" in network
    assert 'String(detail.parser_source || "") === "json-patch"' in network
