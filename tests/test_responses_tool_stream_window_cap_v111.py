from pathlib import Path

from app.responses_tool_stream_v111_patch import (
    PATCH_REVISION,
    _canonical_done_item,
    _mark_tool_follow_up,
    _pending_item,
    _tool_argument_events,
)
from app.responses_tool_stream_v113_patch import (
    _canonical_response_tool_items,
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
    assert PATCH_REVISION == 113


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


def test_v112_compatibility_module_delegates_to_v113() -> None:
    source = (ROOT / "app" / "responses_tool_stream_v112_patch.py").read_text(encoding="utf-8")
    assert "install_responses_tool_stream_v113_patch(app)" in source
    assert "responses_tool_stream_revision = PATCH_REVISION" in source


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
