from __future__ import annotations

from pathlib import Path

from app.responses_console_v110_patch import DOC_MARKER, PATCH_REVISION, RESPONSES_DOC_HTML, USER_ASSET, _public_error


ROOT = Path(__file__).resolve().parents[1]


def test_responses_console_v110_documents_recommended_and_compatible_protocols() -> None:
    assert PATCH_REVISION == 110
    assert USER_ASSET == "/assets/chat2api-responses-console-v110.js"
    assert DOC_MARKER in RESPONSES_DOC_HTML
    assert "Responses API（推荐）" in RESPONSES_DOC_HTML
    assert "/v1/responses" in RESPONSES_DOC_HTML
    assert "/v1/chat/completions" in RESPONSES_DOC_HTML
    assert "web_search" in RESPONSES_DOC_HTML
    assert "function_call_output" in RESPONSES_DOC_HTML
    assert "previous_response_id" in RESPONSES_DOC_HTML
    assert "mcp_tool_call_output" in RESPONSES_DOC_HTML
    assert "工具由调用方执行" in RESPONSES_DOC_HTML


def test_user_console_overlay_adds_protocol_switch_and_responses_examples() -> None:
    js = (ROOT / "app" / "responses_console_v110.js").read_text(encoding="utf-8")
    assert 'id="playProtocol"' in js
    assert 'value="responses"' in js
    assert 'value="chat_completions"' in js
    assert 'id="playTool"' in js
    assert 'value="web_search"' in js
    assert 'fetch("/api/user/playground"' in js
    assert 'protocol: "responses"' in js
    assert "/v1/responses" in js
    assert "function_call_output" in js
    assert "Chat Completions（兼容）" in js


def test_server_patch_intercepts_only_explicit_responses_playground_requests() -> None:
    source = (ROOT / "app" / "responses_console_v110_patch.py").read_text(encoding="utf-8")
    assert 'path == "/api/user/playground"' in source
    assert 'str(parsed.get("protocol") or "") == "responses"' in source
    assert 'f"{base}/v1/responses"' in source
    assert 'payload["tools"] = [{"type": "web_search"}]' in source
    assert 'payload["tool_choice"] = "required"' in source
    assert "call_next(request)" in source


def test_entry_installs_responses_console_after_responses_api() -> None:
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from .responses_console_v110_patch import install_responses_console_v110_patch" in entry
    assert entry.index("install_responses_v108_patch(app)") < entry.index("install_responses_console_v110_patch(app)")
    assert entry.rstrip().endswith("install_request_history_v94_patch(app)")


def test_public_error_does_not_require_internal_runtime_details() -> None:
    assert _public_error({"error": {"message": "tool request rejected"}}, "fallback") == "tool request rejected"
    assert _public_error({"detail": "bad request"}, "fallback") == "bad request"
    assert _public_error({}, "fallback") == "fallback"
