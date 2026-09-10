from __future__ import annotations

import asyncio
import subprocess
import tempfile
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI

from app.broker import RequestBroker
from app.capacity_queue_v57_patch import install_capacity_queue_v57_patch
from app.capacity_scheduler_v58 import install_capacity_scheduler_v58


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class FakeRegistry:
    def __init__(self) -> None:
        self.routing_key_context: ContextVar[str | None] = ContextVar("test_routing_key", default=None)
        self.clients = {"ext_test": SimpleNamespace(metadata={"account_type": "paid"}, connection_enabled=True)}
        self.sent: list[tuple[str, dict]] = []

    def online_client_ids(self) -> list[str]:
        return ["ext_test"]

    def summaries(self) -> list[dict]:
        return [{"client_id": "ext_test"}]

    async def send(self, client_id: str, message: dict) -> None:
        self.sent.append((client_id, dict(message)))


def build_v57(tmp: str) -> tuple[FastAPI, FakeRegistry, RequestBroker]:
    app = FastAPI()
    registry = FakeRegistry()
    broker = RequestBroker()
    broker.client_active_requests = {}
    app.state.registry = registry
    app.state.broker = broker
    app.state.settings = SimpleNamespace(data_dir=Path(tmp))
    app.state.concurrency_config = {}
    install_capacity_queue_v57_patch(app)
    return app, registry, broker


def build_v58(tmp: str) -> tuple[FastAPI, FakeRegistry, RequestBroker]:
    app = FastAPI()
    registry = FakeRegistry()
    broker = RequestBroker()
    broker.client_active_requests = {}
    app.state.registry = registry
    app.state.broker = broker
    app.state.settings = SimpleNamespace(data_dir=Path(tmp))
    app.state.concurrency_config = {}
    install_capacity_scheduler_v58(app)
    return app, registry, broker


def test_v57_remains_historical_but_v58_is_final_entry_owner() -> None:
    hook = read("app/server_worker_sync_lifespan_patch.py")
    old = read("app/capacity_queue_v57_patch.py")
    new = read("app/capacity_scheduler_v58.py")
    assert 'PATCH_ID = "worker-key-capacity-queue-v57"' in old
    assert "install_capacity_queue_v57_patch" not in hook
    assert "install_capacity_scheduler_v58" in hook
    assert 'PATCH_ID = "server-single-authority-scheduler-v58"' in new
    assert 'broker.create = create' in new
    assert 'broker.release = release' in new
    assert '"api_key_capacity_limit": 1' in new
    assert '"browser_side_api_fifo": False' in new
    assert '"reserve_window_target": 0' in new


def test_v58_same_api_is_fifo_one_active_while_distinct_keys_can_share_worker_capacity() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _app, registry, broker = build_v58(tmp)

            token = registry.routing_key_context.set("key_a")
            first = await broker.create("req_a1", "ext_test")
            registry.routing_key_context.reset(token)

            token = registry.routing_key_context.set("key_a")
            second_same = asyncio.create_task(broker.create("req_a2", "ext_test"))
            registry.routing_key_context.reset(token)
            await asyncio.sleep(0.02)
            assert not second_same.done()

            token = registry.routing_key_context.set("key_b")
            other = await asyncio.wait_for(broker.create("req_b1", "ext_test"), timeout=0.5)
            registry.routing_key_context.reset(token)
            assert other.diagnostics["api_key_capacity_id"] == "key_b"
            assert broker.capacity_snapshot("ext_test")["active_requests"] == 2
            assert not second_same.done()

            await broker.release(first.request_id)
            admitted = await asyncio.wait_for(second_same, timeout=0.5)
            assert admitted.diagnostics["api_key_capacity_id"] == "key_a"
            assert admitted.diagnostics["api_key_capacity_limit"] == 1
            assert admitted.diagnostics["browser_side_api_fifo"] is False

            await broker.release(other.request_id)
            await broker.release(admitted.request_id)

    asyncio.run(scenario())


def test_v58_persists_no_speculative_windows_and_fixed_per_api_limit() -> None:
    source = read("app/capacity_scheduler_v58.py")
    for token in (
        '"default_reserve_windows": 0',
        '"default_key_concurrency": 1',
        '"speculative_window_pool": False',
        '"per_api_key_limit": 1',
        '"server_api_fifo_revision": 58',
        '"strict_api_fifo": True',
        '"browser_side_api_fifo": False',
    ):
        assert token in source


def test_v57_rate_limit_behavior_remains_available_to_v58() -> None:
    old = read("app/capacity_queue_v57_patch.py")
    new = read("app/capacity_scheduler_v58.py")
    content = read("chrome_extension/content_rate_limit_guard_v52.js")
    for source in (old, new):
        assert '"too many requests"' in source
        assert "RATE_LIMIT_DEFAULT_SECONDS = 300" in source
        assert "rate_limit_cooldown_active" in source
    assert "terminateActiveRequest" in content
    assert 'type: "chat.error"' in content


def test_release_contract_is_v02274_and_worker_bundle_0830() -> None:
    runtime = read("app/runtime_contract.py")
    manifest = read("chrome_extension/manifest.json")
    marker = read("chrome_extension/content_bundle_marker_v48.js")
    preflight = read("chrome_extension/background_runtime_preflight_v48.js")
    contract = read("chrome_extension/content_runtime_contract_v48.js")
    assert 'SERVER_RUNTIME_VERSION = "0.22.74"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.30"' in runtime
    assert '"version": "0.8.30"' in manifest
    assert 'bundle: "0.8.30"' in marker
    assert 'REQUIRED_BUNDLE = "0.8.30"' in preflight
    assert 'REQUIRED_BUNDLE = "0.8.30"' in contract
    assert '"capacity_scheduler_v58": True' in runtime
    assert '"worker_single_route_authority_v30": True' in runtime
    assert '"window_observer_v90": True' in runtime


def test_capacity_and_runtime_javascript_parse() -> None:
    for path in (
        "app/admin_v21_5.js",
        "chrome_extension/background_capacity_control_v35.js",
        "chrome_extension/background_worker_disabled_window_guard_v86.js",
        "chrome_extension/background_worker_master_switch_v61.js",
        "chrome_extension/background_runtime_preflight_v48.js",
        "chrome_extension/content_runtime_contract_v48.js",
        "chrome_extension/content_runtime_contract_v71.js",
        "chrome_extension/conversation_routing.js",
        "chrome_extension/conversation_dispatch.js",
        "chrome_extension/background_window_observer_v90.js",
    ):
        result = subprocess.run(
            ["node", "--check", str(ROOT / path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{path}: {result.stderr}"
