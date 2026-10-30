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


@pytest.mark.parametrize("render_prefix", [
    "<escape>", "escape>", "scape>", "cape>", "ape>", "pe>", "e>", ">", "",
])
def test_network_completed_json_with_clipped_render_escape_tag_is_validated(render_prefix: str) -> None:
    payload = json_bridge({"kind": "final", "text": MARKER})
    raw = render_prefix + payload + "</escape>"
    text, calls = bridge._interpret(raw, bridge._catalog(body()), body())
    assert text == MARKER
    assert calls == []


@pytest.mark.parametrize("clipped_prefix", [
    'api-responses-tool-v159",',
    '-tool-v159",',
    '159",',
])
def test_v159_real_sse_clipped_fixed_prefix_preserves_tool_calls(clipped_prefix: str) -> None:
    payload = json_bridge({
        "kind": "tool_calls",
        "calls": [{"namespace": None, "name": "noop", "arguments": {"cmd": "printf FDEX_TEST"}}],
    })
    fixed = '{"bridge":"' + bridge.JSON_BRIDGE_PROTOCOL + '",'
    assert payload.startswith(fixed)
    raw = clipped_prefix + payload[len(fixed):] + "</escape>"
    output, calls = bridge._interpret(raw, bridge._catalog(body()), body())
    assert output == ""
    assert len(calls) == 1
    assert calls[0]["type"] == "function_call"
    assert calls[0]["name"] == "noop"
    assert json.loads(calls[0]["arguments"]) == {"cmd": "printf FDEX_TEST"}


def test_clipped_fixed_prefix_still_requires_exact_final_literal() -> None:
    payload = json_bridge({"kind": "final", "text": MARKER[:-5]})
    fixed = '{"bridge":"' + bridge.JSON_BRIDGE_PROTOCOL + '",'
    with pytest.raises(integrity.ResponsesToolBridgeProtocolError, match="exact literal"):
        bridge._interpret(
            '159",' + payload[len(fixed):] + "</escape>",
            bridge._catalog(body()), body(),
        )


def test_wrapped_tool_calls_still_require_declared_tool_and_preserve_arguments() -> None:
    valid = json_bridge({
        "kind": "tool_calls",
        "calls": [{"name": "noop", "arguments": {"key": "literal </escape> text"}}],
    })
    _, calls = bridge._interpret(">" + valid + "</escape>", bridge._catalog(body()), body())
    assert len(calls) == 1
    assert calls[0]["name"] == "noop"
    assert json.loads(calls[0]["arguments"]) == {"key": "literal </escape> text"}
    invalid = json_bridge({
        "kind": "tool_calls",
        "calls": [{"name": "not_declared", "arguments": {}}],
    })
    with pytest.raises(ValueError, match="undeclared tool"):
        bridge._interpret("escape>" + invalid + "</escape>", bridge._catalog(body()), body())


@pytest.mark.parametrize("malformed", [
    "prefix >" + json_bridge({"kind": "final", "text": MARKER}) + "</escape>",
    "escape>" + json_bridge({"kind": "final", "text": MARKER}) + "</escape> suffix",
    "escape>" + json_bridge({"kind": "final", "text": MARKER}) + " extra</escape>",
    "x2api-responses-tool-v159\",\"kind\":\"final\",\"text\":\"" + MARKER + "\"}</escape>",
    "escape>" + json_bridge({"kind": "final", "text": MARKER}).replace(MARKER, MARKER[:-4]) + "</escape>",
    "escape>" + json_bridge({"kind": "final", "text": MARKER}).replace(
        bridge.JSON_BRIDGE_PROTOCOL, "not-the-bridge"
    ) + "</escape>",
    "escape>" + json_bridge({"kind": "tool_calls", "calls": ["not-a-call"]}) + "</escape>",
    "escape>" + '{"bridge":"' + bridge.JSON_BRIDGE_PROTOCOL
        + '","kind":"final","kind":"tool_calls","text":"' + MARKER + '"}' + "</escape>",
])
def test_render_escape_unwrap_never_guesses_missing_json_or_accepts_unknown_text(malformed: str) -> None:
    with pytest.raises(integrity.ResponsesToolBridgeProtocolError):
        bridge._interpret(malformed, bridge._catalog(body()), body())


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


def collaboration_body() -> dict:
    return {
        "model": "gpt-6",
        "tools": [{
            "type": "namespace", "name": "collaboration",
            "tools": [
                {"type": "function", "name": "spawn_agent",
                 "parameters": {"type": "object", "properties": {
                     "task_name": {"type": "string"}, "message": {"type": "string"},
                 }, "required": ["task_name", "message"]}},
                {"type": "function", "name": "wait_agent",
                 "parameters": {"type": "object", "properties": {
                     "timeout_ms": {"type": "number"},
                 }}},
            ],
        }],
        "input": [{"type": "message", "role": "user", "content": (
            "DEVELOPER MESSAGE:\nOlder unrelated instructions.\n"
            "USER MESSAGE:\nUse the official collaboration spawnAgent tool "
            "to delegate a read-only reasoning subtask. Tell the child to return "
            "exactly FDEX_TEST_123456789_SUBAGENT. Then call the official wait tool "
            "before finalizing. Do not use shell or MCP."
        )}],
    }


