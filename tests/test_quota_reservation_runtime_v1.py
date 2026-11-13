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
    assert "/v1/chat/completions" in app.state.quota_reservation_runtime_coverage
    assert "/v1/responses" in app.state.quota_reservation_runtime_coverage
    assert "/v1/images/generations" in app.state.quota_reservation_runtime_coverage
    assert any("image_generation" in value for value in app.state.quota_reservation_runtime_coverage)
    assert "/v1/audio/speech" in app.state.quota_reservation_runtime_coverage
    assert "/v1/audio/conversations" in app.state.quota_reservation_runtime_coverage
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
    # Three deliberately unpriced reservations must fit while we prove that
    # missing usage never releases an in-flight hold.
    app, store, api_keys = _runtime(tmp_path, token_quota=None)
    callback = app.state.telemetry.usage_accounting

    for request_id, usage in (
        ("req_usage_missing_0001", None),
        ("req_usage_zero_0001", {"total_tokens": 0}),
        ("req_usage_fractional_0001", {"total_tokens": 1.5}),
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
    assert store.get("req_usage_fractional_0001")["state"] == "committing"

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
async def test_completed_usage_requires_durable_api_key_ledger_proof_before_settlement(tmp_path: Path) -> None:
    app, store, api_keys = _runtime(tmp_path)
    request_id = "req_charge_proof_missing_0001"
    reserve_request_quota(app, request_id, SimpleNamespace(key_id="key-1"), "prompt", 4096)

    async def interrupted_charge(record: dict) -> None:
        # Simulate an accounting owner that returned without persisting a
        # request_id ledger entry (for example, a missing key after restart).
        return None

    api_keys.account_completed_usage = interrupted_charge
    await app.state.telemetry.usage_accounting({
        "request_id": request_id, "api_key_id": "key-1", "status": "completed",
        "usage": {"total_tokens": 12},
    })
    assert store.get(request_id)["state"] == "committing"
    assert store.active_reserved_tokens("key-1") >= store.get(request_id)["reserved_tokens"]
    assert api_keys.keys["key-1"].tokens_used == 0
    store.close()


@pytest.mark.asyncio
async def test_startup_recovery_replays_completed_telemetry_after_pre_charge_crash(tmp_path: Path) -> None:
    from app.telemetry import TelemetryStore

    app, store, api_keys = _runtime(tmp_path)
    request_id = "req_replay_completed_terminal_0001"
    reserve_request_quota(app, request_id, SimpleNamespace(key_id="key-1"), "prompt", 4096)
    # Simulate a crash after the durable committing transition but before the
    # ApiKeyStore accounting owner wrote charged_requests.
    store.begin_commit(request_id, "key-1", 17)
    terminal = {
        "request_id": request_id, "api_key_id": "key-1", "status": "completed",
        "usage": {"total_tokens": 17},
    }
    telemetry = TelemetryStore(tmp_path)
    await telemetry.upsert(terminal)
    # Simulate a committing request that fell outside TelemetryStore's
    # in-memory window; recovery must find the persisted JSONL terminal.
    telemetry.items.clear()

    replayed = await store.recover_committing(telemetry, api_keys)
    assert replayed == 1
    assert api_keys.keys["key-1"].tokens_used == 17
    assert api_keys.charged_requests[request_id] == 17
    assert store.get(request_id)["state"] == "settled"
    assert await store.recover_committing(telemetry, api_keys) == 0
    assert store.reconcile(api_keys) == 0
    store.close()


@pytest.mark.asyncio
async def test_startup_recovery_replays_terminal_before_expiring_reserved_hold(tmp_path: Path) -> None:
    from app.telemetry import TelemetryStore

    app, store, api_keys = _runtime(tmp_path)
    request_id = "req_replay_before_begin_commit_0001"
    reservation = reserve_request_quota(
        app, request_id, SimpleNamespace(key_id="key-1"), "prompt", 4096,
    )
    telemetry = TelemetryStore(tmp_path)
    await telemetry.upsert({
        "request_id": request_id, "api_key_id": "key-1", "status": "completed",
        "usage": {"total_tokens": 17},
    })
    # Simulate a crash after the durable telemetry append but before the
    # accounting callback can move the reservation from reserved to committing.
    telemetry.items.clear()

    replayed = await store.recover_committing(telemetry, api_keys)
    assert replayed == 1
    assert api_keys.keys["key-1"].tokens_used == 17
    assert api_keys.charged_requests[request_id] == 17
    assert store.get(request_id)["state"] == "settled"
    assert store.expire_reservations(now=reservation["expires_at"] + 1) == 0
    assert await store.recover_committing(telemetry, api_keys) == 0
    store.close()


@pytest.mark.asyncio
async def test_startup_recovery_releases_reserved_terminal_failure(tmp_path: Path) -> None:
    from app.telemetry import TelemetryStore

    app, store, api_keys = _runtime(tmp_path)
    request_id = "req_replay_reserved_cancelled_0001"
    reserve_request_quota(
        app, request_id, SimpleNamespace(key_id="key-1"), "prompt", 4096,
    )
    telemetry = TelemetryStore(tmp_path)
    await telemetry.upsert({
        "request_id": request_id, "api_key_id": "key-1", "status": "cancelled",
    })
    telemetry.items.clear()

    # A crash before the accounting callback must not strand a cancelled
    # request's reservation until the TTL expires.
    assert await store.recover_committing(telemetry, api_keys) == 0
    assert store.get(request_id)["state"] == "released"
    assert store.active_reserved_tokens("key-1") == 0
    store.close()

def test_reconcile_accepts_store_level_idempotency_ledger_adapter(tmp_path: Path) -> None:
    app, store, api_keys = _runtime(tmp_path)
    request_id = "req_reconcile_store_ledger_0001"
    reserve_request_quota(
        app, request_id, SimpleNamespace(key_id="key-1"), "prompt", 4096,
    )
    store.begin_commit(request_id, "key-1", 17)
    # This adapter deliberately exposes the same durable request ledger at
    # store level rather than on each key object.
    api_keys.charged_requests[request_id] = 17

    assert store.reconcile(api_keys) == 1
    assert store.get(request_id)["state"] == "settled"
    assert store.active_reserved_tokens("key-1") == 0
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

def test_responses_api_reserves_and_settles_managed_key_usage(tmp_path: Path) -> None:
    import asyncio

    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from app.responses_model_routing_v108_patch import install_responses_model_routing_v108_patch
    from app.responses_v108_patch import install_responses_v108_patch

    app = create_app(Settings(
        CHAT2API_API_KEY="responses-quota-master",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    public, token = asyncio.run(app.state.api_keys.create("responses-quota"))
    asyncio.run(app.state.api_keys.update(public["key_id"], token_quota=10000))

    app.state.registry.resolve_client = lambda requested: "ext_responses_quota"

    async def fake_worker_send(client_id: str, payload: dict) -> None:
        if payload.get("type") == "chat.request":
            await app.state.broker.publish(payload["request_id"], {
                "type": "chat.completed",
                "text": "Responses quota settlement fixture",
            })

    app.state.registry.send = fake_worker_send
    # create_app() is the API core and intentionally has no production admin
    # asset owner. The v117 request-history column patch defers in this mode;
    # app.entry installs v94 first and applies the column in production.
    install_responses_model_routing_v108_patch(app)
    install_responses_v108_patch(app)
    # create_app() is the API core; the production C composition owner creates
    # this journal before installing the runtime adapter.
    app.state.quota_reservations = QuotaReservationStore(tmp_path)
    install_quota_reservation_runtime_v1(app)

    with TestClient(app) as client:
        response = client.post(
            "/v1/responses",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "gpt-5.6-sol",
                "input": "Hello Responses",
                "max_output_tokens": 64,
            },
        )

    assert response.status_code == 200, response.text
    record = next(
        item for item in app.state.telemetry.recent(10)
        if item.get("request_type") == "responses"
    )
    request_id = record["request_id"]
    reservation = app.state.quota_reservations.get(request_id)
    assert reservation is not None
    assert reservation["state"] == "settled"
    assert record["status"] == "completed"
    assert record["usage"]["total_tokens"] > 0
    assert app.state.api_keys.keys[public["key_id"]].tokens_used == record["usage"]["total_tokens"]
    app.state.quota_reservations.close()

def _managed_chat_quota_app(tmp_path: Path, token_quota: int):
    import asyncio

    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings(
        CHAT2API_API_KEY="chat-quota-master",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    public, token = asyncio.run(app.state.api_keys.create("chat-quota"))
    asyncio.run(app.state.api_keys.update(public["key_id"], token_quota=token_quota))
    app.state.quota_reservations = QuotaReservationStore(tmp_path)
    install_quota_reservation_runtime_v1(app)
    return app, public, token


def test_chat_completions_reserves_and_settles_managed_key_usage(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    app, public, token = _managed_chat_quota_app(tmp_path, token_quota=10000)
    app.state.registry.resolve_client = lambda requested: "ext_chat_quota"

    async def fake_worker_send(client_id: str, payload: dict) -> None:
        if payload.get("type") == "chat.request":
            await app.state.broker.publish(payload["request_id"], {
                "type": "chat.completed",
                "text": "Chat Completions quota settlement fixture",
            })

    app.state.registry.send = fake_worker_send
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "gpt-5.6-sol",
                "messages": [{"role": "user", "content": "Hello quota"}],
                "max_tokens": 64,
            },
        )

    assert response.status_code == 200, response.text
    record = next(
        item for item in app.state.telemetry.recent(20)
        if item.get("request_type") == "text"
        and item.get("api_key_id") == public["key_id"]
    )
    assert record["status"] == "completed"
    assert record["usage"]["total_tokens"] > 0
    reservation = app.state.quota_reservations.get(record["request_id"])
    assert reservation is not None
    assert reservation["state"] == "settled"
    assert app.state.api_keys.keys[public["key_id"]].tokens_used == record["usage"]["total_tokens"]
    app.state.quota_reservations.close()


def test_chat_completions_rejects_over_quota_before_worker_dispatch(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    app, public, token = _managed_chat_quota_app(tmp_path, token_quota=100)
    app.state.registry.resolve_client = lambda requested: "ext_chat_quota"
    sent: list[dict] = []

    async def fake_worker_send(client_id: str, payload: dict) -> None:
        sent.append(payload)

    app.state.registry.send = fake_worker_send
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "gpt-5.6-sol",
                "messages": [{"role": "user", "content": "Hello quota"}],
                "max_tokens": 64,
            },
        )

    assert response.status_code == 429, response.text
    assert sent == []
    assert app.state.api_keys.keys[public["key_id"]].tokens_used == 0
    assert app.state.quota_reservations.list_reservations(key_id=public["key_id"]) == []
    app.state.quota_reservations.close()


def test_images_api_reserves_and_settles_managed_key_usage(tmp_path: Path) -> None:
    import base64

    from fastapi.testclient import TestClient

    app, public, token = _managed_chat_quota_app(tmp_path, token_quota=10000)
    app.state.registry.resolve_client = lambda requested: "ext_image_quota"
    generated = base64.b64encode(b"generated-image-bytes").decode("ascii")

    async def fake_worker_send(client_id: str, payload: dict) -> None:
        if payload.get("type") == "image.request":
            await app.state.broker.publish(payload["request_id"], {
                "type": "image.completed",
                "request_id": payload["request_id"],
                "images": [{"b64_json": generated, "mime_type": "image/png"}],
            })

    app.state.registry.send = fake_worker_send
    with TestClient(app) as client:
        response = client.post(
            "/v1/images/generations",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "gpt-image", "prompt": "quota image", "response_format": "b64_json"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["b64_json"] == generated
    record = next(
        item for item in app.state.telemetry.recent(20)
        if item.get("request_type") == "image_generation"
        and item.get("api_key_id") == public["key_id"]
    )
    reservation = app.state.quota_reservations.get(record["request_id"])
    assert record["status"] == "completed"
    assert record["usage"]["estimated"] is True
    assert reservation is not None
    assert reservation["state"] == "settled"
    assert reservation["reserved_tokens"] >= 4096 + record["usage"]["prompt_tokens"]
    assert reservation["actual_tokens"] == record["usage"]["total_tokens"]
    assert app.state.api_keys.keys[public["key_id"]].tokens_used == record["usage"]["total_tokens"]
    assert record["diagnostics"]["quota_reservation_v1"]["provider_usage_verified"] is False
    app.state.quota_reservations.close()


def test_images_api_rejects_over_quota_before_worker_dispatch(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    app, public, token = _managed_chat_quota_app(tmp_path, token_quota=4096)
    app.state.registry.resolve_client = lambda requested: "ext_image_quota"
    sent: list[dict] = []

    async def fake_worker_send(client_id: str, payload: dict) -> None:
        sent.append(payload)

    app.state.registry.send = fake_worker_send
    with TestClient(app) as client:
        response = client.post(
            "/v1/images/generations",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "gpt-image", "prompt": "quota image"},
        )

    assert response.status_code == 429, response.text
    assert sent == []
    assert app.state.api_keys.keys[public["key_id"]].tokens_used == 0
    assert app.state.quota_reservations.list_reservations(key_id=public["key_id"]) == []
    rejected = next(
        item for item in app.state.telemetry.recent(20)
        if item.get("request_type") == "image_generation"
    )
    assert rejected["status"] == "error"
    assert rejected["diagnostics"]["quota_reservation_v1"]["state"] == "rejected"
    app.state.quota_reservations.close()


def test_images_api_worker_failure_releases_reserved_quota(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    app, public, token = _managed_chat_quota_app(tmp_path, token_quota=10000)
    app.state.registry.resolve_client = lambda requested: "ext_image_quota"

    async def fake_worker_send(client_id: str, payload: dict) -> None:
        if payload.get("type") == "image.request":
            await app.state.broker.publish(payload["request_id"], {
                "type": "image.error",
                "request_id": payload["request_id"],
                "error": "synthetic image worker failure",
            })

    app.state.registry.send = fake_worker_send
    with TestClient(app) as client:
        response = client.post(
            "/v1/images/generations",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "gpt-image", "prompt": "quota image"},
        )

    assert response.status_code == 502, response.text
    record = next(
        item for item in app.state.telemetry.recent(20)
        if item.get("request_type") == "image_generation"
        and item.get("api_key_id") == public["key_id"]
    )
    reservation = app.state.quota_reservations.get(record["request_id"])
    assert record["status"] in {"error", "failed", "cancelled", "stalled"}
    assert reservation is not None
    assert reservation["state"] == "released"
    assert app.state.api_keys.keys[public["key_id"]].tokens_used == 0
    app.state.quota_reservations.close()


def test_responses_image_generation_uses_the_same_managed_quota_reservation(tmp_path: Path) -> None:
    import asyncio
    import base64

    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    from app.responses_model_routing_v108_patch import install_responses_model_routing_v108_patch
    from app.responses_v108_patch import install_responses_v108_patch

    app = create_app(Settings(
        CHAT2API_API_KEY="responses-image-quota-master",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    public, token = asyncio.run(app.state.api_keys.create("responses-image-quota"))
    asyncio.run(app.state.api_keys.update(public["key_id"], token_quota=10000))
    app.state.registry.resolve_client = lambda requested: "ext_responses_image_quota"
    generated = base64.b64encode(b"generated-image-bytes").decode("ascii")

    async def fake_worker_send(client_id: str, payload: dict) -> None:
        if payload.get("type") == "image.request":
            await app.state.broker.publish(payload["request_id"], {
                "type": "image.completed",
                "request_id": payload["request_id"],
                "images": [{"b64_json": generated, "mime_type": "image/png"}],
            })

    app.state.registry.send = fake_worker_send
    install_responses_model_routing_v108_patch(app)
    install_responses_v108_patch(app)
    app.state.quota_reservations = QuotaReservationStore(tmp_path)
    install_quota_reservation_runtime_v1(app)

    with TestClient(app) as client:
        response = client.post(
            "/v1/responses",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "gpt-5.6-sol",
                "input": "Generate a quota test image",
                "tools": [{"type": "image_generation"}],
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["output"][0]["type"] == "image_generation_call"
    child_id = body["chat2api"]["request_id"]
    record = app.state.telemetry.get(child_id)
    reservation = app.state.quota_reservations.get(child_id)
    assert record["request_type"] == "image_generation"
    assert record["status"] == "completed"
    assert reservation is not None and reservation["state"] == "settled"
    assert app.state.api_keys.keys[public["key_id"]].tokens_used == record["usage"]["total_tokens"]
    parent_rows = [
        row for row in app.state.telemetry.recent(30)
        if row.get("request_type") == "responses" and row.get("api_key_id") == public["key_id"]
    ]
    assert parent_rows and parent_rows[0]["usage"]["total_tokens"] == 0
    app.state.quota_reservations.close()


def test_audio_speech_reserves_and_settles_managed_key_usage(tmp_path: Path) -> None:
    import base64

    from fastapi.testclient import TestClient
    from app.voice_patch import install_voice_patch

    app, public, token = _managed_chat_quota_app(tmp_path, token_quota=10000)
    install_voice_patch(app)
    app.state.registry.resolve_client = lambda requested: "ext_voice_quota"
    audio = base64.b64encode(b"fake-webm-opus-audio" * 8).decode("ascii")

    async def fake_worker_send(client_id: str, payload: dict) -> None:
        if payload.get("type") == "voice.request":
            await app.state.broker.publish(payload["request_id"], {
                "type": "image.completed",
                "kind": "voice",
                "request_id": payload["request_id"],
                "voice": {
                    "transcript": "voice quota transcript",
                    "b64_json": audio,
                    "mime_type": "audio/webm;codecs=opus",
                    "size": 160,
                },
            })

    app.state.registry.send = fake_worker_send
    with TestClient(app) as client:
        response = client.post(
            "/v1/audio/speech",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "gpt-live", "input": "say quota test"},
        )

    assert response.status_code == 200, response.text
    record = next(
        item for item in app.state.telemetry.recent(20)
        if item.get("request_type") == "voice_generation"
        and item.get("api_key_id") == public["key_id"]
    )
    reservation = app.state.quota_reservations.get(record["request_id"])
    assert record["status"] == "completed"
    assert record["usage"]["estimated"] is True
    assert reservation is not None and reservation["state"] == "settled"
    assert reservation["reserved_tokens"] >= 4096 + record["usage"]["prompt_tokens"]
    assert reservation["actual_tokens"] == record["usage"]["total_tokens"]
    assert app.state.api_keys.keys[public["key_id"]].tokens_used == record["usage"]["total_tokens"]
    assert record["diagnostics"]["quota_reservation_v1"]["provider_usage_verified"] is False
    app.state.quota_reservations.close()


def test_audio_conversation_reserves_and_settles_admission_floor(tmp_path: Path) -> None:
    import asyncio
    import base64

    from fastapi.testclient import TestClient
    from app.voice_patch import install_voice_patch

    app, public, token = _managed_chat_quota_app(tmp_path, token_quota=10000)
    install_voice_patch(app)
    app.state.registry.resolve_client = lambda requested: "ext_voice_quota"
    audio_file = asyncio.run(app.state.file_store.create(
        filename="quota-input.webm",
        data_base64=base64.b64encode(b"voice-input-bytes").decode("ascii"),
        mime_type="audio/webm",
        owner_key_id=public["key_id"],
        purpose="voice-input",
    ))
    audio = base64.b64encode(b"fake-webm-opus-audio" * 8).decode("ascii")

    async def fake_worker_send(client_id: str, payload: dict) -> None:
        if payload.get("type") == "voice.request":
            await app.state.broker.publish(payload["request_id"], {
                "type": "image.completed",
                "kind": "voice",
                "request_id": payload["request_id"],
                "voice": {
                    "transcript": "audio conversation transcript",
                    "b64_json": audio,
                    "mime_type": "audio/webm;codecs=opus",
                    "size": 160,
                },
            })

    app.state.registry.send = fake_worker_send
    with TestClient(app) as client:
        response = client.post(
            "/v1/audio/conversations",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "gpt-live", "audio_file_id": audio_file.file_id},
        )

    assert response.status_code == 200, response.text
    record = next(
        item for item in app.state.telemetry.recent(20)
        if item.get("request_type") == "voice_conversation"
        and item.get("api_key_id") == public["key_id"]
    )
    assert record["status"] == "completed"
    assert record["usage"]["total_tokens"] > 0
    assert record["usage"]["estimated"] is True
    reservation = app.state.quota_reservations.get(record["request_id"])
    assert reservation is not None and reservation["state"] == "settled"
    assert reservation["reserved_tokens"] >= 4096
    assert reservation["actual_tokens"] == record["usage"]["total_tokens"]
    assert record["diagnostics"]["quota_reservation_v1"]["provider_usage_verified"] is False
    assert app.state.api_keys.keys[public["key_id"]].tokens_used == record["usage"]["total_tokens"]
    app.state.quota_reservations.close()


def test_audio_conversation_zero_text_releases_admission_hold_without_fake_charge(tmp_path: Path) -> None:
    import asyncio
    import base64

    from fastapi.testclient import TestClient
    from app.voice_patch import install_voice_patch

    app, public, token = _managed_chat_quota_app(tmp_path, token_quota=10000)
    install_voice_patch(app)
    app.state.registry.resolve_client = lambda requested: "ext_voice_quota"
    audio_file = asyncio.run(app.state.file_store.create(
        filename="silent-input.webm",
        data_base64=base64.b64encode(b"silent-audio-bytes").decode("ascii"),
        mime_type="audio/webm",
        owner_key_id=public["key_id"],
        purpose="voice-input",
    ))
    audio = base64.b64encode(b"fake-webm-opus-audio" * 8).decode("ascii")

    async def fake_worker_send(client_id: str, payload: dict) -> None:
        if payload.get("type") == "voice.request":
            await app.state.broker.publish(payload["request_id"], {
                "type": "image.completed",
                "kind": "voice",
                "request_id": payload["request_id"],
                "voice": {
                    "transcript": "",
                    "b64_json": audio,
                    "mime_type": "audio/webm;codecs=opus",
                    "size": 160,
                },
            })

    app.state.registry.send = fake_worker_send
    with TestClient(app) as client:
        response = client.post(
            "/v1/audio/conversations",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "gpt-live", "audio_file_id": audio_file.file_id},
        )

    assert response.status_code == 200, response.text
    record = next(
        item for item in app.state.telemetry.recent(20)
        if item.get("request_type") == "voice_conversation"
        and item.get("api_key_id") == public["key_id"]
    )
    assert record["status"] == "completed"
    assert record["usage"]["total_tokens"] == 0
    reservation = app.state.quota_reservations.get(record["request_id"])
    assert reservation is not None and reservation["state"] == "released"
    assert app.state.api_keys.keys[public["key_id"]].tokens_used == 0
    app.state.quota_reservations.close()


def test_audio_conversation_rejects_over_quota_before_worker_dispatch(tmp_path: Path) -> None:
    import asyncio
    import base64

    from fastapi.testclient import TestClient
    from app.voice_patch import install_voice_patch

    app, public, token = _managed_chat_quota_app(tmp_path, token_quota=4095)
    install_voice_patch(app)
    app.state.registry.resolve_client = lambda requested: "ext_voice_quota"
    audio_file = asyncio.run(app.state.file_store.create(
        filename="quota-input.webm",
        data_base64=base64.b64encode(b"voice-input-bytes").decode("ascii"),
        mime_type="audio/webm",
        owner_key_id=public["key_id"],
        purpose="voice-input",
    ))
    sent: list[dict] = []

    async def fake_worker_send(client_id: str, payload: dict) -> None:
        sent.append(payload)

    app.state.registry.send = fake_worker_send
    with TestClient(app) as client:
        response = client.post(
            "/v1/audio/conversations",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "gpt-live", "audio_file_id": audio_file.file_id},
        )

    assert response.status_code == 429, response.text
    assert sent == []
    assert app.state.api_keys.keys[public["key_id"]].tokens_used == 0
    assert app.state.quota_reservations.list_reservations(key_id=public["key_id"]) == []
    rejected = next(
        item for item in app.state.telemetry.recent(20)
        if item.get("request_type") == "voice_conversation"
    )
    assert rejected["status"] == "error"
    assert rejected["diagnostics"]["quota_reservation_v1"]["state"] == "rejected"
    app.state.quota_reservations.close()


def test_audio_speech_rejects_over_quota_before_worker_dispatch(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from app.voice_patch import install_voice_patch

    app, public, token = _managed_chat_quota_app(tmp_path, token_quota=4096)
    install_voice_patch(app)
    app.state.registry.resolve_client = lambda requested: "ext_voice_quota"
    sent: list[dict] = []

    async def fake_worker_send(client_id: str, payload: dict) -> None:
        sent.append(payload)

    app.state.registry.send = fake_worker_send
    with TestClient(app) as client:
        response = client.post(
            "/v1/audio/speech",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "gpt-live", "input": "say quota test"},
        )

    assert response.status_code == 429, response.text
    assert sent == []
    assert app.state.api_keys.keys[public["key_id"]].tokens_used == 0
    assert app.state.quota_reservations.list_reservations(key_id=public["key_id"]) == []
    rejected = next(
        item for item in app.state.telemetry.recent(20)
        if item.get("request_type") == "voice_generation"
    )
    assert rejected["status"] == "error"
    assert rejected["diagnostics"]["quota_reservation_v1"]["state"] == "rejected"


def test_audio_worker_failure_releases_reserved_quota(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from app.voice_patch import install_voice_patch

    app, public, token = _managed_chat_quota_app(tmp_path, token_quota=10000)
    install_voice_patch(app)
    app.state.registry.resolve_client = lambda requested: "ext_voice_quota"

    async def fake_worker_send(client_id: str, payload: dict) -> None:
        if payload.get("type") == "voice.request":
            await app.state.broker.publish(payload["request_id"], {
                "type": "image.error",
                "kind": "voice",
                "request_id": payload["request_id"],
                "error": "synthetic voice worker failure",
            })

    app.state.registry.send = fake_worker_send
    with TestClient(app) as client:
        response = client.post(
            "/v1/audio/speech",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "gpt-live", "input": "say quota test"},
        )

    assert response.status_code == 502, response.text
    record = next(
        item for item in app.state.telemetry.recent(20)
        if item.get("request_type") == "voice_generation"
        and item.get("api_key_id") == public["key_id"]
    )
    reservation = app.state.quota_reservations.get(record["request_id"])
    assert record["status"] == "error"
    assert reservation is not None and reservation["state"] == "released"
    assert app.state.api_keys.keys[public["key_id"]].tokens_used == 0
    app.state.quota_reservations.close()
