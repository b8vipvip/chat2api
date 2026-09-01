from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "chrome_extension"


def test_free_account_limit_phrase_is_owned_by_rate_limit_guard() -> None:
    script = r'''
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const source = fs.readFileSync("chrome_extension/content_rate_limit_guard_v52.js", "utf8");
const intervalCallbacks = [];
const context = {
  console,
  Promise,
  Date,
  location: {href: "https://chatgpt.com/"},
  document: {
    title: "ChatGPT",
    documentElement: {},
    querySelectorAll: () => [],
  },
  getComputedStyle: () => ({display: "block", visibility: "visible", opacity: "1"}),
  MutationObserver: class { observe() {} },
  setInterval: callback => { intervalCallbacks.push(callback); return 1; },
  setTimeout: () => 1,
  clearTimeout: () => {},
  chrome: {
    storage: {
      local: {
        get: async () => ({}),
        set: async () => {},
      },
    },
    runtime: {sendMessage: async () => ({ok: true})},
  },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, {filename: "content_rate_limit_guard_v52.js"});

const guard = context.__CHAT2API_RATE_LIMIT_CONTENT_V52__;
assert.equal(guard.detection_revision, 63);
assert.equal(guard.matches("You've hit your limit. Please try again later."), true);
assert.equal(guard.matches("You have hit your current limit. Please try again later."), true);
assert.equal(guard.matches("You've reached your message limit. Try again later."), true);
assert.equal(guard.matches("你已达到使用上限，请稍后再试。"), true);
assert.equal(guard.matches("Please try again later."), false, "generic retry wording alone is not an account limit");
assert.equal(guard.matchesHard("You've hit your limit. Please try again later."), true);
assert.equal(intervalCallbacks.length, 1);
'''
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_assistant_shell_limit_fallback_is_active_request_only() -> None:
    source = (EXT / "content_rate_limit_guard_v52.js").read_text(encoding="utf-8")
    assert "latestAssistantLimitText" in source
    assert "if (!String(activeRequest()?.requestId || \"\")) return \"\";" in source
    assert "matchesHardLimitText(text)" in source
    assert "characterData: true" in source
    assert "CHECK_MS = 180" in source
    assert "visible-chatgpt-rate-limit-surface-v63" in source


def test_rate_limit_terminal_keeps_server_cooldown_contract() -> None:
    content = (EXT / "content_rate_limit_guard_v52.js").read_text(encoding="utf-8")
    capacity = (ROOT / "app" / "capacity_queue_v57_patch.py").read_text(encoding="utf-8")
    assert "ChatGPT is temporarily rate limited; request dispatch paused for" in content
    assert "chatgpt_rate_limit_detected: true" in content
    assert '"chatgpt is temporarily rate limited"' in capacity
    assert "cooldown_until[state.client_id]" in capacity
