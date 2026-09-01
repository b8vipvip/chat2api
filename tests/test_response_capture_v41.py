from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_manifest_loads_response_capture_v41_after_request_v5() -> None:
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    bootstrap = (ROOT / "chrome_extension" / "content_bootstrap.js").read_text(encoding="utf-8")
    assert manifest["version"] == "0.8.16"
    scripts = manifest["content_scripts"][1]["js"]
    assert "content_response_capture_v41.js" in scripts
    assert scripts.index("content_request_v5.js") < scripts.index("content_response_capture_v41.js")
    assert scripts.index("content_response_capture_v41.js") < scripts.index("content_response_stream_recovery_v49.js") < scripts.index("content_network_stream_recovery_v55.js") < scripts.index("content_completion_v6.js")
    assert '"content_response_capture_v41.js"' in bootstrap
    assert bootstrap.index('"content_request_v5.js"') < bootstrap.index('"content_response_capture_v41.js"')
    assert bootstrap.index('"content_response_capture_v41.js"') < bootstrap.index('"content_response_stream_recovery_v49.js"') < bootstrap.index('"content_network_stream_recovery_v55.js"') < bootstrap.index('"content_completion_v6.js"')


def test_response_capture_v41_classification_contract_executes_in_vm() -> None:
    script = r'''
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const source = fs.readFileSync("chrome_extension/content_response_capture_v41.js", "utf8");
const context = {
  console,
  Set,
  Date,
  Promise,
  setTimeout: () => 1,
  clearTimeout() {},
  MutationObserver: class MutationObserver { observe() {} },
  document: {
    documentElement: {},
    querySelectorAll: () => [],
  },
  getComputedStyle: () => ({ display: "", visibility: "" }),
  chrome: { runtime: { sendMessage: async () => ({ ok: true }) } },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: "content_response_capture_v41.js" });

const contract = context.__CHAT2API_RESPONSE_CAPTURE_V41__?.contract;
assert.ok(contract, "v41 capture contract should be exposed");
assert.equal(contract.classifyTurn({ user: true, hasTextHost: true, hasFinalActions: true }), "");
assert.equal(contract.classifyTurn({ hasAssistantRole: true, roleVisible: false, hasTextHost: true }), "role-proxy");
assert.equal(contract.classifyTurn({ hasAssistantRole: false, hasFinalActions: true, hasTextHost: true }), "final-actions");
assert.equal(contract.classifyTurn({ hasAssistantRole: true, roleVisible: true, hasFinalActions: true, hasTextHost: true }), "final-actions");
assert.equal(contract.classifyTurn({ hasAssistantRole: false, hasFinalActions: false, hasTextHost: true }), "");
'''
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_response_capture_v41_has_both_recovery_paths() -> None:
    source = (ROOT / "chrome_extension" / "content_response_capture_v41.js").read_text(encoding="utf-8")
    assert "repairInvisibleAssistantRoles" in source
    assert "repairCompletedAssistantTurns" in source
    assert "completionProxyHost" in source
    assert 'data-message-author-role", "assistant"' in source
    assert "Good response" in source
    assert "Bad response" in source
    assert "response_capture_recovery" in source
