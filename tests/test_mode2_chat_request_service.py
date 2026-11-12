"""Mode 2 shared application boundary: real broker, fake transport.

These tests do not simulate or replace request-v6 finality. The only final
event in the positive case is deliberately published by the existing Broker.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.broker import RequestBroker
from app.chat_request_service import ChatDispatch, ChatRequestService


class FakeRegistry:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, object]]] = []
        self.fail_send = False

    async def send(self, client_id: str, event: dict[str, object]) -> None:
        if self.fail_send:
            raise ConnectionError("worker-disconnected")
        self.sent.append((client_id, event))


@pytest.mark.asyncio
async def test_chat_and_responses_share_exactly_one_worker_command_shape() -> None:
    registry = FakeRegistry()
    broker = RequestBroker()
    service = ChatRequestService(registry, broker)
    state = await service.reserve("req_mode2_sdk", "ext_test")
    attachments = [{"file_id": "file_test", "purpose": "vision"}]
    options = {"model": "gpt-5.6", "auto_switch_text": True}

    await service.dispatch(
        "ext_test", request_id=state.request_id, prompt="[User]\nhello",
        attachments=attachments, options=options,
    )
    assert registry.sent == [("ext_test", {
        "type": "chat.request",
        "request_id": "req_mode2_sdk",
        "prompt": "[User]\nhello",
        "attachments": [{"file_id": "file_test", "purpose": "vision"}],
        "options": {"model": "gpt-5.6", "auto_switch_text": True},
    })]
    assert not state.final_future.done()
    assert state.terminal_event_type is None

    await broker.publish("req_mode2_sdk", {"type": "chat.completed", "text": "hello from worker"})
    assert await state.final_future == "hello from worker"
    assert state.terminal_event_type == "chat.completed"
    await broker.release(state.request_id)


@pytest.mark.asyncio
async def test_command_is_not_affected_by_adapter_mutation() -> None:
    attachments = [{"file_id": "file_one"}]
    options = {"model": "gpt-5.6"}
    command = ChatDispatch.build("req_no_aliasing", "hi", attachments, options)
    attachments[0]["file_id"] = "file_two"
    options["model"] = "unexpected"
    wire = command.worker_message()
    assert wire["attachments"] == [{"file_id": "file_one"}]
    assert wire["options"] == {"model": "gpt-5.6"}
    wire["attachments"][0]["file_id"] = "file_modified"
    assert command.worker_message()["attachments"] == [{"file_id": "file_one"}]


@pytest.mark.asyncio
async def test_broker_busy_conflict_is_not_bypassed_by_second_api_adapter() -> None:
    broker = RequestBroker()
    service = ChatRequestService(FakeRegistry(), broker)
    state = await service.reserve("req_chat", "ext_busy")
    with pytest.raises(RuntimeError, match="busy"):
        await service.reserve("resp_req_again", "ext_busy")
    assert broker.client_requests["ext_busy"] == "req_chat"
    assert broker.requests["req_chat"] is state
    assert "resp_req_again" not in broker.requests
    await broker.release("req_chat")


@pytest.mark.asyncio
async def test_failed_dispatch_does_not_forge_or_release_broker_terminal() -> None:
    registry = FakeRegistry()
    registry.fail_send = True
    broker = RequestBroker()
    service = ChatRequestService(registry, broker)
    state = await service.reserve("req_send_failure", "ext_dead")
    with pytest.raises(ConnectionError, match="worker-disconnected"):
        await service.dispatch(
            "ext_dead", request_id=state.request_id, prompt="hi",
            attachments=[], options={"timeout_seconds": 20},
        )
    assert broker.requests[state.request_id] is state
    assert state.terminal_event_type is None
    assert not state.final_future.done()
    # Adapter's existing failure path (not this service) owns lease cleanup.
    await broker.release(state.request_id)
