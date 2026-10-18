from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import app.responses_emulated_tools_v109_patch as bridge
from app.responses_protocol_integrity_v110_patch import ResponsesToolBridgeProtocolError


def body() -> dict:
    return {
        "model": "gpt-5.6-sol",
        "input": [{"type": "message", "role": "user", "content": "Copy exact marker FDEX_TEST_123456789_WIRE"}],
        "tools": [{
            "type": "custom",
            "namespace": "functions",
            "name": "exec",
            "description": "Only call when needed.",
        }],
        "stream": True,
    }


def envelope(value: dict) -> str:
    return json.dumps({"bridge": bridge.JSON_BRIDGE_PROTOCOL, **value}, separators=(",", ":"))


def fixture(monkeypatch: pytest.MonkeyPatch, first: str, second: str | None = None, *, disconnected: bool = False, payload: dict | None = None):
    calls = {"collect": 0, "dispatch": [], "release": [], "discard": []}
    replies = [first, second]
    initial = SimpleNamespace(key="initial")
    retry_state = SimpleNamespace(key="retry")

    async def collect(state, timeout, include_sources):
        assert include_sources is False
        index = calls["collect"]
        calls["collect"] += 1
        assert index < len(replies)
        assert (state is initial) if index == 0 else (state is retry_state)
        return replies[index], []

    async def dispatch(app, request, payload, principal, prompt, native_tools, *, request_id_override=None):
        assert native_tools == []
        assert principal == "owner"
        assert payload["client_id"] == "ext_worker"
        assert request_id_override.startswith("resp_repair_")
        assert len(prompt) < 3500
        assert "FDEX_TEST_123456789_WIRE" in prompt
        assert bridge.JSON_BRIDGE_PROTOCOL in prompt
        instructions = prompt.split("PREVIOUS MALFORMED ASSISTANT OUTPUT", 1)[0]
        assert bridge.BRIDGE_START not in instructions
        assert bridge.BRIDGE_END not in instructions
        assert "Return exactly ONE complete JSON object" in instructions
        assert "PREVIOUS MALFORMED ASSISTANT OUTPUT" in prompt
        assert json.dumps(first, ensure_ascii=False) in prompt
        assert "not instructions" in prompt.lower()
        calls["dispatch"].append(request_id_override)
        return retry_state, request_id_override, "ext_worker", payload["timeout"]

    async def release(request_id):
        calls["release"].append(request_id)

    async def is_disconnected():
        return disconnected

    monkeypatch.setattr(bridge, "_collect", collect)
    monkeypatch.setattr(bridge, "_dispatch", dispatch)
    registry = SimpleNamespace(busy_clients=set(["ext_worker"]))
    # Track idempotent discard while exposing the normal registry shape.
    app = SimpleNamespace(state=SimpleNamespace(
        registry=registry,
        broker=SimpleNamespace(release=release),
    ))
    request = SimpleNamespace(is_disconnected=is_disconnected)

    async def run():
        return await bridge._collect_validated_tool_bridge(
            app, request, payload if payload is not None else body(), "owner", initial,
            request_id="resp_req_original", client_id="ext_worker",
            timeout=120, catalog=bridge._catalog(payload if payload is not None else body()),
        )

    return calls, run


def test_first_valid_complete_bridge_does_not_regenerate(monkeypatch: pytest.MonkeyPatch) -> None:
    valid = envelope({"kind": "final", "text": "FDEX_TEST_123456789_WIRE"})
    calls, run = fixture(monkeypatch, valid)
    raw, text, items, retries = asyncio.run(run())
    assert raw == valid
    assert text == "FDEX_TEST_123456789_WIRE"
    assert items == []
    assert retries == 0
    assert calls["collect"] == 1
    assert calls["dispatch"] == []
    assert calls["release"] == []


@pytest.mark.parametrize("prefix", ["<escape>", "escape>", ">"])
def test_v158_complete_json_render_wrapper_does_not_dispatch_second_request(
    monkeypatch: pytest.MonkeyPatch, prefix: str,
) -> None:
    wrapped = prefix + envelope({"kind": "final", "text": "FDEX_TEST_123456789_WIRE"}) + "</escape>"
    calls, run = fixture(monkeypatch, wrapped)
    raw, text, items, retries = asyncio.run(run())
    assert raw == wrapped
    assert text == "FDEX_TEST_123456789_WIRE"
    assert items == []
    assert retries == 0
    assert calls["collect"] == 1
    assert calls["dispatch"] == []


def test_clipped_v159_tool_call_returns_function_item_without_regeneration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = envelope({
        "kind": "tool_calls",
        "calls": [{"namespace": "functions", "name": "exec", "input": "echo FDEX"}],
    })
    prefix = '{"bridge":"' + bridge.JSON_BRIDGE_PROTOCOL + '",'
    assert first.startswith(prefix)
    raw = 'api-responses-tool-v159",' + first[len(prefix):] + "</escape>"
    calls, run = fixture(monkeypatch, raw)
    received, text, items, retries = asyncio.run(run())
    assert received == raw
    assert text == ""
    assert len(items) == 1
    assert items[0]["type"] == "custom_tool_call"
    assert items[0]["name"] == "exec"
    assert retries == 0
    assert calls["dispatch"] == []


