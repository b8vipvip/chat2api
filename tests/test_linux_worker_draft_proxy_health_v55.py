from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_managed_draft_overlay_restores_v42_authoritative_worker_cleanup() -> None:
    source = read("chrome_extension/content_draft_managed_recovery_v55.js")
    for token in (
        '__CHAT2API_DRAFT_OWNERSHIP_V43__',
        '__CHAT2API_REQUEST_HYGIENE_V42__',
        'ownership.matchingRecord(current)',
        'chrome.runtime.onMessage.removeListener(previous)',
        'call(managedListener, message, sender, sendResponse)',
        'draft-managed-recovery-v55',
    ):
        assert token in source
    assert 'setComposerText' not in source
    assert 'replaceChildren' not in source


def test_managed_draft_overlay_delegates_unknown_worker_draft_to_v42_in_vm() -> None:
    script = r'''
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const source = fs.readFileSync("chrome_extension/content_draft_managed_recovery_v55.js", "utf8");
let added = null;
let removed = null;
let managedCalls = 0;
let previousCalls = 0;
const composer = {
  value: "stale automation draft",
  getBoundingClientRect: () => ({width: 100, height: 30}),
};
const form = {
  getBoundingClientRect: () => ({width: 200, height: 50}),
  querySelector: () => composer,
  querySelectorAll: () => [composer],
};
const context = {
  console,
  String,
  Promise,
  document: {
    querySelectorAll: selector => selector.startsWith("form") ? [form] : [composer],
  },
  getComputedStyle: () => ({display:"block", visibility:"visible"}),
  chrome: {runtime: {onMessage: {
    removeListener: fn => { removed = fn; },
    addListener: fn => { added = fn; },
  }}},
  __CHAT2API_DRAFT_OWNERSHIP_V43__: {
    version: 43,
    listener: () => { previousCalls += 1; return true; },
    matchingRecord: async () => null,
  },
  __CHAT2API_REQUEST_HYGIENE_V42__: {
    listener: (message, sender, sendResponse) => {
      managedCalls += 1;
      sendResponse({ok:true,data:{stale_draft_recovered:true}});
      return true;
    },
  },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, {filename:"content_draft_managed_recovery_v55.js"});
assert.equal(removed, context.__CHAT2API_DRAFT_OWNERSHIP_V43__.listener);
assert.equal(typeof added, "function");
let response = null;
assert.equal(added({type:"chat2api.request.preflight",requestId:"req1"},{},value => {response=value;}), true);
await new Promise(resolve => setTimeout(resolve, 0));
assert.equal(managedCalls, 1);
assert.equal(previousCalls, 0);
assert.equal(response?.ok, true);
'''
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_bundle_and_preflight_require_managed_draft_overlay() -> None:
    manifest = json.loads(read("chrome_extension/manifest.json"))
    scripts = manifest["content_scripts"][1]["js"]
    assert scripts.index("content_request_hygiene_v42.js") < scripts.index("content_draft_ownership_v43.js") < scripts.index("content_draft_managed_recovery_v55.js")
    assert scripts.index("content_draft_managed_recovery_v55.js") < scripts.index("content_response_capture_v41.js")

    bootstrap = read("chrome_extension/content_bootstrap.js")
    preflight = read("chrome_extension/background_runtime_preflight_v48.js")
    contract = read("chrome_extension/content_runtime_contract_v48.js")
    assert '"content_draft_managed_recovery_v55.js"' in bootstrap
    assert '"content_draft_managed_recovery_v55.js"' in preflight
    assert "draft_managed_recovery_v55" in contract


def test_generation_probe_reports_network_gpt_and_latency_without_using_telemetry_gate() -> None:
    source = read("scripts/linux_worker_generation_probe.sh")
    for token in (
        '"network_access|GET|${NETWORK_URL}"',
        'https://ipwho.is/',
        '"chatgpt_home|GET|https://chatgpt.com/"',
        '"conversation_route|POST|https://chatgpt.com/backend-api/f/conversation"',
        '"sentinel_route|POST|https://chatgpt.com/backend-api/sentinel/chat-requirements"',
        'proxy_network_ready=',
        'proxy_chatgpt_ready=',
        'proxy_latency_ms=',
        'proxy_checked_at_epoch=',
        'generation_backend_ready=true',
    ):
        assert token in source
    assert 'probe=network_access' not in source  # emitted dynamically from PROBES, not hard-coded response data
    assert 'bzr.openai.com as a text-generation health gate' in source


def test_linux_worker_console_shows_four_proxy_health_facets_and_auto_tests() -> None:
    source = read("app/admin_linux_worker_chinese_progress.js")
    for token in (
        'pill("已配置","good")',
        '"网络正常"',
        '"GPT正常"',
        '`延迟 ${health.latencyMs} ms`',
        'command:"test_proxy"',
        '/api/admin/linux-workers/${encodeURIComponent(workerId)}/commands',
        '"network_access"',
        '"conversation_route"',
        '"sentinel_route"',
        'HEALTH_TTL_MS = 60000',
    ):
        assert token in source
    assert "已连接（" not in source


def test_new_browser_and_admin_assets_parse() -> None:
    for filename in (
        "chrome_extension/content_draft_managed_recovery_v55.js",
        "chrome_extension/content_bootstrap.js",
        "chrome_extension/background_runtime_preflight_v48.js",
        "chrome_extension/content_runtime_contract_v48.js",
        "app/admin_linux_worker_chinese_progress.js",
    ):
        result = subprocess.run(
            ["node", "--check", str(ROOT / filename)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{filename}: {result.stderr}"
