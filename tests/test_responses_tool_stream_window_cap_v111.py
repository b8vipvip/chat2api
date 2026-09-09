import json
from pathlib import Path

import pytest

from app.responses_tool_stream_v111_patch import (
    PATCH_REVISION,
    _canonical_done_item,
    _mark_tool_follow_up,
    _pending_item,
    _tool_argument_events,
)
from app.responses_tool_stream_v114_patch import (
    _call_item_with_nested_custom_recovery,
    _canonical_response_tool_items,
    _normalize_nested_custom_call,
    _recover_single_custom_tool_envelope,
    _restore_tool_namespaces,
)


ROOT = Path(__file__).resolve().parents[1]


def test_custom_tool_stream_emits_input_delta_and_done() -> None:
    item = {
        "id": "ctc_1",
        "type": "custom_tool_call",
        "status": "completed",
        "call_id": "call_1",
        "namespace": "functions",
        "name": "exec",
        "input": "text('ok');",
    }
    pending = _pending_item(item)
    assert pending["status"] == "in_progress"
    assert pending["input"] == ""
    events = _tool_argument_events("resp_1", 0, item)
    assert [name for name, _ in events] == [
        "response.custom_tool_call_input.delta",
        "response.custom_tool_call_input.done",
    ]
    assert events[0][1]["delta"] == "text('ok');"
    assert events[1][1]["input"] == "text('ok');"
    assert events[1][1]["call_id"] == "call_1"


def test_function_tool_stream_emits_argument_delta_and_done() -> None:
    item = {
        "id": "fc_1",
        "type": "function_call",
        "status": "completed",
        "call_id": "call_2",
        "name": "exec_command",
        "arguments": '{"cmd":"pwd"}',
    }
    pending = _pending_item(item)
    assert pending["arguments"] == ""
    events = _tool_argument_events("resp_2", 1, item)
    assert [name for name, _ in events] == [
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
    ]
    assert events[0][1]["delta"] == '{"cmd":"pwd"}'
    assert events[1][1]["arguments"] == '{"cmd":"pwd"}'


def test_codex_0149_done_item_is_minimal_and_keeps_namespace() -> None:
    item = {
        "id": "ctc_1",
        "type": "custom_tool_call",
        "status": "completed",
        "call_id": "call_1",
        "namespace": "functions",
        "name": "exec",
        "input": "text('ok');",
    }
    done = _canonical_done_item(item)
    assert done == {
        "type": "custom_tool_call",
        "call_id": "call_1",
        "namespace": "functions",
        "name": "exec",
        "input": "text('ok');",
    }


def test_actual_fdex_style_unescaped_custom_input_is_recovered() -> None:
    malformed = r'''<<<CHAT2API_RESPONSES_TOOL_V109>>>
{"kind":"tool_calls","calls":[{"name":"exec","input":"const w = await tools.exec_command({cmd:"printf '%s\\n' 'MARKER' > fdex_codex_provider_smoke.txt",workdir:"/tmp/workspace"});\ntext(w.output);"}]}
<<<END_CHAT2API_RESPONSES_TOOL_V109>>>'''
    parsed = _recover_single_custom_tool_envelope(malformed)
    assert parsed is not None
    assert parsed["kind"] == "tool_calls"
    assert parsed["calls"][0]["name"] == "exec"
    assert parsed["calls"][0]["input"] == (
        'const w = await tools.exec_command({cmd:"printf \'%s\\n\' \'MARKER\' > '
        'fdex_codex_provider_smoke.txt",workdir:"/tmp/workspace"});\ntext(w.output);'
    )


def test_actual_fdex_mcp_nested_custom_wrapper_is_promoted_to_raw_javascript() -> None:
    # This is the structural shape captured from the 2026-09-09 FDEX MCP smoke:
    # ChatGPT wrapped custom-tool raw input inside arguments:{name,input}.
    raw = r'''{"kind":"tool_calls","calls":[{"namespace":"functions","name":"exec","arguments":{"name":"exec","input":"const hit = ALL_TOOLS.find(x => x.name === 'mcp__fdex_smoke__fdex_smoke_echo');\nconst fn = tools[hit.name];\nconst r = await fn({marker:'FDEX_CODEX_SMOKE_3304ce6f5f0f476e_MCP'});\ntext(r);"}}]}'''
    envelope = json.loads(raw)
    catalog = [{"type": "custom", "namespace": "functions", "name": "exec"}]
    call = envelope["calls"][0]

    normalized = _normalize_nested_custom_call(call, catalog)
    assert "arguments" not in normalized
    assert normalized["namespace"] == "functions"
    assert normalized["name"] == "exec"
    assert normalized["input"].startswith("const hit = ALL_TOOLS.find")
    assert "FDEX_CODEX_SMOKE_3304ce6f5f0f476e_MCP" in normalized["input"]

    item = _call_item_with_nested_custom_recovery(call, catalog)
    assert item["type"] == "custom_tool_call"
    assert item["namespace"] == "functions"
    assert item["name"] == "exec"
    assert item["input"] == normalized["input"]
    assert not item["input"].lstrip().startswith("{")


