import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
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
        assert delivery["terminal_event_id"] == 0
        terminal_id = await journal.append_delivery_terminal(
            "req_journal_123", "key-owner-a", delivery["text_chars"],
        )
        assert terminal_id == 5

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
        await journal.append_worker_event(
            "req_restart_123", "key-restart",
            {"type": "chat.completed", "text": "full Worker terminal answer"},
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
        assert row["status"] == "recovering"
        assert row["response_text"] == ""
        assert row["response_text_available"] is False
        assert row["response_text_provisional"] == "full Worker terminal answer"
        assert row["diagnostics"]["web_chat_request_recovery"] == (
            "awaiting-worker-terminal-replay"
        )
        snapshot = await journal.snapshot("req_restart_123", "key-restart")
        assert snapshot["terminal_status"] == ""

        deadline = row["diagnostics"]["web_chat_recovery_deadline_at"]
        expired = await telemetry.expire_recovering_after_restart(
            journal, now=deadline + 1,
        )
        assert expired == 1
        stalled = telemetry.get("req_restart_123")
        assert stalled["status"] == "stalled"
        assert stalled["response_text_provisional"] == "full Worker terminal answer"
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



def test_event_journal_first_terminal_outcome_is_absorbing(tmp_path):
    async def scenario():
        journal = WebChatEventJournal(tmp_path)
        await journal.open()
        delivery = await journal.append_delivery(
            "req_absorb_123", "key-absorb", "verified response",
        )
        assert delivery["chunk_event_ids"] == [1]
        terminal_id = await journal.append_delivery_terminal(
            "req_absorb_123", "key-absorb", 17,
        )
        assert terminal_id == 2

        # Late retries, chunks, and Worker observations must not append
        # records after the first terminal event, even if they disagree.
        assert await journal.append_terminal(
            "req_absorb_123", "key-absorb", "stalled", "late watchdog",
        ) is None
        assert await journal.append_terminal(
            "req_absorb_123", "key-absorb", "error", "late Worker retry",
        ) is None
        assert await journal.append_delivery_terminal(
            "req_absorb_123", "key-absorb", 17,
        ) == 0
        assert await journal.append_delivery(
            "req_absorb_123", "key-absorb", "late text",
        ) == {
            "chunk_event_ids": [],
            "terminal_event_id": 0,
            "text_chars": 9,
        }
        assert await journal.append_worker_event(
            "req_absorb_123", "key-absorb",
            {"type": "chat.delta", "delta": "late worker text"},
        ) is None

        page = await journal.events_after("req_absorb_123", "key-absorb", 0)
        assert [event["id"] for event in page["events"]] == [1, 2]
        assert [event["type"] for event in page["events"]] == [
            "delivery.delta", "delivery.completed",
        ]
        snapshot = await journal.snapshot("req_absorb_123", "key-absorb")
        assert snapshot["last_event_id"] == 2
        assert snapshot["terminal_status"] == "completed"
        assert snapshot["terminal_message"] == ""
        assert snapshot["delivered_text"] == "verified response"
        assert snapshot["worker_partial_text"] == ""

    asyncio.run(scenario())



def test_worker_terminal_outbox_replays_across_two_server_instances(tmp_path):
    settings = Settings(
        CHAT2API_API_KEY="restart-master-key",
        CHAT2API_PAIRING_CODE="restart-pair-code",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )
    request_id = "req_brokerrestart123"
    app_before_restart = create_app(settings)

    with TestClient(app_before_restart) as first_server:
        paired = first_server.post(
            "/api/extensions/register",
            headers={"X-Pairing-Code": "restart-pair-code"},
            json={
                "name": "Chrome Worker",
                "version": "0.22.181",
                "metadata": {
                    "chatgpt_login_state": "ready",
                    "chatgpt_login_composer_ready": True,
                },
            },
        )
        assert paired.status_code == 200, paired.text
        client_id, worker_token = paired.json()["client_id"], paired.json()["token"]
        key_response = first_server.post(
            "/api/admin/keys",
            headers={"Authorization": "Bearer restart-master-key"},
            json={"name": "restart-owner"},
        )
        assert key_response.status_code == 200, key_response.text
        key_id = key_response.json()["key"]["key_id"]
        asyncio.run(app_before_restart.state.telemetry.append({
            "request_id": request_id,
            "client_id": client_id,
            "api_key_id": key_id,
            "requested_model": "gpt-5.6",
            "request_type": "web_chat",
            "web_chat_mode": True,
            "status": "running",
            "stream": True,
            "diagnostics": {"route": "web-chat"},
        }))
        asyncio.run(app_before_restart.state.web_chat_event_journal.append_worker_event(
            request_id, key_id, {"type": "chat.delta", "delta": "partial before restart"},
        ))
        assert app_before_restart.state.telemetry.get(request_id)["status"] == "running"

    # A second FastAPI application against the same data directory models a
    # process restart: in-memory Broker state is gone, SQLite and Worker
    # extension credentials/outbox identity remain durable.
    app_after_restart = create_app(settings)
    with TestClient(app_after_restart) as second_server:
        recovering = app_after_restart.state.telemetry.get(request_id)
        assert recovering["status"] == "recovering"
        assert recovering["diagnostics"]["web_chat_request_recovery"] == "awaiting-worker-terminal-replay"
        assert recovering["response_text_provisional"] == "partial before restart"

        with second_server.websocket_connect(
            f"/ws/extensions/{client_id}?token={worker_token}",
        ) as worker:
            assert worker.receive_json()["type"] == "server.hello"
            worker.send_json({
                "type": "chat.completed",
                "request_id": request_id,
                "text": "full answer from durable Worker terminal outbox",
                "diagnostics": {
                    "worker_transport_replayed": True,
                    "worker_transport_recovery": "transport-outbox-v47",
                },
            })
            ack = worker.receive_json()
            assert ack == {
                "type": "chat.terminal.ack",
                "request_id": request_id,
                "terminal_type": "chat.completed",
                "accepted": True,
                "recovered": True,
            }
            # Simulate the first ACK being lost on the network. The durable
            # replay projection must recognize this exact same Worker event.
            worker.send_json({
                "type": "chat.completed",
                "request_id": request_id,
                "text": "full answer from durable Worker terminal outbox",
                "diagnostics": {
                    "worker_transport_replayed": True,
                    "worker_transport_recovery": "transport-outbox-v47",
                },
            })
            duplicate_ack = worker.receive_json()
            assert duplicate_ack["accepted"] is True
            assert duplicate_ack["terminal_type"] == "chat.completed"
            assert duplicate_ack["already_finalized"] is True

        recovered = app_after_restart.state.telemetry.get(request_id)
        assert recovered["status"] == "stalled"
        assert recovered["response_text_available"] is False
        assert recovered["diagnostics"]["web_chat_terminal_replay_reconciled"] == "provisional-only"
        snapshot = asyncio.run(
            app_after_restart.state.web_chat_event_journal.snapshot(request_id, key_id)
        )
        assert snapshot["terminal_status"] == "stalled"
        assert snapshot["worker_partial_text"] == "full answer from durable Worker terminal outbox"
