from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_file_upload_quota_recycler_loads_after_request_recovery() -> None:
    entry = text("chrome_extension/background_entry.js")
    assert '"background_file_upload_quota_recycle_v96.js"' in entry
    assert entry.index('"background_request_recovery_v40.js"') < entry.index('"background_file_upload_quota_recycle_v96.js"')
    assert entry.index('"background_file_upload_quota_recycle_v96.js"') < entry.index('"background_conversation_quota_failover_v95.js"')


def test_file_upload_quota_recycler_is_terminal_only_and_does_not_replay() -> None:
    source = text("chrome_extension/background_file_upload_quota_recycle_v96.js")
    assert 'MESSAGE_TYPE = "chat2api.multimodal.quota.v36"' in source
    assert '"file-upload-quota-exhausted-v96"' in source
    assert 'file_upload_quota_terminal_recycle_action: "close-routed-window-no-replay"' in source
    assert "recovery.recycleRequest" in source
    assert "workersState()?.releaseRequest?.(item.requestId)" in source
    assert "handleServerMessage(" not in source
    assert "__conversation_quota_failover_replay_v95" not in source


def test_file_upload_quota_recycler_vm_closes_only_the_matching_routed_window() -> None:
    script = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

(async () => {
  const source = fs.readFileSync("chrome_extension/background_file_upload_quota_recycle_v96.js", "utf8");
  const listeners = [];
  const timers = [];
  const diagnostics = [];
  const releases = [];
  const recycles = [];
  const requestId = "req_quota_v96";
  const requestTabs = new Map([[requestId, { tabId: 101, windowId: 202 }]]);
  const routes = {
    key_test: { tab_id: 101, window_id: 202, inflight_request_id: requestId },
  };

  const context = {
    console,
    Date,
    Number,
    String,
    Boolean,
    Object,
    Map,
    setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; },
    chrome: {
      runtime: {
        onMessage: { addListener(fn) { listeners.push(fn); } },
      },
    },
    async trySendSocket(payload) { diagnostics.push(payload); return true; },
    __CHAT2API_CONVERSATION_DISPATCH_V1__: { requestTabs },
    __CHAT2API_CONVERSATION_ROUTING_V1__: { routes },
    __CHAT2API_CONVERSATION_WORKERS_V25__: {
      releaseRequest(id) { releases.push(id); },
    },
    __CHAT2API_BACKGROUND_REQUEST_RECOVERY_V40__: {
      async recycleRequest(id, reason) {
        recycles.push([id, reason]);
        requestTabs.delete(id);
        return true;
      },
    },
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: "background_file_upload_quota_recycle_v96.js" });

  const api = context.__CHAT2API_FILE_UPLOAD_QUOTA_RECYCLE_V96__;
  assert.ok(api);
  assert.equal(api.revision, 96);
  assert.equal(listeners.length, 1);

  const accepted = listeners[0](
    {
      type: "chat2api.multimodal.quota.v36",
      data: {
        detected_at_ms: Date.now(),
        recovery_at_ms: Date.now() + 3600000,
        source_text: "免费版套餐文件上传次数已全部用完",
      },
    },
    { tab: { id: 101, windowId: 202 } },
  );
  assert.equal(accepted, false, "quota response remains owned by the existing v91 controller");
  assert.equal(timers.length, 1);
  assert.equal(timers[0].ms, 900);

  const first = timers.shift();
  await first.fn();
  assert.deepEqual(recycles, [[requestId, "file-upload-quota-exhausted-v96"]]);
  assert.deepEqual(releases, [requestId]);
  assert.equal(api.state.recycled, 1);
  assert.equal(diagnostics.length, 1);
  assert.equal(diagnostics[0].request_id, requestId);
  assert.equal(diagnostics[0].diagnostics.file_upload_quota_terminal_recycled, true);
  assert.equal(diagnostics[0].diagnostics.file_upload_quota_terminal_recycle_action, "close-routed-window-no-replay");

  const timersBeforeMismatch = timers.length;
  listeners[0](
    { type: "chat2api.multimodal.quota.v36", data: { source_text: "quota" } },
    { tab: { id: 999, windowId: 999 } },
  );
  assert.equal(timers.length, timersBeforeMismatch, "an unrelated/manual ChatGPT tab must never be closed");
})();
'''
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_file_upload_quota_recycler_javascript_parses() -> None:
    result = subprocess.run(
        ["node", "--check", str(ROOT / "chrome_extension" / "background_file_upload_quota_recycle_v96.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
