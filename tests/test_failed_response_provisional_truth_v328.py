"""Regression from 2026-11-10 request req_b734ffd3: failed pages retain
unverified text internally, but must never present it as final API response.
"""
import asyncio

from app.telemetry import TelemetryStore


RID = "req_b734ffd3fd1d4d20a7bc2466dab68200"


def test_stalled_request_preserves_forensic_candidate_but_clears_final_response(tmp_path):
    store = TelemetryStore(tmp_path)
    provisional = "Working reply: " + ("partial chart\n" * 800)
    async def record():
        await store.upsert({
            "request_id": RID, "status": "running",
            "response_text": provisional,
            "response_text_available": True,
            "completion_chars": len(provisional),
        })
        await store.upsert({
            "request_id": RID, "status": "stalled",
            "error": "Request exceeded its browser lease",
            "completion_chars": 0,
        })
    asyncio.run(record())
    row = store.get(RID)
    assert row["status"] == "stalled"
    assert row["response_text"] == ""
    assert row["response_text_available"] is False
    assert row["completion_chars"] == 0
    assert row["response_text_provisional"] == provisional
    assert row["response_text_provisional_chars"] == len(provisional)
    assert row["response_text_provisional_unverified"] is True
    assert "response_text_provisional" not in store.query(limit=5)["data"][0]
    assert store.get(RID)["response_text_provisional"] == provisional
    assert "response_text_provisional" in (tmp_path / "request_history.jsonl").read_text()


def test_error_and_cancelled_never_promote_partial_text_even_with_same_status_rewrite(tmp_path):
    async def record(store, request_id, failure):
        await store.upsert({
            "request_id": request_id, "status": "running",
            "response_text": "temporary render only",
        })
        await store.upsert({"request_id": request_id, "status": failure})
        await store.merge_diagnostics(request_id, {"http_disconnect_seen": True})
    store = TelemetryStore(tmp_path)
    asyncio.run(record(store, "req_errv328", "error"))
    asyncio.run(record(store, "req_cancelv328", "cancelled"))
    for request_id in ("req_errv328", "req_cancelv328"):
        row = store.get(request_id)
        assert not row["response_text"]
        assert not row["response_text_available"]
        assert row["response_text_provisional"] == "temporary render only"
        assert row["completion_chars"] == 0


def test_completed_reply_remains_authoritative_and_is_not_renamed_provisional(tmp_path):
    store = TelemetryStore(tmp_path)
    async def record():
        await store.upsert({"request_id": "req_completev328", "status": "running",
                            "response_text": "early provisional"})
        await store.upsert({"request_id": "req_completev328", "status": "completed",
                            "response_text": "verified terminal reply",
                            "response_text_available": True, "completion_chars": 23})
    asyncio.run(record())
    row = store.get("req_completev328")
    assert row["response_text"] == "verified terminal reply"
    assert row["response_text_available"] is True
    assert row["completion_chars"] == 23
    assert "response_text_provisional" not in row


def test_forensic_candidate_is_bounded_and_later_failure_does_not_resurrect_it(tmp_path):
    store = TelemetryStore(tmp_path)
    text = "a" * 60000
    async def record():
        await store.upsert({"request_id": "req_bigv328", "status": "running",
                            "response_text": text})
        await store.upsert({"request_id": "req_bigv328", "status": "stalled"})
        await store.upsert({"request_id": "req_bigv328", "status": "stalled",
                            "response_text": "late conflicting partial"})
    asyncio.run(record())
    row = store.get("req_bigv328")
    assert row["response_text"] == ""
    assert row["response_text_provisional"] == "a" * 50000
    assert row["response_text_provisional_chars"] == 60000
    assert row["status"] == "stalled"
