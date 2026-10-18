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


def fixture(monkeypatch: pytest.MonkeyPatch, first: str, second: str | None = None, *, disconnected: bool = False):
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
        assert len(prompt) < 2000
        assert "FDEX_TEST_123456789_WIRE" in prompt
        assert bridge.JSON_BRIDGE_PROTOCOL in prompt
        assert bridge.BRIDGE_START not in prompt
        assert bridge.BRIDGE_END not in prompt
        assert "Return exactly ONE complete JSON object" in prompt
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
            app, request, body(), "owner", initial,
            request_id="resp_req_original", client_id="ext_worker",
            timeout=120, catalog=bridge._catalog(body()),
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
