from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_routed_rate_limit_failure_emits_terminal_event_in_vm() -> None:
    script = r'''
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const source = fs.readFileSync("chrome_extension/conversation_dispatch.js", "utf8");
const sent = [];
let baseCalls = 0;
const context = {
  console,
  Promise,
  Map,
  Set,
  handleServerMessage: async () => { baseCalls += 1; },
  resolveTargetTab: async () => ({id: 1, windowId: 1}),
  trySendSocket: async payload => { sent.push(payload); return true; },
  chrome: {
    tabs: { get: async id => ({id, windowId: 9}) },
    storage: { local: { set: async () => {} } },
    runtime: { onMessage: { addListener: () => {} } },
  },
};
context.globalThis = context;
context.resolveTargetTabForRequest = async () => {
  const error = new Error("ChatGPT is temporarily rate limited; Worker window creation and request dispatch are paused for 210s to avoid a reopen loop");
  error.code = "chatgpt_rate_limited";
  error.retry_after_ms = 210000;
  throw error;
};
vm.createContext(context);
vm.runInContext(source, context, {filename:"conversation_dispatch.js"});

await context.handleServerMessage({
  type: "chat.request",
  request_id: "req_rate_limited",
  routing: {api_key_id: "key_test"},
});

assert.equal(baseCalls, 0, "route allocation failed before the base content dispatch");
assert.equal(sent.length, 1, "routed allocation failure must emit exactly one terminal event");
assert.equal(sent[0].type, "chat.error");
assert.equal(sent[0].request_id, "req_rate_limited");
assert.equal(sent[0].retry_after_ms, 210000);
assert.equal(sent[0].diagnostics.routed_dispatch_terminal_v58, true);
assert.equal(sent[0].diagnostics.rate_limit_terminal, true);
assert.equal(sent[0].diagnostics.route_failure_code, "chatgpt_rate_limited");
'''
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_server_cooldown_recognizes_terminal_rate_limit_message() -> None:
    dispatch = (ROOT / "chrome_extension" / "conversation_dispatch.js").read_text(encoding="utf-8")
    capacity = (ROOT / "app" / "capacity_queue_v57_patch.py").read_text(encoding="utf-8")

    assert "publishRoutedDispatchFailure" in dispatch
    assert 'routed_dispatch_terminal_v58: true' in dispatch
    assert 'error?.code === "chatgpt_rate_limited"' in dispatch
    assert "trySendSocket(event)" in dispatch
    assert '"chatgpt is temporarily rate limited"' in capacity
    assert 'event_type in {"chat.error", "chat.cancelled"}' in capacity
    assert "cooldown_until[state.client_id]" in capacity
