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


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class FakeRegistry:
    def __init__(self) -> None:
        self.routing_key_context: ContextVar[str | None] = ContextVar(
            "test_routing_key", default=None
        )
        self.clients = {
            "ext_test": SimpleNamespace(
                metadata={"account_type": "free"}, connection_enabled=True
            )
        }
        self.sent: list[tuple[str, dict]] = []

    def online_client_ids(self) -> list[str]:
        return ["ext_test"]

    def summaries(self) -> list[dict]:
        return [{"client_id": "ext_test"}]

    async def send(self, client_id: str, message: dict) -> None:
        self.sent.append((client_id, dict(message)))


def build_runtime(tmp: str) -> tuple[FastAPI, FakeRegistry, RequestBroker]:
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


def test_v57_owns_final_admission_after_account_free_safety_patch() -> None:
    hook = read("app/server_worker_sync_lifespan_patch.py")
    source = read("app/capacity_queue_v57_patch.py")
    assert "install_capacity_queue_v57_patch" in hook
    assert 'PATCH_ID = "worker-key-capacity-queue-v57"' in source
    assert 'broker.create = create_fifo' in source
    assert 'broker.release = release_fifo' in source
    assert 'capacity_queue_mode": "fifo-unbounded-v57"' in source
    assert 'capacity_queue_scheduler": "oldest-eligible-cross-key-v57"' in source
    assert 'FREE_ACCOUNT_GENERATION_LIMIT' not in source
    assert 'account_generation_queue_wait_seconds": None' in source


def test_worker_window_settings_are_independent_and_default_to_three() -> None:
    source = read("app/capacity_queue_v57_patch.py")
    admin = read("app/admin_v21_5.js")
    for token in (
        "DEFAULT_WORKER_CONCURRENCY = 3",
        "DEFAULT_RESERVE_WINDOWS = 3",
        '"max_concurrency"',
        '"reserve_windows"',
        '/api/admin/extensions/{client_id}/capacity-v57',
        'routing["worker_limit"] = max(',
    ):
        assert token in source
    for token in (
        'th.dataset.chat2apiColumnKey = "worker_settings"',
        'th.textContent = "并发设置"',
        'data-chat2api-structural-owner="worker-settings-v59"',
        'data-worker-max',
        'data-worker-reserve',
        '最大并发',
        '空闲备用窗口',
    ):
        assert token in admin
    assert 'platformHeader.textContent = "Worker 窗口"' not in admin


def test_per_api_key_concurrency_defaults_to_three_and_queues() -> None:
    source = read("app/capacity_queue_v57_patch.py")
    admin = read("app/admin_v21_5.js")
    for token in (
        "DEFAULT_KEY_CONCURRENCY = 3",
        "key_queues: dict[str, deque[str]]",
        "key_active: dict[str, int]",
        "first_eligible_worker_request",
        '/api/admin/keys/{key_id}/concurrency-v57',
        '"api_key_capacity_limit"',
    ):
        assert token in source
    assert 'th.textContent = "最大并发"' in admin
    assert 'data-key-max' in admin
    assert '超过部分将排队依次执行' in admin


def test_fifo_allows_other_key_to_use_free_worker_slot_without_head_of_line_starvation() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, registry, broker = build_runtime(tmp)
            config = app.state.capacity_queue_v57_config
            config["workers"]["ext_test"] = {
                "max_concurrency": 2,
                "reserve_windows": 3,
            }
            config["keys"]["key_a"] = 1
            config["keys"]["key_b"] = 1

            token = registry.routing_key_context.set("key_a")
            first = await broker.create("req_a1", "ext_test")
            registry.routing_key_context.reset(token)

            token = registry.routing_key_context.set("key_a")
            blocked_same_key = asyncio.create_task(
                broker.create("req_a2", "ext_test")
            )
            registry.routing_key_context.reset(token)
            await asyncio.sleep(0.02)
            assert not blocked_same_key.done()

            token = registry.routing_key_context.set("key_b")
            other_key = await asyncio.wait_for(
                broker.create("req_b1", "ext_test"), timeout=0.5
            )
            registry.routing_key_context.reset(token)
            assert other_key.diagnostics["api_key_capacity_id"] == "key_b"
            assert broker.capacity_snapshot("ext_test")["active_requests"] == 2
            assert not blocked_same_key.done()

            await broker.release(first.request_id)
            admitted = await asyncio.wait_for(blocked_same_key, timeout=0.5)
            assert admitted.diagnostics["api_key_capacity_id"] == "key_a"

            await broker.release(other_key.request_id)
            await broker.release(admitted.request_id)

    asyncio.run(scenario())


def test_master_key_uses_configurable_master_bucket_instead_of_anonymous() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, _registry, broker = build_runtime(tmp)
            app.state.capacity_queue_v57_config["keys"]["master"] = 1
            state = await broker.create("req_master1", "ext_test")
            assert state.diagnostics["api_key_capacity_id"] == "master"
            assert state.diagnostics["api_key_capacity_limit"] == 1
            await broker.release(state.request_id)

    asyncio.run(scenario())


def test_rate_limit_guard_becomes_immediate_terminal_error_and_admission_cooldown() -> None:
    source = read("app/capacity_queue_v57_patch.py")
    content = read("chrome_extension/content_rate_limit_guard_v52.js")
    assert 'broker.publish = publish_rate_aware' in source
    assert '"chatgpt is temporarily rate limited"' in source
    assert '"too many requests"' in source
    assert 'RATE_LIMIT_DEFAULT_SECONDS = 300' in source
    assert 'rate_limit_cooldown_active' in source
    for token in (
        "terminateActiveRequest",
        "__CHAT2API_REQUEST_CONTENT_V5__?.active",
        'type: "chat.error"',
        "active.cancelled = true",
        "ChatGPT is temporarily rate limited",
    ):
        assert token in content


def test_release_contract_is_v02256_and_worker_bundle_0826() -> None:
    runtime = read("app/runtime_contract.py")
    manifest = read("chrome_extension/manifest.json")
    marker = read("chrome_extension/content_bundle_marker_v48.js")
    preflight = read("chrome_extension/background_runtime_preflight_v48.js")
    contract = read("chrome_extension/content_runtime_contract_v48.js")
    assert 'SERVER_RUNTIME_VERSION = "0.22.60"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.28"' in runtime
    assert '"version": "0.8.28"' in manifest
    assert 'bundle: "0.8.28"' in marker
    assert 'REQUIRED_BUNDLE = "0.8.28"' in preflight
    assert 'REQUIRED_BUNDLE = "0.8.28"' in contract
    assert '"worker_key_capacity_fifo_queue": True' in runtime
    assert '"active_rate_limit_terminal_error": True' in runtime
    assert '"routed_dispatch_terminal_error": True' in runtime
    assert '"admin_single_render_owner": True' in runtime
    assert '"linux_worker_disable_authority": True' in runtime
    assert '"network_response_parser_v62": True' in runtime
    assert '"multimodal_main_world_v78": True' in runtime
    assert '"model_capability_routing_v2": True' in runtime


def test_admin_capacity_and_rate_limit_javascript_parse() -> None:
    for path in (
        "app/admin_v21_5.js",
        "chrome_extension/content_rate_limit_guard_v52.js",
        "chrome_extension/background_runtime_preflight_v48.js",
        "chrome_extension/content_runtime_contract_v48.js",
    ):
        result = subprocess.run(
            ["node", "--check", str(ROOT / path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{path}: {result.stderr}"
