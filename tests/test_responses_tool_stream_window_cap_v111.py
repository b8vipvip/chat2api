from pathlib import Path

from app.responses_tool_stream_v111_patch import _pending_item, _tool_argument_events


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


def test_model_routing_installs_v111_before_emulated_middleware() -> None:
    source = (ROOT / "app" / "responses_model_routing_v108_patch.py").read_text(encoding="utf-8")
    assert "install_responses_tool_stream_v111_patch(app)" in source
    assert source.index("install_responses_tool_stream_v111_patch(app)") < source.index(
        "app.add_middleware(ResponsesEmulatedToolsMiddleware"
    )
    assert "tools = _request_tools(payload)" in source
    assert '"responses_tool_stream_revision"' in source


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
