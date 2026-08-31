from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "chrome_extension"


def test_v51_is_a_non_owning_helper_and_cannot_disable_v49():
    v49 = (EXT / "content_response_stream_recovery_v49.js").read_text(encoding="utf-8")
    v51 = (EXT / "content_response_semantic_recovery_v51.js").read_text(encoding="utf-8")

    assert 'owner_revision: 53' in v49
    assert 'owner: "response-stream-v49-single-owner"' in v49
    assert 'const OBSERVER_GRACE_MS = 180000' in v49
    assert 'if (!activeId)' in v49
    assert 'page_progress_probe: "page-progress-v49"' in v49
    assert 'fail-chatgpt-ui-stuck' in v49
    assert 'ROLE_ONLY' in v49 and 'ROLE_PREFIX' in v49

    assert 'mode: "semantic-helper-only"' in v51
    assert 'owner: "response-stream-v49-single-owner-v53"' in v51
    assert 'timer: null' in v51
    assert 'clearInterval' not in v51
    assert 'setInterval' not in v51
    assert 'oldRecovery.timer = null' not in v51


def test_single_observer_semantics_and_detached_controller_watchdog_execute_in_vm():
    script = r'''
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const v49 = fs.readFileSync("chrome_extension/content_response_stream_recovery_v49.js", "utf8");
const v51 = fs.readFileSync("chrome_extension/content_response_semantic_recovery_v51.js", "utf8");
let now = 1_000_000;
const messages = [];
let intervalCalls = 0;
class FakeElement {}
const context = {
  console,
  Promise,
  Map,
  Set,
  Element: FakeElement,
  Date: { now: () => now },
  getComputedStyle: () => ({ display: "block", visibility: "visible" }),
  document: { querySelectorAll: () => [] },
  chrome: { runtime: { sendMessage: async ({event}) => { messages.push(event); return {ok:true}; } } },
  setInterval: () => { intervalCalls += 1; return 900 + intervalCalls; },
  clearInterval: () => { throw new Error("semantic helper must not clear the response owner"); },
};
context.globalThis = context;
context.__CHAT2API_REQUEST_CONTENT_V5__ = { active: { requestId: "req_detached", baselineCount: 0, baselineIds: new Set() } };
context.__CHAT2API_REQUEST_STALL_GUARD_V34__ = { track: { requestId: "req_detached", sawGenerating: true, submittedAt: now } };
vm.createContext(context);
vm.runInContext(v49, context, {filename:"content_response_stream_recovery_v49.js"});
const owner = context.__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__;
assert.ok(owner);
assert.equal(owner.owner_revision, 53);
assert.equal(intervalCalls, 1);
assert.equal(owner.sanitizeAssistantText("ChatGPT said:").text, "");
assert.equal(owner.sanitizeAssistantText("ChatGPT said: 在的，有什么可以帮你？").text, "在的，有什么可以帮你？");
assert.equal(owner.sanitizeAssistantText("正常回答").text, "正常回答");

await owner.tick();
assert.equal(owner.request.requestId, "req_detached");
context.__CHAT2API_REQUEST_CONTENT_V5__.active = null;
now += 16_000;
await owner.tick();
assert.equal(owner.request.requestId, "req_detached", "observer must survive local controller cleanup");
assert.ok(messages.some(event => event?.type === "chat.diagnostics" && event?.diagnostics?.page_progress_probe === "page-progress-v49"));
now += 30_000;
await owner.tick();
assert.ok(messages.some(event => event?.type === "chat.error" && /45s/.test(String(event?.error || ""))), "no-candidate path must fail inside the bounded v49 probe instead of waiting 150s");

const timerBefore = owner.timer;
vm.runInContext(v51, context, {filename:"content_response_semantic_recovery_v51.js"});
const helper = context.__CHAT2API_RESPONSE_SEMANTIC_RECOVERY_V51__;
assert.ok(helper);
assert.equal(helper.timer, null);
assert.equal(owner.timer, timerBefore);
assert.equal(intervalCalls, 1, "v51 must not install a competing observer timer");
assert.equal(helper.sanitize("ChatGPT said:").text, "");
'''
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_contract_requires_worker_0813_single_owner():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    preflight = (EXT / "background_runtime_preflight_v48.js").read_text(encoding="utf-8")
    contract = (EXT / "content_runtime_contract_v48.js").read_text(encoding="utf-8")
    marker = (EXT / "content_bundle_marker_v48.js").read_text(encoding="utf-8")

    assert 'SERVER_RUNTIME_VERSION = "0.22.40"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.13"' in runtime
    assert '"single_response_observer": True' in runtime
    assert 'single-response-owner-v53' in runtime
    assert 'const REQUIRED_BUNDLE = "0.8.13"' in preflight
    assert 'const REQUIRED_BUNDLE = "0.8.13"' in contract
    assert 'response_single_owner_v53' in contract
    assert 'semanticHelper?.timer == null' in contract
    assert 'bundle: "0.8.13"' in marker
