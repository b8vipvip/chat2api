from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_manifest_installs_v55_main_world_stream_recovery_without_v54_double_wrap() -> None:
    manifest = json.loads(read("chrome_extension/manifest.json"))
    main = manifest["content_scripts"][0]
    isolated = manifest["content_scripts"][1]["js"]
    assert manifest["version"] == "0.8.22"
    assert main["world"] == "MAIN"
    assert main["run_at"] == "document_start"
    assert "network_stream_main_v55.js" in main["js"]
    assert "network_stream_main_v54.js" not in main["js"]
    assert isolated.index("content_response_stream_recovery_v49.js") < isolated.index("content_network_stream_recovery_v55.js")
    assert "content_network_stream_progress_v54.js" not in isolated


def test_main_world_v55_uses_parser_revision_62_for_conversation_sse() -> None:
    source = read("chrome_extension/network_stream_main_v55.js")
    for token in ('url.pathname === "/backend-api/f/conversation"','const PARSER_REVISION = 62;','data-chat2api-network-stream-parser','type.includes("text/event-stream")',"response.clone()","clone.body?.getReader",'phase: "assistant-snapshot"','phase: "assistant-complete"','type === "message_stream_complete"','patch?.p !== undefined ? patch.p : patch?.path','if (pointer == null || pointer === "") return [];','if (typeof payload === "string") return;'):
        assert token in source
    for forbidden in ("Authorization", "Cookie", "request_body", "prompt_text"):
        assert forbidden not in source


def test_main_world_v55_reconstructs_real_root_patch_protocol_without_dom_in_vm() -> None:
    script = r'''
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
import {TextDecoder, TextEncoder} from "node:util";

const source = fs.readFileSync("chrome_extension/network_stream_main_v55.js", "utf8");
const messages = [];
const attributes = new Map();
const encoder = new TextEncoder();
const chunks = [
  encoder.encode('data: "v1"\n\n'),
  encoder.encode('data: {"p":"","o":"add","v":{"message":{"author":{"role":"assistant"},"content":{"content_type":"text","parts":["Hel"]},"status":"in_progress"}},"c":2}\n\n'),
  encoder.encode('data: {"p":"/message/content/parts/0","o":"append","v":"lo"}\n\n'),
  encoder.encode('data: {"p":"/message/status","o":"replace","v":"finished_successfully"}\n\n'),
  encoder.encode('data: {"type":"message_stream_complete"}\n\n'),
  encoder.encode('data: [DONE]\n\n'),
];
let cursor = 0;
const reader = { async read() { if (cursor >= chunks.length) return {done:true, value:undefined}; return {done:false, value:chunks[cursor++]}; }, releaseLock() {} };
const response = { ok: true, status: 200, body: {}, headers: {get: name => name.toLowerCase() === "content-type" ? "text/event-stream" : ""}, clone: () => ({body: {getReader: () => reader}}) };
const documentElement = { setAttribute(name, value) { attributes.set(name, String(value)); }, getAttribute(name) { return attributes.get(name) ?? null; } };
const context = { console, Math, String, Number, Boolean, Promise, Object, Array, Date, URL, TextDecoder, structuredClone, setTimeout, clearTimeout, location: {href:"https://chatgpt.com/", origin:"https://chatgpt.com"}, document: {documentElement}, postMessage: message => messages.push(message), fetch: async () => response };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, {filename:"network_stream_main_v55.js"});
await context.fetch("https://chatgpt.com/backend-api/f/conversation", {method:"POST"});
for (let i = 0; i < 16; i++) await new Promise(resolve => setTimeout(resolve, 0));
const snapshots = messages.filter(item => item?.phase === "assistant-snapshot");
const completed = messages.find(item => item?.phase === "assistant-complete");
const done = messages.find(item => item?.phase === "done");
assert.equal(attributes.get("data-chat2api-network-stream-parser"), "62");
assert.equal(snapshots.at(-1)?.text, "Hello");
assert.equal(snapshots.at(-1)?.parser_source, "json-patch");
assert.equal(snapshots.at(-1)?.parser_revision, 62);
assert.equal(completed?.text, "Hello");
assert.equal(completed?.completion_hint, true);
assert.equal(completed?.parser_revision, 62);
assert.equal(done?.assistant_chars, 5);
assert.equal(done?.completion_hint, true);
'''
    result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_isolated_v55_recovery_owns_success_and_stops_legacy_dom_watchdogs() -> None:
    source = read("chrome_extension/content_network_stream_recovery_v55.js")
    for token in ('RESPONSE_KEY = "__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__"','network_response_recovery: "sse-assistant-v55"','type: "chat.snapshot"','type: "chat.completed"',"active.networkCompleted = true","active.cancelled = true","sealResponseOwner(row.requestId, text)","setTimeout(() => sealResponseOwner(row.requestId, text), 300)","network_stream_controller_detached"):
        assert token in source
    assert 'type: "chat.error"' not in source


def test_runtime_preflight_requires_v55_parser_62_main_and_isolated_recovery_modules() -> None:
    bootstrap = read("chrome_extension/content_bootstrap.js")
    preflight = read("chrome_extension/background_runtime_preflight_v48.js")
    contract = read("chrome_extension/content_runtime_contract_v48.js")
    marker = read("chrome_extension/content_bundle_marker_v48.js")
    assert 'world: "MAIN"' in bootstrap
    assert '"network_stream_main_v55.js"' in bootstrap
    assert '"content_network_stream_recovery_v55.js"' in bootstrap
    assert 'const REQUIRED_BUNDLE = "0.8.22"' in preflight
    assert 'const MAIN_FILES = ["network_stream_main_v55.js", "multimodal_main_v78.js"]' in preflight
    assert '"content_network_stream_recovery_v55.js"' in preflight
    assert "network_stream_recovery_v55" in contract
    assert "network_stream_main_v55" in contract
    assert "network_stream_parser_v62" in contract
    assert "data-chat2api-network-stream-parser" in contract
    assert 'bundle: "0.8.22"' in marker
    assert "network_stream_main_v54.js" not in preflight
    assert "content_network_stream_progress_v54.js" not in preflight


def test_linux_worker_bundle_actually_packages_generation_probe() -> None:
    dockerfile = read("Dockerfile")
    dockerignore = read(".dockerignore")
    assert "scripts/linux_worker_generation_probe.sh" in dockerfile
    assert "/app/worker_payload/scripts/linux_worker_generation_probe.sh" in dockerfile
    assert "!scripts/linux_worker_generation_probe.sh" in dockerignore
