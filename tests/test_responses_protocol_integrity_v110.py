from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app import responses_emulated_tools_v109_patch as bridge

ROOT = Path(__file__).resolve().parents[1]
MARKER = "FDEX_CODEX_SMOKE_cdac2df519f84e20_WIRE"


def body() -> dict:
    return {
        "model": "gpt-5.6-sol",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": f"Reply exactly {MARKER}"}]}],
        "tools": [{"type": "namespace", "name": "mcp"}],
    }


def test_bridge_rejects_plain_text_without_envelope() -> None:
    parsed = bridge._interpret("plain answer", body())
    assert parsed.get("kind") == "protocol_error"


def test_bridge_accepts_complete_envelope() -> None:
    raw = f'{bridge.BRIDGE_START}\n{{"kind":"final","text":"{MARKER}"}}\n{bridge.BRIDGE_END}'
    parsed = bridge._interpret(raw, body())
    assert parsed.get("kind") == "final"
    assert parsed.get("text") == MARKER


def test_bridge_rejects_partial_exact_marker() -> None:
    raw = f'{bridge.BRIDGE_START}\n{{"kind":"final","text":"{MARKER[:-8]}"}}\n{bridge.BRIDGE_END}'
    parsed = bridge._interpret(raw, body())
    assert parsed.get("kind") == "protocol_error"


def test_bridge_prompt_places_exact_marker_at_integrity_boundary() -> None:
    app = SimpleNamespace(state=SimpleNamespace())
    prompt, _ = bridge._bridge_prompt(app, body())
    tail = prompt.rsplit("FINAL TRANSPORT INTEGRITY CHECK (v110):", 1)[1]
    assert "byte-for-byte" in tail
    assert "partial marker" in tail.lower()
    assert MARKER in tail
    assert bridge.BRIDGE_START in tail
    assert bridge.BRIDGE_END in tail


def test_worker_uses_request_v6_terminal_owner_without_v89_wrapper() -> None:
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    assert manifest["version"] == "0.8.40"
    assert "content_request_v6.js" in scripts
    assert "content_response_semantic_recovery_v51.js" in scripts
    assert "content_terminal_integrity_v89.js" not in scripts
    request = (ROOT / "chrome_extension" / "content_request_v6.js").read_text(encoding="utf-8")
    network = (ROOT / "chrome_extension" / "content_network_stream_recovery_v55.js").read_text(encoding="utf-8")
    assert 'type: "chat.completed"' in request
    assert 'type: "chat.completed"' not in network
    assert 'network_terminal_authority: "request-v6"' in network
