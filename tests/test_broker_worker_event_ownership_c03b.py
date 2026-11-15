from __future__ import annotations

import ast
import asyncio
from pathlib import Path

from app.broker import RequestBroker


ROOT = Path(__file__).resolve().parents[1]


def test_worker_event_publication_requires_assigned_worker_and_current_socket() -> None:
    async def scenario() -> None:
        broker = RequestBroker()
        state = await broker.create("req_worker_binding_123", "worker-a")
        worker_a_socket = object()
        worker_b_socket = object()
        stale_worker_a_socket = object()

        forged_terminal = {
            "type": "chat.completed",
            "request_id": state.request_id,
            "text": "forged cross-worker answer",
        }

        # A different authenticated Worker cannot complete another Worker's
        # request even if it knows the request ID and uses its current socket.
        assert not await broker.publish_worker_event(
            state.request_id,
            forged_terminal,
            client_id="worker-b",
            websocket=worker_b_socket,
            current_socket=worker_b_socket,
        )
        # A replaced/stale socket cannot publish for the same Worker identity.
        assert not await broker.publish_worker_event(
            state.request_id,
            forged_terminal,
            client_id="worker-a",
            websocket=stale_worker_a_socket,
            current_socket=worker_a_socket,
        )

        assert state.terminal_event_type is None
        assert state.queue.empty()
        assert state.final_future is not None and not state.final_future.done()

        # Only the currently authenticated socket assigned by the Broker can
        # publish the authoritative terminal and resolve the request future.
        assert await broker.publish_worker_event(
            state.request_id,
            {
                "type": "chat.completed",
                "request_id": state.request_id,
                "text": "authoritative answer",
            },
            client_id="worker-a",
            websocket=worker_a_socket,
            current_socket=worker_a_socket,
        )
        assert state.terminal_event_type == "chat.completed"
        assert state.final_future is not None and state.final_future.result() == "authoritative answer"

        # The first terminal remains final; a second terminal is rejected.
        assert not await broker.publish_worker_event(
            state.request_id,
            {"type": "chat.error", "request_id": state.request_id, "error": "late"},
            client_id="worker-a",
            websocket=worker_a_socket,
            current_socket=worker_a_socket,
        )
        assert state.terminal_event_type == "chat.completed"

    asyncio.run(scenario())


def test_extension_websocket_routes_all_request_events_through_worker_binding() -> None:
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    extension_socket = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "extension_socket"
    )
    calls = [
        node for node in ast.walk(extension_socket)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert any(call.func.attr == "publish_worker_event" for call in calls)
    assert not any(
        call.func.attr == "publish"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "broker"
        for call in calls
    )
