import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

const read=path=>readFileSync(path,"utf8");
const capture=read("chrome_extension/background_native_page_diagnostic_v1.js");
const supervisor=read("chrome_extension/background_tab_supervisor_v32.js");
const pool=read("chrome_extension/conversation_persistent_pool_v132.js");
const observer=read("chrome_extension/background_window_observer_v90.js");
const legacy=read("chrome_extension/background_window_manager_v88.js");
const admin=read("app/admin_worker_tools_v1.js");
const service=read("app/native_page_diagnostic_api.py");
const linuxManager=read("app/admin_linux_device_authority_v124.js");
const linuxPairing=read("app/admin_linux_worker_pairing.js");

const KEY="__CHAT2API_NATIVE_PAGE_DIAG_TAB_IDS_V1__";
assert.ok(capture.includes('globalThis[TAB_LEASE_KEY]=diagnosticTabs'),
  "Temporary diagnosis tabs need a global lifetime lease");
assert.ok(capture.includes("diagnosticTabs.add(tab.id)"));
assert.ok(capture.includes("diagnosticTabs.delete(tab.id)"));
assert.ok(capture.includes('chrome.windows.create({'),
  "Unbound Windows Worker must create an isolated own Chrome window");
assert.ok(capture.includes('url:"about:blank"'),
  "Owned diagnosis tab must be protected before ChatGPT navigation");
assert.ok(capture.indexOf("diagnosticTabs.add(tab.id)") <
  capture.indexOf("await chrome.tabs.update(tab.id,{url:job.conversation_url})"),
  "Lifetime lease must precede navigation to ChatGPT");
assert.ok(capture.includes("worker_busy_visual_diagnosis"),
  "Foreground independent window cannot hide a current API request");
assert.ok(capture.includes("active:showTab"),
  "Background diagnosis remains inactive; foreground is explicit opt-in");
assert.ok(capture.includes("const showTab=job.show_tab===true"));
assert.ok(capture.includes("worker_busy_visual_diagnosis"),
  "Foreground mode cannot hide an active API request");
assert.ok(capture.includes("restore=!busyNow&&activeNow.some"),
  "Do not restore focus over a newly active API request");
assert.ok(capture.includes("chrome.tabs.remove(tab.id)"));
assert.ok(supervisor.includes(KEY)&&
  supervisor.includes("!diagnostic?.has?.(tab.id)")&&
  supervisor.includes("!diagnosis?.has?.(tab?.id)")&&
  supervisor.includes("if (globalThis."+KEY+"?.has?.(tab.id)) continue"),
  "Tab Supervisor must exclude diagnostic tabs from shared tab count, adoption and reclaim");
for(const [name,source] of [["pool",pool],["observer",observer],["manager",legacy]]){
  assert.ok(source.includes(KEY),"Physical "+name+" must exclude diagnostic tabs");
  assert.ok(source.includes("!diagnostic?.has?.("),"Physical "+name+" must check the tab lease");
}
assert.ok(admin.includes("请选择 Worker（Windows / Linux）"));
assert.ok(admin.includes('option.dataset.platform = worker.platform || "linux"'));
assert.ok(!admin.includes('const none = elem("option", "请选择 Linux Worker")'));
assert.ok(service.includes('return wid,"linux",worker'),
  "Public ext_ must map to actual Linux controller");
assert.ok(service.includes('if platform=="windows" and not url:'),
  "Windows job with no saved ChatGPT URL must fail fast");
assert.ok(service.includes('"visual_verified":False'),
  "Windows DOM-only evidence cannot claim 1:1 verification");
assert.ok(linuxManager.includes("const publicId=visibleWorkerId(w);"));
assert.ok(linuxManager.includes('<td><code>${esc(publicId)}</code>'));
assert.ok(linuxManager.includes('<code>${esc(visibleWorkerId(w))}</code>'));
assert.ok(!linuxManager.includes('<td><code>${esc(id)}</code>'));
assert.ok(!linuxManager.includes('<code>${esc(w.worker_id)}</code>'));
assert.ok(!linuxPairing.includes('workerName || workerId'),
  "Pairing dialog cannot expose a wrk_ fallback");
console.log("Window lease, persistent pool noninterference, ext_ mapping and Windows fail-closed contracts passed");
