from __future__ import annotations

import asyncio
import pytest
from fastapi import FastAPI

from app.broker import RequestBroker
from app.response_semantic_guard_patch import install_response_semantic_guard_patch, is_internal_tool_handoff, sanitize_assistant_text


def test_assistant_role_shell_is_not_model_output() -> None:
    assert sanitize_assistant_text("ChatGPT said:") == ("", True)
    assert sanitize_assistant_text("ChatGPT said: 成功") == ("成功", True)
    assert sanitize_assistant_text("正常回答") == ("正常回答", False)


def test_broker_does_not_complete_on_role_shell_and_accepts_real_body() -> None:
    async def scenario() -> None:
        app = FastAPI()
        app.state.broker = RequestBroker()
        install_response_semantic_guard_patch(app)

        state = await app.state.broker.create("req_semantic12345678", "ext_test")
        assert await app.state.broker.publish(
            state.request_id,
            {"type": "chat.snapshot", "request_id": state.request_id, "text": "ChatGPT said:"},
        ) is True
        assert state.text == ""
        assert state.final_future is not None and not state.final_future.done()

        assert await app.state.broker.publish(
            state.request_id,
            {"type": "chat.completed", "request_id": state.request_id, "text": "ChatGPT said:"},
        ) is True
        assert state.final_future is not None and not state.final_future.done()
        assert state.diagnostics["assistant_ui_boilerplate_filtered"] is True

        assert await app.state.broker.publish(
            state.request_id,
            {"type": "chat.completed", "request_id": state.request_id, "text": "ChatGPT said: 成功"},
        ) is True
        assert state.final_future is not None and state.final_future.done()
        assert await state.final_future == "成功"
        assert state.text == "成功"

    asyncio.run(scenario())


def test_internal_tool_handoff_is_not_a_deliverable_assistant_response() -> None:
    assert is_internal_tool_handoff('{"skipped_mainline":true}')
    assert is_internal_tool_handoff(' { "skipped_mainline" : true } ')
    assert not is_internal_tool_handoff('{"skipped_mainline":false}')
    assert not is_internal_tool_handoff('{"skipped_mainline":true,"text":"hello"}')
    assert not is_internal_tool_handoff('普通文字 {"skipped_mainline":true}')
    assert not is_internal_tool_handoff('{"status":"completed"}')


def test_internal_tool_handoff_fails_closed_instead_of_success() -> None:
    async def scenario() -> None:
        app = FastAPI()
        app.state.broker = RequestBroker()
        install_response_semantic_guard_patch(app)
        state = await app.state.broker.create("req_handoff12345678", "ext_test")
        assert await app.state.broker.publish(
            state.request_id,
            {"type": "chat.snapshot", "request_id": state.request_id, "text": '{"skipped_mainline":true}'},
        )
        assert state.text == ""
        assert state.final_future is not None and not state.final_future.done()

        assert await app.state.broker.publish(
            state.request_id,
            {"type": "chat.completed", "request_id": state.request_id, "text": '{"skipped_mainline":true}'},
        )
        assert state.terminal_event_type == "chat.error"
        assert state.diagnostics["non_deliverable_tool_handoff"] is True
        with pytest.raises(RuntimeError, match="no transferable result"):
            await state.final_future

    asyncio.run(scenario())
