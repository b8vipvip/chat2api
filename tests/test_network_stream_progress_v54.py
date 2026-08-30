from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_manifest_installs_main_world_stream_tap_before_isolated_progress_bridge() -> None:
    manifest = json.loads(read("chrome_extension/manifest.json"))
    main = manifest["content_scripts"][0]
    isolated = manifest["content_scripts"][1]["js"]
    assert main["world"] == "MAIN"
    assert main["run_at"] == "document_start"
    assert "network_stream_main_v54.js" in main["js"]
    assert isolated.index("content_response_stream_recovery_v49.js") < isolated.index("content_network_stream_progress_v54.js")
    assert isolated.index("content_network_stream_progress_v54.js") < isolated.index("content_response_semantic_recovery_v51.js")


def test_main_world_tap_observes_only_conversation_sse_without_exposing_payload() -> None:
    source = read("chrome_extension/network_stream_main_v54.js")
    for token in (
        'meta.url.pathname === "/backend-api/f/conversation"',
        'meta.method !== "POST"',
        'contentType.includes("text/event-stream")',
        "response.clone()",
        "clone.body?.getReader",
        'phase: "chunk"',
        'phase: "done"',
        'data-chat2api-network-stream-main-v54',
    ):
        assert token in source
    for forbidden in ("Authorization", "Cookie", "request_body", "response_text", "payloadData"):
        assert forbidden not in source


def test_isolated_bridge_turns_real_sse_bytes_into_generation_activity_without_owning_terminal_error() -> None:
    source = read("chrome_extension/content_network_stream_progress_v54.js")
    for token in (
        'RECOVERY_KEY = "__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__"',
        "ctx.lastMeaningfulProgressAt = now",
        'network_stream_observer: "conversation-fetch-v54"',
        "diagnostics.generation_progress",
        "network_stream_controller_detached",
        "requestIdentity",
    ):
        assert token in source
    assert 'type: "chat.error"' not in source


def test_stream_progress_bridge_updates_detached_response_owner_in_vm() -> None:
    script = r'''
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const source = fs.readFileSync("chrome_extension/content_network_stream_progress_v54.js", "utf8");
let now = 1_000_000;
const sent = [];
let listener = null;
const windowObject = { addEventListener: (type, fn) => { if (type === "message") listener = fn; } };
const context = {
  console,
  Math,
  String,
  Number,
  Boolean,
  Promise,
  Date: { now: () => now },
  window: windowObject,
  chrome: { runtime: { sendMessage: async message => { sent.push(message); return {ok:true}; } } },
  __CHAT2API_REQUEST_CONTENT_V5__: { active: { requestId: "req_stream", cancelled: false } },
  __CHAT2API_RESPONSE_STREAM_RECOVERY_V49__: {
    request: {
      requestId: "req_stream",
      generationSeenAt: 0,
      lastMeaningfulProgressAt: 100,
      completed: false,
      failed: false,
    },
  },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, {filename:"content_network_stream_progress_v54.js"});
const bridge = context.__CHAT2API_NETWORK_STREAM_PROGRESS_V54__;
assert.ok(bridge?.contract);

await bridge.contract.handle({phase:"response", stream_id:"s1", status:200, event_stream:true});
now += 1_100;
await bridge.contract.handle({phase:"chunk", stream_id:"s1", sequence:1, total_bytes:512, chunk_bytes:512});
assert.equal(context.__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__.request.lastMeaningfulProgressAt, now);
assert.ok(sent.some(item => item?.event?.diagnostics?.generation_progress));

context.__CHAT2API_REQUEST_CONTENT_V5__.active = null;
now += 1_100;
await bridge.contract.handle({phase:"chunk", stream_id:"s1", sequence:2, total_bytes:1024, chunk_bytes:512});
assert.equal(context.__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__.request.lastMeaningfulProgressAt, now);
assert.ok(sent.some(item => item?.event?.diagnostics?.network_stream_controller_detached === true));

const beforeError = context.__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__.request.lastMeaningfulProgressAt;
now += 1_100;
await bridge.contract.handle({phase:"error", stream_id:"s1", total_bytes:1024});
assert.equal(context.__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__.request.lastMeaningfulProgressAt, beforeError);
const errorEvent = sent.at(-1)?.event;
assert.equal(errorEvent?.diagnostics?.network_stream_phase, "error");
assert.equal(errorEvent?.diagnostics?.generation_progress, undefined);
assert.ok(listener);
'''
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_preflight_requires_both_main_and_isolated_stream_modules() -> None:
    bootstrap = read("chrome_extension/content_bootstrap.js")
    preflight = read("chrome_extension/background_runtime_preflight_v48.js")
    contract = read("chrome_extension/content_runtime_contract_v48.js")
    assert 'world: "MAIN"' in bootstrap
    assert '"network_stream_main_v54.js"' in bootstrap
    assert '"content_network_stream_progress_v54.js"' in bootstrap
    assert 'const MAIN_FILES = ["network_stream_main_v54.js"]' in preflight
    assert 'world: "MAIN"' in preflight
    assert '"content_network_stream_progress_v54.js"' in preflight
    assert "network_stream_progress_v54" in contract
    assert "network_stream_main_v54" in contract
    assert "data-chat2api-network-stream-main-v54" in contract


def test_linux_worker_bundle_actually_packages_generation_probe() -> None:
    dockerfile = read("Dockerfile")
    dockerignore = read(".dockerignore")
    assert "scripts/linux_worker_generation_probe.sh" in dockerfile
    assert "/app/worker_payload/scripts/linux_worker_generation_probe.sh" in dockerfile
    assert "!scripts/linux_worker_generation_probe.sh" in dockerignore
