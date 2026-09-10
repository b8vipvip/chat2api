from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.responses_tool_stream_v118_patch import PATCH_REVISION, _normalize_nested_custom_call_v118
from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload
from fastapi import FastAPI


ROOT = Path(__file__).resolve().parents[1]


def _exec_catalog() -> list[dict[str, object]]:
    return [{"type": "custom", "namespace": "functions", "name": "exec"}]


def test_v118_accepts_exact_namespace_qualified_nested_exec_identity() -> None:
    call = {
        "namespace": "functions",
        "name": "exec",
        "arguments": {
            "name": "functions.exec",
            "input": "const r = await tools.collaboration__spawn_agent({task_name:'x',message:'ok'}); text(r);",
        },
    }
    normalized = _normalize_nested_custom_call_v118(call, _exec_catalog())
    assert normalized["namespace"] == "functions"
    assert normalized["name"] == "exec"
    assert "arguments" not in normalized
    assert normalized["input"].startswith("const r = await tools.collaboration__spawn_agent")
    assert PATCH_REVISION == 118


def test_v118_still_rejects_a_different_nested_custom_tool() -> None:
    call = {
        "namespace": "functions",
        "name": "exec",
        "arguments": {"name": "functions.other", "input": "text('bad');"},
    }
    with pytest.raises(ValueError, match="does not match outer tool"):
        _normalize_nested_custom_call_v118(call, _exec_catalog())


def test_worker_bundle_and_runtime_are_new_release_versions() -> None:
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    assert SERVER_RUNTIME_VERSION == "0.22.73"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.29"
    assert manifest["version"] == "0.8.29"
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert payload["features"]["worker_single_route_v28"] is True
    assert payload["features"]["worker_strict_api_fifo_v29"] is True
    assert payload["features"]["responses_tool_stream_v118"] is True
    assert payload["features"]["same_api_parallel_requests"] is False
    assert "release-v02273" in payload["server"]["feature_revision"]


def test_background_entry_installs_single_worker_then_terminal_fifo() -> None:
    source = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    assert source.index('"conversation_workers_v27.js"') < source.index('"conversation_workers_v28.js"')
    assert source.index('"conversation_workers_v28.js"') < source.index('"conversation_dispatch.js"')
    assert source.index('"conversation_dispatch.js"') < source.index('"conversation_dispatch_v29.js"')


def test_worker_v28_hard_caps_same_logical_key_to_worker_one() -> None:
    source = (ROOT / "chrome_extension" / "conversation_workers_v28.js").read_text(encoding="utf-8")
    assert "worker_limit: 1" in source
    assert "worker_index: 1" in source
    assert "strict_api_fifo: true" in source
    assert 'replace(/::worker\\d+$/i, "")' in source
    assert "per-logical-api-strict-worker1-v28" in source


def test_dispatch_v29_is_terminal_aware_and_per_key_not_global() -> None:
    source = (ROOT / "chrome_extension" / "conversation_dispatch_v29.js").read_text(encoding="utf-8")
    assert "lanes: new Map()" in source
    assert "entries: new Map()" in source
    assert "entry.terminal.promise" in source
    assert "extension_api_fifo" in source
    assert "worker_limit: 1" in source
    assert "strict_api_fifo: true" in source
    assert "state.chain" not in source


def test_dispatch_v29_behaviorally_queues_same_key_until_terminal() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = r'''
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync('chrome_extension/conversation_dispatch_v29.js', 'utf8');
const listeners = [];
const dispatched = [];
const baseState = { requestTabs: new Map() };
const context = {
  console,
  setTimeout,
  clearTimeout,
  Promise,
  Map,
  Set,
  globalThis: null,
  trySendSocket: async () => true,
  handleServerMessage: async message => {
    if (String(message.type || '').endsWith('.request') || message.type === 'voice.live.start') {
      dispatched.push({ id: message.request_id, key: message.routing.api_key_id, limit: message.routing.worker_limit });
      baseState.requestTabs.set(message.request_id, { tabId: 1, windowId: 1 });
    }
    return null;
  },
  chrome: { runtime: { onMessage: { addListener(fn) { listeners.push(fn); } } } },
  __CHAT2API_CONVERSATION_DISPATCH_V1__: baseState,
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const emit = (type, id) => {
  baseState.requestTabs.delete(id);
  for (const fn of listeners) fn({ type: 'chat2api.event', event: { type, request_id: id } });
};
(async () => {
  const mk = (id, key) => ({ type: 'chat.request', request_id: id, routing: { api_key_id: key } });
  const p1 = context.handleServerMessage(mk('r1', 'keyA'));
  await sleep(0);
  const p2 = context.handleServerMessage(mk('r2', 'keyA'));
  const p3 = context.handleServerMessage(mk('r3', 'keyB'));
  await sleep(5);
  if (!dispatched.some(x => x.id === 'r1')) throw new Error('r1 did not dispatch');
  if (dispatched.some(x => x.id === 'r2')) throw new Error('same-key r2 dispatched before r1 terminal');
  if (!dispatched.some(x => x.id === 'r3')) throw new Error('different-key r3 was globally blocked');
  if (dispatched.some(x => x.limit !== 1 || x.key.includes('::worker'))) throw new Error('strict worker1 routing was not enforced');
  emit('chat.completed', 'r1');
  await sleep(5);
  if (!dispatched.some(x => x.id === 'r2')) throw new Error('r2 did not dispatch after r1 terminal');
  emit('chat.completed', 'r2');
  emit('chat.completed', 'r3');
  await Promise.all([p1, p2, p3]);
  process.stdout.write('strict-api-fifo-v29 ok\n');
})().catch(error => { console.error(error); process.exit(1); });
'''
    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "strict-api-fifo-v29 ok" in result.stdout
