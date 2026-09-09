import json
from pathlib import Path

from app.responses_emulated_tools_v109_patch import (
    BRIDGE_END,
    BRIDGE_START,
    _catalog,
    _completed_response,
    _input_context,
    _interpret,
    needs_emulated_tools,
)


ROOT = Path(__file__).resolve().parents[1]


def envelope(value: dict) -> str:
    return BRIDGE_START + "\n" + json.dumps(value) + "\n" + BRIDGE_END


def test_function_tool_becomes_canonical_function_call() -> None:
    body = {
        "tools": [
            {
                "type": "function",
                "name": "exec_command",
                "description": "Run a command",
                "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]},
            }
        ]
    }
    catalog = _catalog(body)
    text, items = _interpret(
        envelope({"kind": "tool_calls", "calls": [{"name": "exec_command", "arguments": {"cmd": "printf ok"}}]}),
        catalog,
        body,
    )
    assert text == ""
    assert len(items) == 1
    item = items[0]
    assert item["type"] == "function_call"
    assert item["name"] == "exec_command"
    assert item["call_id"].startswith("call_")
    assert json.loads(item["arguments"]) == {"cmd": "printf ok"}


def test_namespace_mcp_tool_preserves_namespace_for_codex_dispatch() -> None:
    body = {
        "tools": [
            {
                "type": "namespace",
                "name": "mcp__fdex_smoke",
                "description": "FDEX smoke MCP",
                "tools": [
                    {
                        "type": "function",
                        "name": "fdex_smoke_echo",
                        "description": "Echo marker",
                        "parameters": {"type": "object", "properties": {"marker": {"type": "string"}}, "required": ["marker"]},
                    }
                ],
            }
        ]
    }
    catalog = _catalog(body)
    _, items = _interpret(
        envelope(
            {
                "kind": "tool_calls",
                "calls": [
                    {
                        "namespace": "mcp__fdex_smoke",
                        "name": "fdex_smoke_echo",
                        "arguments": {"marker": "SMOKE"},
                    }
                ],
            }
        ),
        catalog,
        body,
    )
    assert items[0]["type"] == "function_call"
    assert items[0]["namespace"] == "mcp__fdex_smoke"
    assert items[0]["name"] == "fdex_smoke_echo"


def test_custom_apply_patch_becomes_custom_tool_call() -> None:
    body = {
        "tools": [
            {
                "type": "custom",
                "name": "apply_patch",
                "description": "Apply a patch",
                "format": {"type": "grammar", "syntax": "lark", "definition": "start: /.+/s"},
            }
        ]
    }
    catalog = _catalog(body)
    _, items = _interpret(
        envelope({"kind": "tool_calls", "calls": [{"name": "apply_patch", "input": "*** Begin Patch\n*** End Patch"}]}),
        catalog,
        body,
    )
    assert items[0]["type"] == "custom_tool_call"
    assert items[0]["name"] == "apply_patch"
    assert items[0]["input"].startswith("*** Begin Patch")


def test_tool_search_output_loads_followup_namespace_tools() -> None:
    body = {
        "input": [
            {
                "type": "tool_search_output",
                "call_id": "call_search",
                "status": "completed",
                "execution": "client",
                "tools": [
                    {
                        "type": "namespace",
                        "name": "mcp__calendar",
                        "tools": [{"type": "function", "name": "list_events", "parameters": {"type": "object"}}],
                    }
                ],
            }
        ]
    }
    assert needs_emulated_tools(body) is True
    catalog = _catalog(body)
    assert catalog[0]["namespace"] == "mcp__calendar"
    assert catalog[0]["name"] == "list_events"
    assert "TOOL SEARCH RESULT (call_search)" in _input_context(body)


def test_function_call_output_continuation_is_visible_to_bridge() -> None:
    body = {
        "input": [
            {"type": "function_call", "call_id": "call_1", "name": "exec_command", "arguments": "{\"cmd\":\"cat marker\"}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "MARKER_OK"},
        ]
    }
    context = _input_context(body)
    assert "PRIOR TOOL CALL" in context
    assert "TOOL RESULT (call_1)" in context
    assert "MARKER_OK" in context


def test_emulated_response_bills_bridge_generation_not_empty_visible_text() -> None:
    body = {"model": "gpt-5.6-sol", "tools": [{"type": "function", "name": "exec_command", "parameters": {}}]}
    bridge_text = envelope({"kind": "tool_calls", "calls": [{"name": "exec_command", "arguments": {"cmd": "pwd"}}]})
    _, items = _interpret(bridge_text, _catalog(body), body)
    response = _completed_response("resp_1", body, "bridge prompt", bridge_text, "", items, 1)
    assert response["status"] == "completed"
    assert response["output_text"] == ""
    assert response["usage"]["output_tokens"] > 0
    assert response["metadata"]["chat2api_tool_bridge"] == "emulated-v109"


def test_emulated_middleware_stays_inside_model_and_billing_owner() -> None:
    source = (ROOT / "app" / "responses_model_routing_v108_patch.py").read_text(encoding="utf-8")
    emulated = (ROOT / "app" / "responses_emulated_tools_v109_patch.py").read_text(encoding="utf-8")
    assert source.index("app.add_middleware(ResponsesEmulatedToolsMiddleware") < source.index("app.add_middleware(_ResponsesModelContextMiddleware")
    assert "server_app.state.telemetry.upsert" in source
    assert '"function_call_output"' in emulated
    assert '"mcp_tool_call_output"' in emulated
    assert '"custom_tool_call_output"' in emulated
    assert '"tool_search_output"' in emulated
