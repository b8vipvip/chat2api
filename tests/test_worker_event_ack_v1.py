import asyncio

from fastapi.testclient import TestClient

from app.broker import RequestState
from app.config import Settings
from app.main import create_app


def _register_worker(app):
    return asyncio.run(app.state.registry.register(
        "Worker ACK test",
        "Chrome",
        "test",
        {"account_type": "plus", "chatgpt_logged_in": True},
    ))


def test_worker_nonterminal_ack_requires_mode1_journal_commit(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    client_id, token = _register_worker(app)
    request_id = "req_worker_event_ack_123"
    state = RequestState(request_id, client_id)
    state.mode1_web_chat = True
    state.mode1_owner_key_id = "key_owner_123"
    app.state.broker.requests[request_id] = state

    with TestClient(app) as client:
        with client.websocket_connect(
            f"/ws/extensions/{client_id}?token={token}"
        ) as socket:
            assert socket.receive_json()["type"] == "server.hello"
            state.dispatch_socket = app.state.registry.sockets[client_id]
            socket.send_json({
                "type": "chat.delta",
                "request_id": request_id,
                "worker_event_id": f"{request_id}:1",
                "delta": "durable token",
            })
            ack = socket.receive_json()
            assert ack["type"] == "chat.event.ack"
            assert ack["request_id"] == request_id
            assert ack["worker_event_id"] == f"{request_id}:1"
            assert ack["accepted"] is True
            assert ack["durable"] is True
            assert ack["event_id"] > 0

            events = asyncio.run(app.state.web_chat_event_journal.events_after(
                request_id, "key_owner_123", 0,
            ))
            assert events["events"][0]["type"] == "worker.delta"
            assert events["events"][0]["data"]["delta"] == "durable token"

            # A successful Broker queue publication is not enough for Mode 1:
            # the Worker must retain and retry the event until the durable
            # owner-scoped journal append succeeds.
            second_id = "req_worker_event_ack_failure_456"
            second_state = RequestState(second_id, client_id)
            second_state.mode1_web_chat = True
            second_state.mode1_owner_key_id = "key_owner_123"
            second_state.dispatch_socket = state.dispatch_socket
            app.state.broker.requests[second_id] = second_state

            journal = app.state.web_chat_event_journal
            original_append = journal.append_worker_event

            async def fail_append(*_args, **_kwargs):
                raise OSError("disk unavailable")

            journal.append_worker_event = fail_append
            try:
                socket.send_json({
                    "type": "chat.delta",
                    "request_id": second_id,
                    "worker_event_id": f"{second_id}:1",
                    "delta": "must retry",
                })
                failed_ack = socket.receive_json()
            finally:
                journal.append_worker_event = original_append

            assert failed_ack["type"] == "chat.event.ack"
            assert failed_ack["accepted"] is False
            assert failed_ack["durable"] is False
            assert failed_ack["discarded"] is False
            assert failed_ack["reason"] == "journal-not-durable"
