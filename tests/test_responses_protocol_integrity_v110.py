from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.responses_emulated_tools_v109_patch as bridge
import app.responses_protocol_integrity_v110_patch as integrity

ROOT = Path(__file__).resolve().parents[1]
MARKER = "FDEX_CODEX_SMOKE_cdac2df519f84e20_WIRE"


def envelope(value: dict) -> str:
    return bridge.BRIDGE_START + "\n" + json.dumps(value) + "\n" + bridge.BRIDGE_END


def body() -> dict:
    return {
        "model": "gpt-5.6-sol",
        "tools": [
            {
                "type": "function",
                "name": "noop",
                "description": "No-op",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
        "input": [
            {
                "role": "user",
                "content": f"Reply with exact marker {MARKER}",
            }
        ],
    }


def test_plain_or_truncated_final_is_protocol_failure_not_completed_text() -> None:
    catalog = bridge._catalog(body())
    with pytest.raises(integrity.ResponsesToolBridgeProtocolError):
        bridge._interpret("FDEX_CODEX_SMOKE_cdac2df519f84e20_", catalog, body())


def test_truncated_exact_literal_inside_valid_envelope_is_still_protocol_failure() -> None:
    catalog = bridge._catalog(body())
    with pytest.raises(integrity.ResponsesToolBridgeProtocolError, match="exact literal"):
        bridge._interpret(
            envelope({"kind": "final", "text": "FDEX_CODEX_SMOKE_cdac2df519f84e20_"}),
            catalog,
            body(),
        )


def test_exact_final_inside_complete_envelope_is_preserved_byte_for_byte() -> None:
    catalog = bridge._catalog(body())
    text, items = bridge._interpret(envelope({"kind": "final", "text": MARKER}), catalog, body())
    assert text == MARKER
    assert items == []


def test_bridge_rejects_text_outside_sentinel() -> None:
    catalog = bridge._catalog(body())
    wrapped = "prefix\n" + envelope({"kind": "final", "text": "ok"})
    with pytest.raises(integrity.ResponsesToolBridgeProtocolError):
        bridge._interpret(wrapped, catalog, body())


def test_bridge_prompt_repeats_literal_integrity_contract_at_generation_boundary() -> None:
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
    assert manifest["version"] == "0.22.95"
    assert "content_request_v6.js" in scripts
    assert "content_response_semantic_recovery_v51.js" in scripts
    assert "content_terminal_integrity_v89.js" not in scripts
    request = (ROOT / "chrome_extension" / "content_request_v6.js").read_text(encoding="utf-8")
    network = (ROOT / "chrome_extension" / "content_network_stream_recovery_v55.js").read_text(encoding="utf-8")
    assert 'type: "chat.completed"' in request
    assert 'type: "chat.completed"' not in network
    assert 'network_terminal_authority: "request-v6"' in network
