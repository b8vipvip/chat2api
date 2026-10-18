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


def json_bridge(value: dict) -> str:
    return json.dumps(
        {"bridge": bridge.JSON_BRIDGE_PROTOCOL, **value},
        separators=(",", ":"),
    )


def test_json_only_final_preserves_exact_marker() -> None:
    catalog = bridge._catalog(body())
    result, items = bridge._interpret(
        json_bridge({"kind": "final", "text": MARKER}), catalog, body(),
    )
    assert result == MARKER
    assert items == []


def test_json_only_tool_call_uses_same_allowlist() -> None:
    catalog = bridge._catalog(body())
    text, items = bridge._interpret(
        json_bridge({"kind": "tool_calls", "calls": [{"name": "noop", "arguments": {}}]}),
        catalog, body(),
    )
    assert text == ""
    assert len(items) == 1
    assert items[0]["name"] == "noop"
    with pytest.raises(ValueError, match="undeclared tool"):
        bridge._interpret(
            json_bridge({"kind": "tool_calls", "calls": [{"name": "unknown", "arguments": {}}]}),
            catalog, body(),
        )


@pytest.mark.parametrize("invalid", [
    '{"kind":"final","text":"' + MARKER + '"}',
    '{"bridge":"wrong","kind":"final","text":"' + MARKER + '"}',
    '{"bridge":"' + bridge.JSON_BRIDGE_PROTOCOL + '","kind":"final","text":"' + MARKER + '"',
    '{"bridge":"' + bridge.JSON_BRIDGE_PROTOCOL + '","kind":"final","text":"' + MARKER + '"} trailing',
    '{"bridge":"' + bridge.JSON_BRIDGE_PROTOCOL + '","kind":"final","text":"' + MARKER + '","extra":1}',
    '{"bridge":"' + bridge.JSON_BRIDGE_PROTOCOL + '","kind":"tool_calls","calls":[]}',
    '[[CHAT2API_RESPONSES_TOOL_V109]]\n{"kind":"final","text":"' + MARKER + '"}',
    '_RESPONSES_TOOL_V109]]\n{"kind":"final","text":"' + MARKER + '"}\n[[END_CHAT2API_RESPONSES_TOOL_V109]]',
    '>{"kind":"final","text":"' + MARKER + '"}</escape>\n[[END_CHAT2API_RESPONSES_TOOL_V109]]',
])
def test_json_only_bridge_rejects_incomplete_or_unverified_payloads(invalid: str) -> None:
    with pytest.raises(integrity.ResponsesToolBridgeProtocolError):
        bridge._interpret(invalid, bridge._catalog(body()), body())


def test_bridge_prompt_requires_json_only_at_both_boundaries() -> None:
    app = SimpleNamespace(state=SimpleNamespace())
    prompt, _ = bridge._bridge_prompt(app, body())
    tail = prompt.rsplit("FINAL TRANSPORT INTEGRITY CHECK (v159):", 1)[1]
    assert "byte-for-byte" in tail
    assert "partial JSON object" in tail
    assert MARKER in tail
    assert bridge.JSON_BRIDGE_PROTOCOL in prompt
    assert bridge.JSON_BRIDGE_PROTOCOL in tail
    assert bridge.BRIDGE_START not in prompt
    assert bridge.BRIDGE_END not in prompt
    assert bridge.LEGACY_BRIDGE_START not in prompt
    repair = bridge._bridge_correction_prompt(body())
    assert bridge.JSON_BRIDGE_PROTOCOL in repair
    assert bridge.BRIDGE_START not in repair
    assert bridge.BRIDGE_END not in repair
    assert "FDEX_CODEX_SMOKE_" in repair



def test_old_complete_envelope_is_accepted_during_rolling_upgrade() -> None:
    catalog = bridge._catalog(body())
    old = (
        bridge.LEGACY_BRIDGE_START
        + "\n" + json.dumps({"kind": "final", "text": MARKER})
        + "\n" + bridge.LEGACY_BRIDGE_END
    )
    final, items = bridge._interpret(old, catalog, body())
    assert final == MARKER
    assert items == []


def test_escaped_missing_legacy_start_is_rejected_without_tool_execution() -> None:
    catalog = bridge._catalog(body())
    malformed = (
        '<escape>{"kind":"final","text":"' + MARKER + '"}</escape>'
        + "\n" + bridge.LEGACY_BRIDGE_END
    )
    with pytest.raises(integrity.ResponsesToolBridgeProtocolError, match="required Responses tool-bridge envelope"):
        bridge._interpret(malformed, catalog, body())


def test_mixed_legacy_and_html_safe_delimiters_fail_closed() -> None:
    catalog = bridge._catalog(body())
    mixed = (
        bridge.LEGACY_BRIDGE_START + "\n"
        + json.dumps({"kind": "final", "text": MARKER})
        + "\n" + bridge.BRIDGE_END
    )
    with pytest.raises(integrity.ResponsesToolBridgeProtocolError, match="required Responses tool-bridge envelope"):
        bridge._interpret(mixed, catalog, body())


def test_html_safe_tool_call_requires_declared_catalog_tool() -> None:
    catalog = bridge._catalog(body())
    valid = envelope({"kind": "tool_calls", "calls": [
        {"name": "noop", "arguments": {}},
    ]})
    text, calls = bridge._interpret(valid, catalog, body())
    assert text == ""
    assert len(calls) == 1
    assert calls[0]["name"] == "noop"
    with pytest.raises(ValueError, match="undeclared tool"):
        bridge._interpret(
            envelope({"kind": "tool_calls", "calls": [
                {"name": "undeclared", "arguments": {}},
            ]}),
            catalog, body(),
        )


def test_worker_uses_request_v6_terminal_owner_without_v89_wrapper() -> None:
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    assert manifest["version"] == "0.22.159"
    assert "content_request_v6.js" in scripts
    assert "content_response_semantic_recovery_v51.js" in scripts
    assert "content_terminal_integrity_v89.js" not in scripts
    request = (ROOT / "chrome_extension" / "content_request_v6.js").read_text(encoding="utf-8")
    network = (ROOT / "chrome_extension" / "content_network_stream_recovery_v55.js").read_text(encoding="utf-8")
    assert 'type: "chat.completed"' in request
    assert 'type: "chat.completed"' not in network
    assert 'network_terminal_authority: "request-v6"' in network



def test_shared_recovery_delimiter_extractor_supports_both_complete_formats() -> None:
    inner = '{"kind":"final","text":"safe"}'
    for start, end in (
        (bridge.BRIDGE_START, bridge.BRIDGE_END),
        (bridge.LEGACY_BRIDGE_START, bridge.LEGACY_BRIDGE_END),
    ):
        assert bridge._strip_complete_bridge_envelope(
            f"{start}\n{inner}\n{end}"
        ) == inner
    mixed = f"{bridge.LEGACY_BRIDGE_START}\n{inner}\n{bridge.BRIDGE_END}"
    assert bridge._strip_complete_bridge_envelope(mixed) == mixed
