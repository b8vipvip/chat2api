from __future__ import annotations

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
        reconnected_worker_a_socket = object()
        state.dispatch_socket = worker_a_socket

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
        # A new socket with the same client_id is still not the socket that
        # received the original dispatch and cannot inherit the request.
        assert not await broker.publish_worker_event(
            state.request_id,
            forged_terminal,
            client_id="worker-a",
            websocket=reconnected_worker_a_socket,
            current_socket=reconnected_worker_a_socket,
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
        # Release the first lease before simulating a separate request on the
        # same Worker; one Worker cannot own two concurrent Broker requests.
        await broker.release(state.request_id)

        # An API/server timeout can close the response before the Worker
        # terminal arrives. completed_mono is therefore also a terminal
        # cutoff even when no Worker terminal_event_type was observed.
        timed_out = await broker.create("req_server_timeout_123", "worker-a")
        timed_out.dispatch_socket = worker_a_socket
        timed_out.completed_mono = 123.0
        late_worker_terminal = {
            "type": "chat.completed",
            "request_id": timed_out.request_id,
            "text": "late after API timeout",
        }
        assert not await broker.publish_worker_event(
            timed_out.request_id,
            late_worker_terminal,
            client_id="worker-a",
            websocket=worker_a_socket,
            current_socket=worker_a_socket,
        )
        assert not await broker.publish(timed_out.request_id, late_worker_terminal)
        assert timed_out.terminal_event_type is None
        assert timed_out.queue.empty()
        assert timed_out.final_future is not None and not timed_out.final_future.done()
        await broker.release(timed_out.request_id)

    asyncio.run(scenario())


def test_all_request_scoped_worker_sidecars_require_original_dispatch_socket() -> None:
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    # File chunks, DOM evidence, native sidecars, probes and generic
    # Chat/Image progress+terminal ingress must share the exact socket check.
    assert source.count(".owns_dispatch_socket(") == 5
    assert "state.completed_mono is None" in source
    assert "active_state.completed_mono is None" in source


def test_extension_websocket_routes_all_request_events_through_worker_binding() -> None:
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    event_handler = source.split('if request_id and message_type in {', 1)[1].split(
        "except WebSocketDisconnect:", 1
    )[0]
    assert "broker.publish_worker_event(" in event_handler
    assert "await broker.publish(request_id, message)" not in event_handler

    # The only direct Broker publication in this socket handler is the
    # server-owned disconnect failure, restricted to requests whose
    # dispatch_socket is the exact socket being closed.
    disconnect_handler = source.split("except WebSocketDisconnect:", 1)[1]
    assert "state.client_id == client_id" in disconnect_handler
    assert 'getattr(state, "dispatch_socket", None) is websocket' in disconnect_handler


def test_tool_activity_is_bounded_nonterminal_and_original_socket_scoped() -> None:
    async def scenario() -> None:
        broker = RequestBroker()
        state = await broker.create("req_tool_activity_123", "worker-tool")
        socket = object()
        state.dispatch_socket = socket

        call = {
            "type": "chat.tool.call", "request_id": state.request_id,
            "tool_name": "browser.search", "call_id": "call-123",
            "status": "in_progress", "arguments": "x" * 10000,
        }
        assert not await broker.publish_worker_event(
            state.request_id, call, client_id="other-worker",
            websocket=socket, current_socket=socket,
        )
        assert await broker.publish_worker_event(
            state.request_id, call, client_id="worker-tool",
            websocket=socket, current_socket=socket,
        )
        observed = await state.queue.get()
        assert observed["type"] == "chat.tool.call"
        assert observed["status"] == "running"
        assert len(observed["arguments"]) == 8000
        assert state.text == ""
        assert state.terminal_event_type is None
        assert state.completed_mono is None

        result = {
            "type": "chat.tool.result", "request_id": state.request_id,
            "tool_name": "browser.search", "call_id": "call-123",
            "result_id": "result-123", "status": "finished_successfully",
            "output": "found two useful pages",
            "metadata": {"unbounded": "must not be forwarded"},
        }
        assert await broker.publish_worker_event(
            state.request_id, result, client_id="worker-tool",
            websocket=socket, current_socket=socket,
        )
        observed_result = await state.queue.get()
        assert observed_result == {
            "type": "chat.tool.result", "tool_name": "browser.search",
            "call_id": "call-123", "status": "completed",
            "output": "found two useful pages", "result_id": "result-123",
        }
        assert state.text == "" and state.terminal_event_type is None

        assert await broker.publish_worker_event(
            state.request_id,
            {"type": "chat.completed", "request_id": state.request_id, "text": "done"},
            client_id="worker-tool", websocket=socket, current_socket=socket,
        )
        assert not await broker.publish_worker_event(
            state.request_id, result, client_id="worker-tool",
            websocket=socket, current_socket=socket,
        )
        await broker.release(state.request_id)

    asyncio.run(scenario())


def test_mode1_tool_reference_cards_are_bounded_and_public_https_only() -> None:
    async def scenario() -> None:
        broker = RequestBroker()
        state = await broker.create("req_tool_refs_123", "worker-tool-refs")
        socket = object()
        state.dispatch_socket = socket
        state.mode1_web_chat = True
        event = {
            "type": "chat.tool.result",
            "request_id": state.request_id,
            "tool_name": "browser.search",
            "call_id": "call-refs-123",
            "result_id": "result-refs-123",
            "status": "completed",
            "output": "Found sources.",
            "metadata": {
                "content_references": [{
                    "title": "Python docs",
                    "snippet": "Official documentation",
                    "safe_urls": ["https://docs.python.org/3/library/"],
                }, {
                    "title": "Private address",
                    "url": "https://127.0.0.1/private",
                }],
                "search_result_groups": [{
                    "entries": [{
                        "title": "Second source",
                        "url": "https://example.org/reference",
                    }],
                }],
                "unrelated": {"url": "https://ignored.example.net/private"},
            },
        }
        assert await broker.publish_worker_event(
            state.request_id, event, client_id="worker-tool-refs",
            websocket=socket, current_socket=socket,
        )
        observed = await state.queue.get()
        assert observed["references"] == [
            {
                "url": "https://docs.python.org/3/library/",
                "title": "Python docs",
                "snippet": "Official documentation",
            },
            {
                "url": "https://example.org/reference",
                "title": "Second source",
                "snippet": "",
            },
        ]
        assert "metadata" not in observed
        assert state.text == "" and state.terminal_event_type is None
        await broker.release(state.request_id)

    asyncio.run(scenario())


def test_mode1_tool_events_use_the_existing_worker_and_web_chat_stream_only() -> None:
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    event_handler = source.split('if request_id and message_type in {', 1)[1].split(
        "except WebSocketDisconnect:", 1
    )[0]
    assert '"chat.tool.call", "chat.tool.result"' in event_handler
    assert 'request.scope.get("chat2api_mode1_web_chat") is True' in source
    assert '"chat2api_tool": tool_delta' in source
