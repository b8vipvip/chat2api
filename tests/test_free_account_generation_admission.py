from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from app.account_generation_admission_patch import install_account_generation_admission_patch
from app.config import Settings
from app.main import create_app
from app.model_capability_routing_patch import _MODEL_CONTEXT, install_model_capability_routing_patch
from app.v21_patch import install_v21_patch
from app.v21_routing_patch import install_v21_routing_patch
from app.v21_1_patch import install_v21_1_patch


def build_app(data_dir: Path):
    settings = Settings(
        CHAT2API_API_KEY="test-master-key",
        CHAT2API_PAIRING_CODE="test-pair-code",
        CHAT2API_ADMIN_USERNAME="admin",
        CHAT2API_ADMIN_PASSWORD="strong-password-for-generation-admission",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=data_dir,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=30,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )
    app = create_app(settings)
    install_v21_patch(app)
    install_v21_routing_patch(app)
    install_v21_1_patch(app)
    install_model_capability_routing_patch(app)
    install_account_generation_admission_patch(app)
    return app


async def add_online_client(app, name: str, account_type: str, models: list[str]):
    client_id, _token = await app.state.registry.register(
        name,
        "Chrome",
        "0.8.11",
        {"account_type": account_type, "models": models},
    )
    # Selection only needs the registry to regard this client as online. No
    # websocket send is performed by these admission tests.
    app.state.registry.sockets[client_id] = object()
    return client_id


def test_free_account_uses_one_generation_slot_but_keeps_configured_capacity():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            app = build_app(Path(tmp))
            free_id = await add_online_client(app, "free", "free", ["gpt-5.5-mini"])
            app.state.concurrency_config["client_limits"][free_id] = 5
            broker = app.state.broker

            first = await broker.create("req_free_1", free_id)
            snapshot = broker.capacity_snapshot(free_id)
            assert snapshot["configured_limit_units"] == 5
            assert snapshot["limit_units"] == 1
            assert snapshot["account_type"] == "free"
            assert snapshot["account_generation_queue"] is True
            assert not broker.can_accept(free_id, 1)

            second_task = asyncio.create_task(broker.create("req_free_2", free_id))
            await asyncio.sleep(0.05)
            assert not second_task.done(), "second Free request must wait instead of dispatching concurrently"

            await broker.release(first.request_id)
            second = await asyncio.wait_for(second_task, timeout=1.0)
            assert second.request_id == "req_free_2"
            assert second.diagnostics["account_generation_limit"] == 1
            assert second.diagnostics["account_generation_configured_limit"] == 5
            assert second.diagnostics["extension_capacity_wait_ms"] >= 40
            await broker.release(second.request_id)

    asyncio.run(scenario())


def test_busy_free_worker_is_queued_candidate_instead_of_no_compatible_worker_503():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            app = build_app(Path(tmp))
            free_id = await add_online_client(app, "free", "free", ["gpt-5.5-mini"])
            app.state.concurrency_config["client_limits"][free_id] = 5
            broker = app.state.broker
            first = await broker.create("req_free_active", free_id)

            token = _MODEL_CONTEXT.set({"model": "gpt-5.5-mini", "needs_multimodal": False})
            try:
                selected = app.state.registry.resolve_client(None)
            finally:
                _MODEL_CONTEXT.reset(token)
            assert selected == free_id

            await broker.release(first.request_id)

    asyncio.run(scenario())


def test_paid_account_preserves_configured_parallel_generation_capacity():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            app = build_app(Path(tmp))
            paid_id = await add_online_client(app, "paid", "paid", ["gpt-5.6-sol", "gpt-5.5-mini"])
            app.state.concurrency_config["client_limits"][paid_id] = 5
            broker = app.state.broker

            states = [await broker.create(f"req_paid_{index}", paid_id) for index in range(3)]
            snapshot = broker.capacity_snapshot(paid_id)
            assert snapshot["configured_limit_units"] == 5
            assert snapshot["limit_units"] == 5
            assert snapshot["active_requests"] == 3
            assert broker.can_accept(paid_id, 1)

            for state in states:
                await broker.release(state.request_id)

    asyncio.run(scenario())


def test_entry_installs_account_admission_after_final_model_routing():
    root = Path(__file__).resolve().parents[1]
    entry = (root / "app" / "entry.py").read_text(encoding="utf-8")
    patch = (root / "app" / "account_generation_admission_patch.py").read_text(encoding="utf-8")

    assert "FREE_ACCOUNT_GENERATION_LIMIT = 1" in patch
    assert "DEFAULT_FREE_QUEUE_WAIT_SECONDS = 180.0" in patch
    assert '"account_generation_admission": PATCH_ID' in patch
    assert "install_account_generation_admission_patch(app)" in entry
    assert entry.index("install_model_capability_routing_patch(app)") < entry.index("install_account_generation_admission_patch(app)")
