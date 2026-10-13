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


def test_canonical_completion_has_no_prompt_specific_format_arbiter():
    request = (ROOT / "chrome_extension/content_request_v6.js").read_text(encoding="utf-8")
    network = (ROOT / "chrome_extension/content_network_stream_recovery_v55.js").read_text(encoding="utf-8")
    server = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert "reconcileStrictTerminal" not in request
    assert "requiresStrictJsonTen" not in request
    assert "enforce_nonstream_contract(" not in server
    assert 'text: lastText || networkTerminalText' in request
    assert 'type: "chat.completed"' not in network
    assert 'String(detail.parser_source || "") === "json-patch"' in network
