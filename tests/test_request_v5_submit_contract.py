from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_request_v5_submission_contract_executes_in_vm() -> None:
    script = r'''
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const source = fs.readFileSync("chrome_extension/content_request_v5.js", "utf8");
const context = {
  console,
  Set,
  Date,
  Promise,
  performance: { now: () => 0 },
  document: { querySelectorAll: () => [] },
  getComputedStyle: () => ({ display: "", visibility: "" }),
  HTMLTextAreaElement: class HTMLTextAreaElement {},
  HTMLInputElement: class HTMLInputElement {},
  Event: class Event {},
  InputEvent: class InputEvent {},
  KeyboardEvent: class KeyboardEvent {},
  chrome: {
    runtime: {
      sendMessage: async () => ({ ok: true }),
      onMessage: { addListener() {}, removeListener() {} },
    },
  },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: "content_request_v5.js" });

const contract = context.__CHAT2API_REQUEST_CONTENT_V5__?.contract;
assert.ok(contract, "request-v5 contract should be exposed");

assert.equal(contract.nextSubmitAction(null, true), "retry");
assert.equal(contract.nextSubmitAction(null, false), "settle");
assert.equal(contract.nextSubmitAction({ reason: "ok" }, false), "confirmed");

assert.equal(
  contract.classifySubmissionState({ promptPresent: true, composerChars: 4212, generating: false, newAssistant: false }),
  null,
);
assert.deepEqual(
  contract.classifySubmissionState({ promptPresent: false, composerChars: 0, generating: false, newAssistant: false }),
  { reason: "composer-cleared", composerCleared: true, generating: false },
);
assert.deepEqual(
  contract.classifySubmissionState({ promptPresent: false, composerChars: 0, generating: true, newAssistant: false }),
  { reason: "generating", composerCleared: true, generating: true },
);
assert.deepEqual(
  contract.classifySubmissionState({ promptPresent: false, composerChars: 10, generating: false, newAssistant: true }),
  { reason: "assistant-turn", composerCleared: false, generating: false },
);
'''
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_late_transition_path_suppresses_duplicate_send() -> None:
    source = (ROOT / "chrome_extension" / "content_request_v5.js").read_text(encoding="utf-8")
    assert 'settleAfterPromptLeftComposer(active, prompt, attempts)' in source
    assert 'submission_retry_suppressed: true' in source
    assert 'await waitAfterSend(active, prompt, "late", 20000)' in source
    assert 'duplicate send was suppressed' in source