def test_explicit_collaboration_tool_requires_real_caller_owned_call() -> None:
    payload = collaboration_body()
    catalog = bridge._catalog(payload)
    required = integrity._explicit_declared_tool_requirement(payload, catalog)
    assert required is not None
    assert (required["namespace"], required["name"]) == ("collaboration", "spawn_agent")
    prompt, _ = bridge._bridge_prompt(SimpleNamespace(state=SimpleNamespace()), payload)
    assert "CURRENT TURN DECLARED TOOL REQUIREMENT" in prompt
    assert "collaboration.spawn_agent" in prompt
    with pytest.raises(integrity.ResponsesToolBridgeProtocolError, match="has not been called"):
        bridge._interpret(json_bridge({"kind": "final", "text": "FDEX_TEST_123456789_SUBAGENT"}), catalog, payload)
    with pytest.raises(integrity.ResponsesToolBridgeProtocolError, match="omitted"):
        bridge._interpret(json_bridge({"kind": "tool_calls", "calls": [
            {"namespace": "collaboration", "name": "wait_agent", "arguments": {}},
        ]}), catalog, payload)
    text, calls = bridge._interpret(json_bridge({"kind": "tool_calls", "calls": [
        {"namespace": "collaboration", "name": "spawn_agent", "arguments": {
            "task_name": "fdex_reasoning", "message": "Return FDEX_TEST_123456789_SUBAGENT",
        }},
    ]}), catalog, payload)
    assert text == ""
    assert calls[0]["type"] == "function_call"
    assert calls[0]["namespace"] == "collaboration"
    assert calls[0]["name"] == "spawn_agent"
    assert json.loads(calls[0]["arguments"])["task_name"] == "fdex_reasoning"


def test_collaboration_call_result_allows_normal_followup() -> None:
    payload = collaboration_body()
    payload["input"].extend([
        {"type": "function_call", "namespace": "collaboration",
         "name": "spawn_agent", "call_id": "call_spawn", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_spawn", "output": "child started"},
    ])
    catalog = bridge._catalog(payload)
    next_tool = integrity._explicit_declared_tool_requirement(payload, catalog)
    assert next_tool is not None and next_tool["name"] == "wait_agent"
    with pytest.raises(integrity.ResponsesToolBridgeProtocolError, match="wait_agent"):
        bridge._interpret(
            json_bridge({"kind": "final", "text": "FDEX_TEST_123456789_SUBAGENT"}),
            catalog, payload,
        )
    payload["input"].extend([
        {"type": "function_call", "namespace": "collaboration",
         "name": "wait_agent", "call_id": "call_wait", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_wait", "output": "child completed"},
    ])
    assert integrity._explicit_declared_tool_requirement(payload, catalog) is None
    result, calls = bridge._interpret(
        json_bridge({"kind": "final", "text": "FDEX_TEST_123456789_SUBAGENT"}),
        catalog, payload,
    )
    assert result == "FDEX_TEST_123456789_SUBAGENT"
    assert calls == []


def test_serialized_collaboration_tool_result_is_not_reexecuted() -> None:
    payload = collaboration_body()
    original = payload["input"][0]["content"]
    payload["input"][0]["content"] = (
        original + "\nPRIOR TOOL CALL:\n"
        + json.dumps({"type": "function_call", "namespace": "collaboration",
                      "name": "spawn_agent", "call_id": "call_spawn"})
        + "\nTOOL RESULT (call_spawn):\nChild started"
    )
    next_tool = integrity._explicit_declared_tool_requirement(payload, bridge._catalog(payload))
    assert next_tool is not None and next_tool["name"] == "wait_agent"
    payload["input"][0]["content"] += (
        "\nPRIOR TOOL CALL:\n"
        + json.dumps({"type": "function_call", "namespace": "collaboration",
                      "name": "wait_agent", "call_id": "call_wait"})
        + "\nTOOL RESULT (call_wait):\nChild completed"
    )
    assert integrity._explicit_declared_tool_requirement(payload, bridge._catalog(payload)) is None


@pytest.mark.parametrize("instruction", [
    "What is the collaboration spawnAgent tool?",
    "Explain how to use spawnAgent safely.",
    "Do not use spawnAgent for this task.",
])
def test_tool_explanations_and_negations_do_not_require_execution(instruction: str) -> None:
    payload = collaboration_body()
    payload["input"] = [{"role": "user", "content": instruction}]
    assert integrity._explicit_declared_tool_requirement(payload, bridge._catalog(payload)) is None


def test_worker_uses_request_v6_terminal_owner_without_v89_wrapper() -> None:
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    assert manifest["version"] == "0.22.258"
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