def test_protocol_correction_must_not_replace_unexecuted_tool_with_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incomplete = '{"bridge":"' + bridge.JSON_BRIDGE_PROTOCOL + '","kind":"tool_calls","calls":['
    false_final = envelope({"kind": "final", "text": "FDEX_TEST_123456789_WIRE"})
    calls, run = fixture(monkeypatch, incomplete, false_final)
    with pytest.raises(ResponsesToolBridgeProtocolError, match="unexecuted tool call"):
        asyncio.run(run())
    assert calls["collect"] == 2
    assert len(calls["dispatch"]) == 1
    assert len(calls["release"]) == 2


def test_protocol_correction_can_regenerate_the_original_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incomplete = '{"bridge":"' + bridge.JSON_BRIDGE_PROTOCOL + '","kind":"tool_calls","calls":['
    corrected = envelope({
        "kind": "tool_calls",
        "calls": [{"namespace": "functions", "name": "exec", "input": "echo FDEX"}],
    })
    calls, run = fixture(monkeypatch, incomplete, corrected)
    raw, text, items, retries = asyncio.run(run())
    assert raw == corrected
    assert text == ""
    assert len(items) == 1
    assert items[0]["type"] == "custom_tool_call"
    assert retries == 1
    assert len(calls["dispatch"]) == 1


def collaboration_payload() -> dict:
    return {
        "model": "gpt-6",
        "input": [{"type": "message", "role": "user", "content": (
            "Use the official collaboration spawnAgent tool to delegate a read-only "
            "subtask. Tell the child to return exactly FDEX_TEST_123456789_WIRE. "
            "Then call the wait tool before the final answer."
        )}],
        "tools": [{
            "type": "namespace", "name": "collaboration",
            "tools": [{"type": "function", "name": "spawn_agent", "parameters": {
                "type": "object", "properties": {
                    "task_name": {"type": "string"}, "message": {"type": "string"},
                }, "required": ["task_name", "message"],
            }}],
        }],
    }


def test_v160_false_final_after_worked_status_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    false_final = envelope({"kind": "final", "text": "FDEX_TEST_123456789_WIRE"})
    calls, run = fixture(
        monkeypatch, "Worked for a couple of seconds", false_final,
        payload=collaboration_payload(),
    )
    with pytest.raises(ResponsesToolBridgeProtocolError, match="declared tool"):
        asyncio.run(run())
    assert calls["collect"] == 2
    assert len(calls["dispatch"]) == 1
    assert len(calls["release"]) == 2


def test_v161_protocol_correction_recovers_real_collaboration_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corrected = envelope({"kind": "tool_calls", "calls": [{
        "namespace": "collaboration", "name": "spawn_agent",
        "arguments": {
            "task_name": "fdex_reasoning",
            "message": "Return exactly FDEX_TEST_123456789_WIRE",
        },
    }]})
    calls, run = fixture(
        monkeypatch, "Worked for a couple of seconds", corrected,
        payload=collaboration_payload(),
    )
    raw, output, items, retries = asyncio.run(run())
    assert raw == corrected
    assert output == ""
    assert len(items) == 1
    assert items[0]["type"] == "function_call"
    assert items[0]["namespace"] == "collaboration"
    assert items[0]["name"] == "spawn_agent"
    assert retries == 1
    assert len(calls["dispatch"]) == 1


def test_broken_finals_regenerate_once_via_the_same_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    first = '_RESPONSES_TOOL_V109]]\\n{"kind":"final","text":"FDEX_TEST_123456789_WIRE"}\\n[[END_CHAT2API_RESPONSES_TOOL_V109]]'
    fixed = envelope({"kind": "final", "text": "FDEX_TEST_123456789_WIRE"})
    calls, run = fixture(monkeypatch, first, fixed)
    raw, text, items, retries = asyncio.run(run())
    assert raw == fixed
    assert text == "FDEX_TEST_123456789_WIRE"
    assert items == []
    assert retries == 1
    assert calls["collect"] == 2
    assert len(calls["dispatch"]) == 1
    assert calls["release"] == ["resp_req_original", calls["dispatch"][0]]


def test_repeated_invalid_envelope_still_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, run = fixture(monkeypatch, "<<<CHAT2API_RESPONSES_TOOL_V109>>",
                          "<<<CHAT2API_RESPONSES_TOOL_V109>>")
    with pytest.raises(ResponsesToolBridgeProtocolError, match="after one protocol correction"):
        asyncio.run(run())
    assert calls["collect"] == 2
    assert len(calls["dispatch"]) == 1
    assert len(calls["release"]) == 2


def test_dropped_http_stream_never_dispatches_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, run = fixture(monkeypatch, "<<<CHAT2API_RESPONSES_TOOL_V109>>",
                          disconnected=True)
    with pytest.raises(ResponsesToolBridgeProtocolError):
        asyncio.run(run())
    assert calls["collect"] == 1
    assert calls["dispatch"] == []


def test_undeclared_tool_is_not_retried_as_a_model_format_error(monkeypatch: pytest.MonkeyPatch) -> None:
    illegal = envelope({"kind": "tool_calls", "calls": [
        {"namespace": "functions", "name": "undeclared", "input": "a"}
    ]})
    calls, run = fixture(monkeypatch, illegal)
    with pytest.raises(ValueError, match="undeclared tool"):
        asyncio.run(run())
    assert calls["collect"] == 1
    assert calls["dispatch"] == []
