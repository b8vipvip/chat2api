"""Mode 2 shared application boundary: real broker, fake transport.

These tests do not simulate or replace request-v6 finality. The only final
event in the positive case is deliberately published by the existing Broker.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
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
async def test_command_is_not_affected_by_nested_adapter_or_wire_mutation() -> None:
    attachments = [{"file_id": "file_one", "metadata": {"labels": ["original"]}}]
    options = {"model": "gpt-5.6", "tool": {"arguments": {"mode": "safe"}}}
    command = ChatDispatch.build("req_no_aliasing", "hi", attachments, options)
    attachments[0]["metadata"]["labels"].append("adapter-mutated")
    options["tool"]["arguments"]["mode"] = "unexpected"
    wire = command.worker_message()
    assert wire["attachments"][0]["metadata"]["labels"] == ["original"]
    assert wire["options"]["tool"]["arguments"]["mode"] == "safe"
    wire["attachments"][0]["metadata"]["labels"].append("wire-mutated")
    wire["options"]["tool"]["arguments"]["mode"] = "wire-mutated"
    assert command.worker_message()["attachments"][0]["metadata"]["labels"] == ["original"]
    assert command.worker_message()["options"]["tool"]["arguments"]["mode"] == "safe"


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


@pytest.mark.asyncio
async def test_dispatch_requires_live_matching_nonterminal_broker_lease() -> None:
    registry = FakeRegistry()
    broker = RequestBroker()
    service = ChatRequestService(registry, broker)

    with pytest.raises(RuntimeError, match="reserved before Worker dispatch"):
        await service.dispatch(
            "ext_expected", request_id="req_unreserved", prompt="hi",
            attachments=[], options={},
        )

    state = await service.reserve("req_owned", "ext_owner")
    with pytest.raises(RuntimeError, match="different Worker"):
        await service.dispatch(
            "ext_other", request_id=state.request_id, prompt="hi",
            attachments=[], options={},
        )
    assert registry.sent == []

    await broker.publish(state.request_id, {"type": "chat.completed", "text": "already done"})
    with pytest.raises(RuntimeError, match="already terminal"):
        await service.dispatch(
            "ext_owner", request_id=state.request_id, prompt="replay",
            attachments=[], options={},
        )
    assert registry.sent == []
    await broker.release(state.request_id)


def test_both_real_mode2_adapters_use_one_shared_service(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from app.responses_v108_patch import install_responses_v108_patch

    app = create_app(Settings(
        CHAT2API_API_KEY="mode2-integration-test-master",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    install_responses_v108_patch(app)
    registry = app.state.registry
    registry.resolve_client = lambda requested: "ext_mode2_both"
    service = app.state.chat_request_service
    original_reserve = service.reserve
    original_dispatch = service.dispatch
    admitted: list[str] = []
    dispatched: list[str] = []

    async def observe_reserve(request_id: str, client_id: str):
        admitted.append(request_id)
        return await original_reserve(request_id, client_id)

    async def observe_dispatch(client_id: str, *, request_id: str, prompt: str, attachments, options):
        dispatched.append(request_id)
        return await original_dispatch(
            client_id, request_id=request_id, prompt=prompt,
            attachments=attachments, options=options,
        )

    async def fake_worker(client_id: str, payload: dict):
        if payload.get("type") == "chat.request":
            await app.state.broker.publish(payload["request_id"], {
                "type": "chat.completed", "text": "shared-service-fixture",
            })

    service.reserve = observe_reserve
    service.dispatch = observe_dispatch
    registry.send = fake_worker
    headers = {"Authorization": "Bearer mode2-integration-test-master"}

    with TestClient(app) as client:
        completion = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "gpt-5.6-sol", "messages": [{"role": "user", "content": "Hello"}]},
        )
        assert completion.status_code == 200, completion.text
        assert completion.json()["choices"][0]["message"]["content"] == "shared-service-fixture"

        response = client.post(
            "/v1/responses",
            headers=headers,
            json={"model": "gpt-5.6-sol", "input": "Hello Responses"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["output_text"] == "shared-service-fixture"

    assert len(admitted) == 2
    assert dispatched == admitted
    assert admitted[0].startswith("req_")
    assert admitted[1].startswith("resp_req_")
    assert admitted[0] != admitted[1]
    assert app.state.broker.requests == {}



def test_both_api_adapters_release_the_shared_lease_after_dispatch_failure(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from app.responses_v108_patch import install_responses_v108_patch

    app = create_app(Settings(
        CHAT2API_API_KEY="mode2-dispatch-failure-master",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    install_responses_v108_patch(app)
    app.state.registry.resolve_client = lambda requested: "ext_mode2_send_failure"

    async def fail_worker_send(client_id: str, payload: dict) -> None:
        if payload.get("type") == "chat.request":
            raise ConnectionError("worker-send-failed")

    app.state.registry.send = fail_worker_send
    headers = {"Authorization": "Bearer mode2-dispatch-failure-master"}

    with TestClient(app) as client:
        chat = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "gpt-5.6-sol", "messages": [{"role": "user", "content": "Hello"}]},
        )
        assert chat.status_code == 503, chat.text
        assert app.state.broker.requests == {}
        assert "ext_mode2_send_failure" not in app.state.registry.busy_clients

        responses = client.post(
            "/v1/responses",
            headers=headers,
            json={"model": "gpt-5.6-sol", "input": "Hello Responses"},
        )
        assert responses.status_code == 503, responses.text
        assert app.state.broker.requests == {}
        assert "ext_mode2_send_failure" not in app.state.registry.busy_clients


def test_composed_production_entry_uses_the_single_shared_service(tmp_path) -> None:
    script = r'''
from app.entry import app
from app.chat_request_service import ChatRequestService
assert isinstance(app.state.chat_request_service, ChatRequestService)
assert app.state.chat_request_service.broker is app.state.broker
assert app.state.chat_request_service.registry is app.state.registry
print("production entry shared ChatRequestService wiring passed")
'''
    env = dict(os.environ)
    env["CHAT2API_DATA_DIR"] = str(tmp_path / "production-entry")
    env["CHAT2API_API_KEY"] = "mode2-entry-wiring-test"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=75,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert "production entry shared ChatRequestService wiring passed" in result.stdout
