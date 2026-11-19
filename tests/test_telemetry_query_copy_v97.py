from __future__ import annotations

import asyncio

from app.telemetry import TelemetryStore


def test_query_rows_are_copies_so_list_redaction_cannot_destroy_detail_text(tmp_path) -> None:
    store = TelemetryStore(tmp_path)
    asyncio.run(
        store.upsert(
            {
                "request_id": "req_12345678",
                "status": "completed",
                "final_prompt": "prompt body",
                "response_text": "reply body",
            }
        )
    )

    result = store.query(limit=10)
    row = result["data"][0]
    row.pop("final_prompt")
    row.pop("response_text")

    detail = store.get("req_12345678")
    assert detail is not None
    assert detail["final_prompt"] == "prompt body"
    assert detail["response_text"] == "reply body"


def test_create_only_rejects_duplicate_request_id_without_rewriting_history(tmp_path) -> None:
    from app.telemetry import DuplicateRequestIdentity

    store = TelemetryStore(tmp_path)
    first = asyncio.run(store.upsert({
        "request_id": "req_unique_123456",
        "status": "completed",
        "response_text": "original answer",
    }, create_only=True))

    try:
        asyncio.run(store.upsert({
            "request_id": "req_unique_123456",
            "status": "error",
            "response_text": "replayed request",
        }, create_only=True))
    except DuplicateRequestIdentity:
        pass
    else:
        raise AssertionError("duplicate request identity was accepted")

    assert store.get("req_unique_123456") == first
