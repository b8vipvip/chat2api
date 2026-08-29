from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

from app.config import Settings
from app.main import create_app
from app.v21_1_patch import install_v21_1_patch
from app.v21_patch import install_v21_patch
from app.v21_routing_patch import install_v21_routing_patch


ROOT = Path(__file__).resolve().parents[1]


class _Socket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, **_kwargs) -> None:
        return None


def _settings(data_dir: Path) -> Settings:
    return Settings(
        CHAT2API_API_KEY="test-master-key",
        CHAT2API_PAIRING_CODE="test-pair-code",
        CHAT2API_ADMIN_USERNAME="admin",
        CHAT2API_ADMIN_PASSWORD="strong-password-for-worker-limit-test",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=data_dir,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=30,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )


def test_server_dispatch_carries_same_per_extension_worker_limit_as_admission() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(_settings(Path(tmp)))
            install_v21_patch(app)
            install_v21_routing_patch(app)
            install_v21_1_patch(app)

            registry = app.state.registry
            client_id, _token = await registry.register("A", "Chrome", "0.8.7", {})
            app.state.concurrency_config["client_limits"][client_id] = 5
            socket = _Socket()
            await registry.attach(client_id, socket)

            state = await app.state.broker.create("req_alignment", client_id)
            assert state.diagnostics["extension_capacity_limit_units"] == 5

            await registry.send(
                client_id,
                {
                    "type": "chat.request",
                    "request_id": state.request_id,
                    "routing": {"api_key_id": "key_alignment"},
                },
            )
            routed = socket.sent[-1]
            assert routed["routing"]["worker_limit"] == 5
            assert routed["routing"]["concurrency_mode"] == "per-extension"
            assert routed["routing"]["api_key_id"] == "key_alignment"
            await app.state.broker.release(state.request_id)

    asyncio.run(scenario())


def test_extension_worker_router_uses_runtime_limit_and_reserves_identical_parallel_requests() -> None:
    script = r'''
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const source = fs.readFileSync("chrome_extension/conversation_workers_v25.js", "utf8");
const diagnostics = [];
const context = {
  console,
  Map,
  Set,
  Date,
  Number,
  Promise,
  setTimeout,
  clearTimeout,
  resolveTargetTabForRequest: async message => {
    // Yield once so two simultaneous callers overlap while route allocation is
    // still being resolved. v25 must already have reserved distinct routes.
    await Promise.resolve();
    const index = Number(message?.routing?.worker_index || 1);
    return { id: 100 + index, windowId: 200 + index };
  },
  trySendSocket: async payload => { diagnostics.push(payload); return true; },
  chrome: { runtime: { onMessage: { addListener() {} } } },
  __CHAT2API_CONVERSATION_ROUTING_V1__: { routes: {}, activeRequests: new Map() },
  __CHAT2API_RESERVE_POOL_V29__: { target: 5, configRefreshedAt: Date.now() },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: "conversation_workers_v25.js" });

const runtimeFallback = {
  type: "chat.request",
  request_id: "req_runtime_fallback",
  routing: { api_key_id: "key_a" },
};
await context.resolveTargetTabForRequest(runtimeFallback);
assert.equal(runtimeFallback.routing.worker_limit, 5);
assert.equal(diagnostics.at(-1).diagnostics.extension_worker_limit, 5);
assert.equal(diagnostics.at(-1).diagnostics.extension_worker_limit_source, "runtime-config");
assert.equal(diagnostics.at(-1).diagnostics.extension_worker_request_reservation, true);

const serverRouted = {
  type: "chat.request",
  request_id: "req_server_limit",
  routing: { api_key_id: "key_b", worker_limit: 7 },
};
await context.resolveTargetTabForRequest(serverRouted);
assert.equal(serverRouted.routing.worker_limit, 7);
assert.equal(diagnostics.at(-1).diagnostics.extension_worker_limit_source, "server-routing");

context.__CHAT2API_RESERVE_POOL_V29__.configRefreshedAt = 0;
const cached = {
  type: "chat.request",
  request_id: "req_cached_limit",
  routing: { api_key_id: "key_c" },
};
await context.resolveTargetTabForRequest(cached);
assert.equal(cached.routing.worker_limit, 7);
assert.equal(diagnostics.at(-1).diagnostics.extension_worker_limit_source, "cached-server-routing");

// Same API key + byte-identical prompt + simultaneous arrival must remain two
// independent executions. Prompt equality is intentionally irrelevant; request_id
// owns identity and each in-flight call reserves a different conversation worker.
const first = {
  type: "chat.request",
  request_id: "req_same_1",
  prompt: "identical prompt",
  routing: { api_key_id: "key_same", worker_limit: 2 },
};
const second = {
  type: "chat.request",
  request_id: "req_same_2",
  prompt: "identical prompt",
  routing: { api_key_id: "key_same", worker_limit: 2 },
};
const [firstTab, secondTab] = await Promise.all([
  context.resolveTargetTabForRequest(first),
  context.resolveTargetTabForRequest(second),
]);
assert.notEqual(first.request_id, second.request_id);
assert.equal(first.routing.logical_api_key_id, "key_same");
assert.equal(second.routing.logical_api_key_id, "key_same");
assert.deepEqual(new Set([first.routing.worker_index, second.routing.worker_index]), new Set([1, 2]));
assert.notEqual(first.routing.api_key_id, second.routing.api_key_id);
assert.notEqual(firstTab.id, secondTab.id);

const workers = context.__CHAT2API_CONVERSATION_WORKERS_V25__;
assert.equal(workers.requestRoutes.size >= 2, true);
assert.equal(workers.routeReservations.get("key_same")?.requestId, "req_same_1");
assert.equal(workers.routeReservations.get("key_same::worker2")?.requestId, "req_same_2");
workers.releaseRequest("req_same_1");
assert.equal(workers.routeReservations.has("key_same"), false);
assert.equal(workers.routeReservations.get("key_same::worker2")?.requestId, "req_same_2");
'''
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
