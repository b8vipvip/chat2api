import asyncio

import pytest

from app.telemetry import TelemetryStore
from app.web_chat_event_journal_v1 import WebChatEventJournal


def test_event_journal_persists_ordered_delivery_and_owner_scope(tmp_path):
    async def scenario():
        journal = WebChatEventJournal(tmp_path)
        await journal.open()

        worker_delta = await journal.append_worker_event(
            "req_journal_123", "key-owner-a",
            {"type": "chat.delta", "delta": "provisional "},
        )
        tool_event = await journal.append_worker_event(
            "req_journal_123", "key-owner-a",
            {
                "type": "chat.tool.result",
                "tool_name": "browser.search",
                "call_id": "call-123",
                "result_id": "result-123",
                "status": "completed",
                "output": "Found a source",
                "references": [{
                    "url": "https://docs.python.org/3/library/",
                    "title": "Python docs",
                    "snippet": "Official docs",
                }],
            },
        )
        delivery = await journal.append_delivery(
            "req_journal_123", "key-owner-a", "Final answer " * 500,
        )

        assert worker_delta == 1
        assert tool_event == 2
        assert delivery["chunk_event_ids"] == [3, 4]
        assert delivery["terminal_event_id"] == 5

        page = await journal.events_after("req_journal_123", "key-owner-a", 2)
        assert [event["id"] for event in page["events"]] == [3, 4, 5]
        assert [event["type"] for event in page["events"]] == [
            "delivery.delta", "delivery.delta", "delivery.completed",
        ]
        snapshot = await journal.snapshot("req_journal_123", "key-owner-a")
        assert snapshot["terminal_status"] == "completed"
        assert snapshot["delivered_text"] == "Final answer " * 500
        assert snapshot["worker_partial_text"] == "provisional "

        # Reopening a fresh journal object must preserve sequence and content.
        reopened = WebChatEventJournal(tmp_path)
        await reopened.open()
        restored = await reopened.snapshot("req_journal_123", "key-owner-a")
        assert restored == snapshot
        hidden = await reopened.snapshot("req_journal_123", "key-owner-b")
        assert hidden["last_event_id"] == 0
        with pytest.raises(PermissionError):
            await reopened.append_worker_event(
                "req_journal_123", "key-owner-b",
                {"type": "chat.delta", "delta": "cross-owner"},
            )

    asyncio.run(scenario())


def test_event_journal_rejects_unbounded_and_unrecognized_worker_metadata(tmp_path):
    async def scenario():
        journal = WebChatEventJournal(tmp_path)
        await journal.open()
        assert await journal.append_worker_event(
            "req_journal_safe", "key-safe",
            {
                "type": "chat.tool.result",
                "tool_name": "browser.search",
                "call_id": "call-safe",
                "status": "completed",
                "output": "ok",
                "metadata": {
                    "private": "must not be stored",
                    "url": "https://ignored.example.org",
                },
            },
        ) == 1
        assert await journal.append_worker_event(
            "req_journal_safe", "key-safe",
            {"type": "chat.diagnostics", "diagnostics": {"secret": "not persisted"}},
        ) is None
        page = await journal.events_after("req_journal_safe", "key-safe", 0)
        assert page["events"] == [{
            "id": 1,
            "type": "chat.tool.result",
            "data": {
                "tool_name": "browser.search",
                "call_id": "call-safe",
                "result_id": "",
                "status": "completed",
                "output": "ok",
            },
        }]

    asyncio.run(scenario())


def test_running_mode1_request_is_marked_stalled_after_broker_restart(tmp_path):
    async def scenario():
        journal = WebChatEventJournal(tmp_path)
        await journal.open()
        await journal.append_worker_event(
            "req_restart_123", "key-restart",
            {"type": "chat.delta", "delta": "partial answer"},
        )
        telemetry = TelemetryStore(tmp_path)
        telemetry._remember({
            "request_id": "req_restart_123",
            "api_key_id": "key-restart",
            "web_chat_mode": True,
            "status": "running",
            "response_text": "",
            "diagnostics": {},
        })

        recovered = await telemetry.recover_running_after_restart(
            active_request_ids=set(), event_journal=journal,
        )
        assert recovered == 1
        row = telemetry.get("req_restart_123")
        assert row["status"] == "stalled"
        assert row["response_text"] == ""
        assert row["response_text_available"] is False
        assert row["response_text_provisional"] == "partial answer"
        assert row["diagnostics"]["web_chat_request_recovery"] == (
            "broker-state-lost-after-server-restart"
        )
        snapshot = await journal.snapshot("req_restart_123", "key-restart")
        assert snapshot["terminal_status"] == "stalled"

        # Mode 2 and already terminal requests are not rewritten by this path.
        telemetry._remember({
            "request_id": "req_mode2_123", "api_key_id": "master",
            "web_chat_mode": False, "status": "running",
        })
        telemetry._remember({
            "request_id": "req_done_123", "api_key_id": "key-restart",
            "web_chat_mode": True, "status": "completed",
        })
        assert await telemetry.recover_running_after_restart(
            active_request_ids=set(), event_journal=journal,
        ) == 0
        assert telemetry.get("req_mode2_123")["status"] == "running"
        assert telemetry.get("req_done_123")["status"] == "completed"

    asyncio.run(scenario())
