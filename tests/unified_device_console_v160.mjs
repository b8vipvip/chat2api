import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
const src=fs.readFileSync(new URL("../app/admin_unified_workers_v153.js",import.meta.url),"utf8");
const pos=src.indexOf("/* Unified equipment and Worker presentation v160.");
assert.ok(pos>0,"v160 unified console must be bundled after legacy event owners");
const context={
  document:{readyState:"loading",addEventListener(){}},
  globalThis:null,
};
context.globalThis=context;
vm.runInNewContext(src.slice(pos),context);
const model=context.__CHAT2API_UNIFIED_DEVICE_CONSOLE_V160__;
assert.equal(model.version,"v160");
const devices=[{
  device_id:"install_linux",device_name:"TX03",device_pairing_id:"pair_linux",
  workers:[{worker_id:"wrk_one",extension_client_id:"ext_linux_a"},
           {worker_id:"wrk_two",extension_client_id:"ext_linux_b"}]
}];
const payload={pairing_codes:[
  {pairing_id:"pair_linux",name:"TX03 renamed",bound_client_id:"ext_linux_a",enabled:true,prefix:"L001"},
  {pairing_id:"pair_win_a",name:"Windows 1",bound_client_id:"ext_win_a",bound_device_id:"physical_win",enabled:true,prefix:"W001"},
  {pairing_id:"pair_win_b",name:"Windows 2",bound_client_id:"ext_win_b",bound_device_id:"physical_win",enabled:false,prefix:"W002"},
  {pairing_id:"pair_pending_a",name:"New",enabled:true,prefix:"P001"},
  {pairing_id:"pair_pending_b",name:"New",enabled:true,prefix:"P002"},
],clients:[
  {client_id:"ext_linux_a",online:true,metadata:{linux_worker_id:"wrk_one"}},
  {client_id:"ext_linux_b",online:true,metadata:{linux_worker_id:"wrk_two"}},
  {client_id:"ext_win_a",online:true,device_id:"physical_win",metadata:{platform:"windows"}},
  {client_id:"ext_win_b",online:false,device_id:"physical_win",metadata:{platform:"windows"}},
  {client_id:"ext_standalone_linux",online:true,device_id:"standalone",metadata:{platform:"linux"}},
  {client_id:"ext_conflict",online:true,metadata:{linux_bridge_binding_conflict:true,linux_worker_id:"wrk_one"}}
]};
const rows=model.aggregate(devices,payload);
assert.equal(rows.length,6,"one Linux, one Windows, two pending codes, standalone Linux and separate conflict");
assert.equal(model.aggregate([{...devices[0],device_pairing_id:""}],payload).length,6,
  "exact Bridge Client ID must merge an installed Linux device and its code even without pairing metadata");
const linux=rows.find(x=>x.linux?.device_id==="install_linux");
const linuxHtml=model.deviceMarkup(linux);
assert.match(linuxHtml,/data-device-add-worker="install_linux"/);
assert.match(linuxHtml,/>增加 Worker<\/button>/);
assert.match(linuxHtml,/data-pairing-list-action="rename"/);
assert.match(linuxHtml,/data-pairing-list-action="copy"/);
assert.match(linuxHtml,/data-pairing-list-action="toggle"/);
assert.match(linuxHtml,/data-device-upgrade="install_linux"/);
assert.doesNotMatch(linuxHtml,/data-manage-device=|data-delete-device=|>管理<|>删除设备</,
  "Linux device actions must not expose Management or Delete device");
assert.match(linuxHtml,/data-pairing-list-action="rename"/);
assert.match(linuxHtml,/data-pairing-list-action="copy"/);
assert.match(linuxHtml,/data-pairing-list-action="toggle"/);
const legacyLinux=fs.readFileSync(new URL("../app/admin_linux_device_authority_v124.js",import.meta.url),"utf8");
const legacyActions=legacyLinux.slice(legacyLinux.indexOf("  function renderDevices(){"),
  legacyLinux.indexOf("  function renderWorkers(){"));
assert.doesNotMatch(legacyActions,/data-delete-device=|data-manage-device=/,
  "Hidden historical device table cannot re-introduce removed actions");

assert.equal(linux.codes.length,1);
assert.equal(linux.clients.length,2);
assert.equal(linux.codes[0].name,"TX03 renamed");
const win=rows.find(x=>x.id==="physical_win");
assert.equal(win.codes.length,2,"multiple codes must retain separate actions in one physical row");
assert.equal(win.clients.length,2);
assert.equal(rows.filter(x=>x.key.startsWith("pairing:")).length,2,"identical names must never merge pending codes");
const pending=rows.find(x=>x.key==="pairing:pair_pending_a");
assert.ok(!model.deviceMarkup(pending).includes('data-device-add-worker='),
  "uninstalled device codes cannot create Linux Workers without a controller");
assert.equal((model.deviceMarkup(win).match(/data-pairing-list-action="toggle"/g)||[]).length,2,
  "each pairing code retains independent enable/disable controls");

assert.equal(rows.find(x=>x.id==="standalone").clients[0].client_id,"ext_standalone_linux");
assert.equal(rows.find(x=>x.key==="client:ext_conflict").clients.length,1);
assert.ok(!linux.clients.some(x=>x.client_id==="ext_conflict"),
  "conflicting Bridge may not contaminate Linux Worker");
assert.ok(src.includes('data-pairing-list-action="rename"'));
assert.ok(src.includes('data-pairing-list-action="copy"'));
assert.ok(src.includes('data-pairing-list-action="toggle"'));
assert.ok(src.includes('chat2api:linux-devices-loaded'));
console.log("v160 unified device/Worker identity and action contracts passed");
