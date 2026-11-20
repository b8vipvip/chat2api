import asyncio

from app.broker import RequestBroker
from app.web_chat_event_journal_v1 import WebChatEventJournal


def test_mode1_worker_terminal_ack_requires_a_durable_owner_scoped_journal_record(tmp_path):
    async def scenario():
        journal = WebChatEventJournal(tmp_path)
        await journal.open()
        broker = RequestBroker()
        broker.event_journal = journal

        state = await broker.create("req_durable_ack_123", "worker-durable")
        state.mode1_web_chat = True
        state.mode1_owner_key_id = "key-durable"
        socket = object()
        state.dispatch_socket = socket

        accepted = await broker.publish_worker_event(
            state.request_id,
            {
                "type": "chat.completed",
                "request_id": state.request_id,
                "text": "durable terminal",
            },
            client_id="worker-durable",
            websocket=socket,
            current_socket=socket,
        )
        assert accepted is True
        assert state.diagnostics["web_chat_worker_terminal_journaled_type"] == "chat.completed"
        event_id = state.diagnostics["web_chat_worker_terminal_journaled_event_id"]
        assert event_id > 0
        snapshot = await journal.snapshot(state.request_id, "key-durable")
        assert snapshot["last_event_id"] == event_id
        events = await journal.events_after(state.request_id, "key-durable", 0)
        assert events["events"][-1]["type"] == "worker.terminal"
        assert events["events"][-1]["data"]["status"] == "completed"

    asyncio.run(scenario())


def test_mode1_worker_terminal_journal_failure_never_claims_durable_ack():
    class BrokenJournal:
        async def append_worker_event(self, *_args, **_kwargs):
            raise OSError("disk unavailable")

    async def scenario():
        broker = RequestBroker()
        broker.event_journal = BrokenJournal()
        state = await broker.create("req_failed_ack_123", "worker-durable")
        state.mode1_web_chat = True
        state.mode1_owner_key_id = "key-durable"
        socket = object()
        state.dispatch_socket = socket
        accepted = await broker.publish_worker_event(
            state.request_id,
            {
                "type": "chat.completed",
                "request_id": state.request_id,
                "text": "not durably journaled",
            },
            client_id="worker-durable",
            websocket=socket,
            current_socket=socket,
        )
        assert accepted is True
        assert "web_chat_worker_terminal_journaled_type" not in state.diagnostics
        assert state.diagnostics["web_chat_event_journal_error"] == "OSError"

    asyncio.run(scenario())