def test_nested_custom_wrapper_identity_mismatch_fails_closed() -> None:
    catalog = [{"type": "custom", "namespace": "functions", "name": "exec"}]
    call = {
        "namespace": "functions",
        "name": "exec",
        "arguments": {"name": "other_tool", "input": "text('bad');"},
    }
    with pytest.raises(ValueError, match="does not match outer tool"):
        _normalize_nested_custom_call(call, catalog)


def test_missing_namespace_is_restored_from_unique_catalog_match() -> None:
    items = [
        {
            "id": "ctc_1",
            "type": "custom_tool_call",
            "status": "completed",
            "call_id": "call_1",
            "name": "exec",
            "input": "text('ok');",
        }
    ]
    catalog = [{"type": "custom", "namespace": "functions", "name": "exec"}]
    restored = _restore_tool_namespaces(items, catalog)
    assert restored[0]["namespace"] == "functions"


def test_response_completed_uses_same_canonical_tool_shape_as_done_event() -> None:
    items = [
        {
            "id": "ctc_1",
            "type": "custom_tool_call",
            "status": "completed",
            "call_id": "call_1",
            "namespace": "functions",
            "name": "exec",
            "input": "text('ok');",
        }
    ]
    assert _canonical_response_tool_items(items) == [
        {
            "type": "custom_tool_call",
            "call_id": "call_1",
            "namespace": "functions",
            "name": "exec",
            "input": "text('ok');",
        }
    ]


def test_tool_response_explicitly_requires_follow_up() -> None:
    response = {"id": "resp_1", "metadata": {}}
    _mark_tool_follow_up(response, [{"type": "custom_tool_call"}])
    assert response["end_turn"] is False
    assert response["metadata"]["chat2api_tool_stream"] == "responses-v113-codex-0149"
    assert PATCH_REVISION == 114


def test_model_routing_installs_v111_compatibility_entry_before_emulated_middleware() -> None:
    source = (ROOT / "app" / "responses_model_routing_v108_patch.py").read_text(encoding="utf-8")
    assert "install_responses_tool_stream_v111_patch(app)" in source
    assert source.index("install_responses_tool_stream_v111_patch(app)") < source.index(
        "app.add_middleware(ResponsesEmulatedToolsMiddleware"
    )
    assert "tools = _request_tools(payload)" in source
    assert '"responses_tool_stream_revision"' in source


def test_v111_compatibility_module_delegates_to_v112() -> None:
    source = (ROOT / "app" / "responses_tool_stream_v111_patch.py").read_text(encoding="utf-8")
    assert "install_responses_tool_stream_v112_patch(app)" in source
    assert "responses_tool_stream_revision = PATCH_REVISION" in source


def test_v112_compatibility_module_delegates_to_v114() -> None:
    source = (ROOT / "app" / "responses_tool_stream_v112_patch.py").read_text(encoding="utf-8")
    assert "install_responses_tool_stream_v114_patch(app)" in source
    assert "responses_tool_stream_revision = PATCH_REVISION" in source


def test_terminal_event_can_reuse_same_worker_route_before_async_route_cleanup() -> None:
    source = (ROOT / "chrome_extension" / "conversation_workers_v25.js").read_text(encoding="utf-8")
    assert "terminalRequests: new Map()" in source
    assert "state.terminalRequests.has(inflight)" in source
    assert "route.inflight_request_id = null" in source
    assert "markTerminal(requestId);" in source
    assert source.index("markTerminal(requestId);") < source.index("state.releaseRequest(requestId);")
    assert "per-api-key-v26-terminal-reuse" in source


def test_background_entry_loads_routed_window_cap_after_manager() -> None:
    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    cap = (ROOT / "chrome_extension" / "background_routed_window_cap_v111.js").read_text(encoding="utf-8")
    assert entry.index('"background_window_manager_v88.js"') < entry.index(
        '"background_routed_window_cap_v111.js"'
    )
    assert "routedCount - cap" in cap
    assert "!active.has(windowId)" in cap
    assert "manager.protectedUntil.delete(windowId)" in cap
    assert "reserve.reconcile()" in cap
    assert "workers.resize" in cap
