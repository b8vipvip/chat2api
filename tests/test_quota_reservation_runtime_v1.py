from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException

from app.quota_reservations import QuotaReservationStore
from app.quota_reservation_runtime_v1 import install_quota_reservation_runtime_v1, reserve_request_quota


class FakeKey:
    def __init__(self, token_quota: int | None = 10000) -> None:
        self.token_quota = token_quota
        self.tokens_used = 0

    def public(self) -> dict[str, bool]:
        return {"enabled": True}


class FakeApiKeys:
    def __init__(self, token_quota: int | None = 10000) -> None:
        self.keys = {"key-1": FakeKey(token_quota)}
        self.charged_requests: dict[str, int] = {}

    async def account_completed_usage(self, record: dict) -> None:
        request_id = str(record["request_id"])
        if request_id in self.charged_requests:
            return
        usage = record.get("usage") or {}
        amount = max(0, int(usage.get("total_tokens") or 0))
        self.keys[str(record["api_key_id"])].tokens_used += amount
        self.charged_requests[request_id] = amount


def _runtime(tmp_path: Path, token_quota: int | None = 10000):
    app = FastAPI()
    api_keys = FakeApiKeys(token_quota)
    store = QuotaReservationStore(tmp_path)
    app.state.api_keys = api_keys
    app.state.quota_reservations = store
    app.state.telemetry = SimpleNamespace(usage_accounting=api_keys.account_completed_usage)
    install_quota_reservation_runtime_v1(app)
    return app, store, api_keys


def test_runtime_reserves_prompt_plus_conservative_output_floor(tmp_path: Path) -> None:
    app, store, api_keys = _runtime(tmp_path)
    reservation = reserve_request_quota(
        app, "req_runtime_0001", SimpleNamespace(key_id="key-1"), "hello world", 4096,
    )
    assert reservation is not None
    assert reservation["state"] == "reserved"
    assert reservation["reserved_tokens"] >= 4097
    assert app.state.quota_reservation_runtime_enabled is True
    assert app.state.quota_reservation_runtime_coverage == ["/v1/chat/completions"]
    store.close()


def test_runtime_leaves_master_key_on_unmetered_path(tmp_path: Path) -> None:
    app, store, api_keys = _runtime(tmp_path)
    reservation = reserve_request_quota(
        app, "req_master_unmetered_0001",
        SimpleNamespace(key_id="master", kind="master"), "hello world", 4096,
    )
    assert reservation is None
    assert store.get("req_master_unmetered_0001") is None
    store.close()


def test_runtime_denies_reservation_when_completed_plus_inflight_exceeds_quota(tmp_path: Path) -> None:
    app, store, api_keys = _runtime(tmp_path, token_quota=100)
    with pytest.raises(HTTPException) as error:
        reserve_request_quota(
            app, "req_runtime_0002", SimpleNamespace(key_id="key-1"), "hello", 4096,
        )
    assert error.value.status_code == 429
    assert store.get("req_runtime_0002") is None
    store.close()


@pytest.mark.asyncio
async def test_nonterminal_telemetry_updates_do_not_release_reservation(tmp_path: Path) -> None:
    app, store, api_keys = _runtime(tmp_path)
    reserve_request_quota(app, "req_runtime_0003", SimpleNamespace(key_id="key-1"), "prompt", 4096)
    callback = app.state.telemetry.usage_accounting
    for status in ("queued", "running"):
        await callback({"request_id": "req_runtime_0003", "api_key_id": "key-1", "status": status})
        assert store.get("req_runtime_0003")["state"] == "reserved"
    store.close()


@pytest.mark.asyncio
async def test_completed_usage_without_positive_total_keeps_reservation_until_ledger_proof(tmp_path: Path) -> None:
    app, store, api_keys = _runtime(tmp_path)
    callback = app.state.telemetry.usage_accounting

    for request_id, usage in (
        ("req_usage_missing_0001", None),
        ("req_usage_zero_0001", {"total_tokens": 0}),
    ):
        reserve_request_quota(
            app, request_id, SimpleNamespace(key_id="key-1"), "prompt", 4096,
        )
        record = {
            "request_id": request_id, "api_key_id": "key-1", "status": "completed",
        }
        if usage is not None:
            record["usage"] = usage
        await callback(record)
        reservation = store.get(request_id)
        assert reservation["state"] == "committing"
        assert store.active_reserved_tokens("key-1") >= reservation["reserved_tokens"]

    assert api_keys.keys["key-1"].tokens_used == 0
    assert store.reconcile(api_keys) == 0
    assert store.get("req_usage_missing_0001")["state"] == "committing"
    assert store.get("req_usage_zero_0001")["state"] == "committing"

    # A later terminal replay with real usage can complete the same
    # reservation exactly once without dropping the conservative hold.
    await callback({
        "request_id": "req_usage_missing_0001", "api_key_id": "key-1",
        "status": "completed", "usage": {"total_tokens": 12},
    })
    assert store.get("req_usage_missing_0001")["state"] == "settled"
    assert api_keys.keys["key-1"].tokens_used == 12
    assert store.get("req_usage_zero_0001")["state"] == "committing"
    store.close()


@pytest.mark.asyncio
async def test_completed_usage_commits_once_and_terminal_failure_releases(tmp_path: Path) -> None:
    app, store, api_keys = _runtime(tmp_path)
    callback = app.state.telemetry.usage_accounting
    reserve_request_quota(app, "req_runtime_0004", SimpleNamespace(key_id="key-1"), "prompt", 4096)
    completed = {
        "request_id": "req_runtime_0004", "api_key_id": "key-1", "status": "completed",
        "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
    }
    await callback(completed)
    await callback(completed)
    assert store.get("req_runtime_0004")["state"] == "settled"
    assert api_keys.keys["key-1"].tokens_used == 12
    assert api_keys.charged_requests["req_runtime_0004"] == 12

    reserve_request_quota(app, "req_runtime_0005", SimpleNamespace(key_id="key-1"), "prompt", 4096)
    await callback({"request_id": "req_runtime_0005", "api_key_id": "key-1", "status": "cancelled"})
    assert store.get("req_runtime_0005")["state"] == "released"
    assert api_keys.keys["key-1"].tokens_used == 12
    store.close()
