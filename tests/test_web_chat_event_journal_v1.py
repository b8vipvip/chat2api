import asyncio
import base64
import hashlib
import io
import os
import re
import stat
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app, web_chat_delivery_text
from app.telemetry import TelemetryStore
from app.user_console_v104_patch import install_user_console_v104_patch
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
    install_user_console_v104_patch(app_before_restart)

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
        wallet_user = app_before_restart.state.user_accounts.register(
            "restart-wallet@example.org", "strong-test-password",
        )
        wallet_user_id = wallet_user["user_id"]
        app_before_restart.state.user_accounts.attach_key(wallet_user_id, key_id)
        billing_before_restart = app_before_restart.state.user_billing
        billing_before_restart.credit(wallet_user_id, 10_000)
        price_snapshot = {
            "model_id": "gpt-5.6",
            "enabled": True,
            "input_usd_per_million": 1.0,
            "cached_input_usd_per_million": 0.2,
            "output_usd_per_million": 1.0,
        }
        billing_before_restart.reserve_for_request(
            wallet_user_id, request_id,
            model_id="gpt-5.6",
            estimated_cny_cents=1_000,
            input_tokens=12,
            output_tokens=300_000,
            price_snapshot=price_snapshot,
            price_catalog_snapshot={"gpt-5.6": price_snapshot},
            usd_cny_rate=7.0,
        )
        assert billing_before_restart.mark_dispatch_authorized(request_id, wallet_user_id)

        # A complete generated file is durably staged before the API process
        # exits. The terminal outbox replay must restore this exact request/
        # owner-bound stage and archive it, not silently drop the attachment.
        file_bytes = b"Generated notes survive the API restart.\\n"
        upload_id = "a" * 32
        staged = app_before_restart.state.generated_staging.ingest(
            request_id=request_id,
            owner_key_id=key_id,
            upload_id=upload_id,
            filename="notes.txt",
            mime="text/plain",
            sequence=0,
            expected_bytes=len(file_bytes),
            expected_sha256=hashlib.sha256(file_bytes).hexdigest(),
            base64_chunk=base64.b64encode(file_bytes).decode("ascii"),
            final=True,
        )
        assert staged["finished"] is True
        asyncio.run(app_before_restart.state.telemetry.append({
            "request_id": request_id,
            "client_id": client_id,
            "api_key_id": key_id,
            "api_key_name": "restart-owner",
            "auth_kind": "managed",
            "response_id": "resp_brokerrestart123",
            "requested_model": "gpt-5.6",
            "request_type": "web_chat",
            "web_chat_mode": True,
            "status": "running",
            "stream": True,
            "prompt_mode": "auto",
            "prompt_chars": 18,
            "attachments_count": 0,
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 0,
                "total_tokens": 12,
                "estimated": True,
                "estimator": "chat2api-heuristic-v1",
            },
            "timings": {},
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
    install_user_console_v104_patch(app_after_restart)
    with TestClient(app_after_restart) as second_server:
        recovering = app_after_restart.state.telemetry.get(request_id)
        assert recovering["status"] == "recovering"
        assert recovering["diagnostics"]["web_chat_request_recovery"] == "awaiting-worker-terminal-replay"
        assert recovering["response_text_provisional"] == "partial before restart"
        image_buffer = io.BytesIO()
        Image.new("RGB", (1, 1), (255, 0, 0)).save(image_buffer, format="PNG")
        image_base64 = base64.b64encode(image_buffer.getvalue()).decode("ascii")
        final_text = (
            "full answer from durable Worker terminal outbox\\n"
            + f"![generated image](data:image/png;base64,{image_base64})\\n"
            + f"![📎 notes.txt](chat2api-staged:{upload_id})\\n"
            + ("x" * 1_100_000)
        )

        # Model a crash between the completed telemetry append and its
        # accounting callback. The retry must settle the same request_id once
        # and only then ACK the durable Worker outbox.
        original_accounting = app_after_restart.state.telemetry.usage_accounting
        accounting_failures_remaining = {"count": 2}

        async def fail_first_completed_accounting(record):
            if record.get("status") == "completed" and accounting_failures_remaining["count"] > 0:
                accounting_failures_remaining["count"] -= 1
                raise RuntimeError("simulated usage ledger write interruption")
            await original_accounting(record)

        app_after_restart.state.telemetry.usage_accounting = fail_first_completed_accounting

        billing_after_restart = app_after_restart.state.user_billing
        original_record_charge = billing_after_restart.record_charge
        wallet_charge_failures_remaining = {"count": 1}

        def fail_first_wallet_charge(user_id, row, pricing):
            if wallet_charge_failures_remaining["count"] > 0:
                wallet_charge_failures_remaining["count"] -= 1
                raise RuntimeError("simulated wallet ledger write interruption")
            return original_record_charge(user_id, row, pricing)

        billing_after_restart.record_charge = fail_first_wallet_charge

        with second_server.websocket_connect(
            f"/ws/extensions/{client_id}?token={worker_token}",
        ) as worker:
            assert worker.receive_json()["type"] == "server.hello"
            worker.send_json({
                "type": "chat.completed",
                "request_id": request_id,
                "text": final_text,
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
                "accepted": False,
                "reason": "terminal_finalization_retryable",
            }
            # The next replay retries token settlement, which is independently
            # interrupted once. It must not ACK while either ledger is incomplete.
            worker.send_json({
                "type": "chat.completed",
                "request_id": request_id,
                "text": final_text,
                "diagnostics": {
                    "worker_transport_replayed": True,
                    "worker_transport_recovery": "transport-outbox-v47",
                },
            })
            accounting_retry_ack = worker.receive_json()
            assert accounting_retry_ack["accepted"] is False
            assert accounting_retry_ack["reason"] == "usage_or_delivery_terminal_not_durable"

            # The third exact replay settles API-key usage, reconciles the
            # admission-time wallet hold, and only then receives a positive ACK.
            worker.send_json({
                "type": "chat.completed",
                "request_id": request_id,
                "text": final_text,
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
            charged = app_after_restart.state.api_keys.keys[key_id].charged_requests
            assert charged[request_id] == recovered["usage"]["total_tokens"]
            assert accounting_failures_remaining["count"] == 0
            assert wallet_charge_failures_remaining["count"] == 0
            wallet_charge = billing_after_restart.charge_for_request(request_id)
            wallet_reservation = billing_after_restart.reservation_for_request(request_id)
            assert wallet_charge and wallet_charge["debited"] is True
            assert wallet_reservation and wallet_reservation["state"] == "settled"
            assert billing_after_restart.balance_cents(wallet_user_id) == (
                10_000 - wallet_charge["cost_cny_cents"]
            )

        recovered = app_after_restart.state.telemetry.get(request_id)
        assert recovered["status"] == "completed"
        assert recovered["response_text_available"] is True
        assert "data:image/png;base64," not in recovered["response_text"]
        assert "chat2api-staged:" not in recovered["response_text"]
        assert "/api/media/" in recovered["response_text"]
        assert "/api/generated-files/pending/" in recovered["response_text"]
        assert recovered["response_text_provisional"] == ""
        assert recovered["diagnostics"]["web_chat_terminal_replay_reconciled"] == "full-finalization"

        # The full-resolution image and the generated document must both be
        # independently downloadable after restart, with the original bytes.
        media_items = recovered.get("response_media") or []
        assert len(media_items) == 1
        media_record = app_after_restart.state.media_archive.describe(
            media_items[0]["id"], key_id,
        )
        archived_image, archived_mime = app_after_restart.state.media_archive.load(
            media_record["id"], media_record["token"],
        )
        assert archived_mime == "image/png"
        assert archived_image == image_buffer.getvalue()

        deadline = time.monotonic() + 3.0
        staged_files = []
        while time.monotonic() < deadline:
            staged_files = app_after_restart.state.deferred_generated_files.list_for_request(
                request_id, key_id,
            )
            if staged_files and staged_files[0]["status"] in {"ready", "failed"}:
                break
            time.sleep(0.01)
        assert len(staged_files) == 1
        assert staged_files[0]["status"] == "ready"
        pending_match = re.search(
            r"/api/generated-files/pending/(pgen_[A-Za-z0-9]+)/([A-Za-z0-9_-]+)",
            recovered["response_text"],
        )
        assert pending_match
        archived_file, archived_file_mime, archived_file_name = (
            app_after_restart.state.deferred_generated_files.read(
                pending_match.group(1), pending_match.group(2),
            )
        )
        assert archived_file == file_bytes
        assert archived_file_mime == "text/plain"
        assert archived_file_name == "notes.txt"

        assert recovered["usage"]["prompt_tokens"] == 12
        assert recovered["usage"]["completion_tokens"] > 0
        assert recovered["usage"]["total_tokens"] == (
            recovered["usage"]["prompt_tokens"] + recovered["usage"]["completion_tokens"]
        )
        snapshot = asyncio.run(
            app_after_restart.state.web_chat_event_journal.snapshot(request_id, key_id)
        )
        assert snapshot["terminal_status"] == "completed"
        # Worker evidence is bounded to 50k chars; delivery stores the full
        # post-archive text actually sent to Web Chat, with owned media URLs.
        assert snapshot["worker_partial_text"] == final_text[:50_000]
        assert snapshot["worker_text_truncated"] is True
        assert snapshot["delivered_text"] == recovered["response_text"]
        assert "data:image/png;base64," not in snapshot["delivered_text"]
        assert "chat2api-staged:" not in snapshot["delivered_text"]
        assert "/api/media/" in snapshot["delivered_text"]
        assert "/api/generated-files/pending/" in snapshot["delivered_text"]


def test_delivery_journal_resume_is_idempotent_and_appends_only_missing_suffix(tmp_path):
    async def scenario():
        journal = WebChatEventJournal(tmp_path)
        await journal.open()
        request_id = "req_delivery_resume_idempotent"
        owner = "key-delivery-resume"
        prefix = "The answer was partly persisted. "
        final_text = prefix + "This suffix must appear exactly once."

        first = await journal.append_delivery(request_id, owner, prefix)
        second = await journal.append_delivery(request_id, owner, final_text)
        repeated = await journal.append_delivery(request_id, owner, final_text)
        assert first["chunk_event_ids"]
        assert second["chunk_event_ids"]
        assert repeated["chunk_event_ids"] == []
        assert repeated["text_chars"] == len(final_text)

        snapshot = await journal.snapshot(request_id, owner)
        assert snapshot["delivered_text"] == final_text
        assert snapshot["delivered_text_truncated"] is False

        page = await journal.events_after(request_id, owner, 0, limit=512)
        replayed = "".join(
            item["data"]["delta"] for item in page["events"]
            if item["type"] == "delivery.delta"
        )
        assert replayed == final_text
        terminal = await journal.append_delivery_terminal(
            request_id, owner, len(final_text),
        )
        assert terminal > 0
        assert await journal.append_delivery(request_id, owner, final_text) == {
            "chunk_event_ids": [], "terminal_event_id": 0, "text_chars": len(final_text),
        }
        completed = await journal.snapshot(request_id, owner)
        assert completed["terminal_status"] == "completed"
        assert completed["delivered_text"] == final_text

    asyncio.run(scenario())


def test_delivery_journal_rejects_a_non_prefix_resume(tmp_path):
    async def scenario():
        journal = WebChatEventJournal(tmp_path)
        await journal.open()
        await journal.append_delivery("req_delivery_prefix_conflict", "key-prefix", "first version")
        with pytest.raises(ValueError, match="conflicts with its durable prefix"):
            await journal.append_delivery("req_delivery_prefix_conflict", "key-prefix", "different version")

    asyncio.run(scenario())


def test_final_message_citation_metadata_is_durable_in_worker_and_terminal_records(tmp_path):
    async def scenario():
        journal = WebChatEventJournal(tmp_path)
        await journal.open()
        metadata = {
            "annotations": [{
                "type": "url_citation",
                "start_index": 0,
                "end_index": 26,
                "url_citation": {
                    "url": "https://docs.example.org/api",
                    "title": "API reference",
                    "ref_id": "turn0search0",
                },
            }],
        }
        first = await journal.append_worker_event(
            "req_final_citation_journal", "key-final-citation",
            {
                "type": "chat.references",
                "message_id": "assistant-final",
                "metadata": metadata,
            },
        )
        terminal = await journal.append_worker_event(
            "req_final_citation_journal", "key-final-citation",
            {
                "type": "chat.completed",
                "message_id": "assistant-final",
                "text": "The API is documented here.",
                "metadata": metadata,
            },
        )
        assert (first, terminal) == (1, 2)
        page = await journal.events_after(
            "req_final_citation_journal", "key-final-citation", 0,
        )
        assert [item["type"] for item in page["events"]] == [
            "chat.references", "worker.terminal",
        ]
        expected = [{
            "url": "https://docs.example.org/api",
            "title": "API reference",
            "snippet": "",
            "id": "turn0search0",
            "start_index": 0,
            "end_index": 26,
        }]
        assert page["events"][0]["data"]["references"] == expected
        assert page["events"][1]["data"]["references"] == expected
        assert page["events"][1]["data"]["message_id"] == "assistant-final"
        snapshot = await journal.snapshot(
            "req_final_citation_journal", "key-final-citation",
        )
        assert snapshot["references"] == expected

    asyncio.run(scenario())



def test_durable_delivery_journal_stores_the_exact_native_citation_projection(tmp_path):
    async def scenario():
        journal = WebChatEventJournal(tmp_path)
        await journal.open()
        text = "Read the docs"
        terminal = {
            "diagnostics": {
                "native_citation_text_match": True,
                "response_boundary_prompt_echo_removed": False,
            },
            "metadata": {
                "annotations": [{
                    "type": "url_citation",
                    "start_index": 0,
                    "end_index": len(text),
                    "url_citation": {
                        "url": "https://docs.example.org/",
                        "title": "Documentation",
                        "ref_id": "turn0search0",
                    },
                }],
            },
        }
        display_text = web_chat_delivery_text(text, text, terminal)
        await journal.append_worker_event(
            "req_citation_delivery", "key-citation-delivery",
            {"type": "chat.completed", "text": text, "metadata": terminal["metadata"]},
        )
        delivery = await journal.append_delivery(
            "req_citation_delivery", "key-citation-delivery", display_text,
        )
        terminal_id = await journal.append_delivery_terminal(
            "req_citation_delivery", "key-citation-delivery", len(display_text),
        )
        snapshot = await journal.snapshot(
            "req_citation_delivery", "key-citation-delivery",
        )
        page = await journal.events_after(
            "req_citation_delivery", "key-citation-delivery", 0,
        )
        assert delivery["text_chars"] == len(display_text)
        assert terminal_id > 0
        assert snapshot["delivered_text"] == display_text
        assert page["events"][-2]["data"]["delta"] == display_text
        assert page["events"][-1]["data"]["text_chars"] == len(display_text)

    asyncio.run(scenario())


def test_main_wires_projected_text_to_live_and_replayed_delivery_journals():
    from pathlib import Path
    import app.main as main_module

    source = Path(main_module.__file__).read_text(encoding="utf-8")
    assert "delivery_text=display_final_text" in source
    assert "delivery_text=display_text" in source
    assert "ensure_replayed_delivery_terminal(request_id, owner, display_text)" in source



@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink assertions")
def test_event_journal_database_is_private_and_rejects_symlinks(tmp_path):
    private_root = tmp_path / "private"
    journal = WebChatEventJournal(private_root)
    asyncio.run(journal.open())
    assert stat.S_IMODE(journal.path.stat().st_mode) == 0o600

    linked_root = tmp_path / "linked"
    linked_root.mkdir()
    target = tmp_path / "outside.sqlite3"
    target.write_text("must remain untouched", encoding="utf-8")
    (linked_root / "web_chat_events.sqlite3").symlink_to(target)

    with pytest.raises(RuntimeError, match="symbolic links"):
        asyncio.run(WebChatEventJournal(linked_root).open())
    assert target.read_text(encoding="utf-8") == "must remain untouched"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink assertions")
def test_event_journal_rejects_symlinked_wal_sidecar(tmp_path):
    journal = WebChatEventJournal(tmp_path)
    asyncio.run(journal.open())
    wal_path = type(journal.path)(str(journal.path) + "-wal")
    wal_path.unlink(missing_ok=True)
    target = tmp_path / "outside-wal"
    target.write_text("must remain untouched", encoding="utf-8")
    wal_path.symlink_to(target)

    with pytest.raises(RuntimeError, match="symbolic links"):
        journal._connect()
    assert target.read_text(encoding="utf-8") == "must remain untouched"
