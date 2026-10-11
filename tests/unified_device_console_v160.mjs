import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const src=fs.readFileSync(new URL("../app/admin_unified_workers_v153.js",import.meta.url),"utf8");
const pos=src.indexOf("/* Unified equipment and Worker presentation v160.");
assert.ok(pos>0,"unified equipment console exists");
const runtime={document:{readyState:"loading",addEventListener(){}},globalThis:null};
runtime.globalThis=runtime;
vm.runInNewContext(src.slice(pos),runtime);
const model=runtime.__CHAT2API_UNIFIED_DEVICE_CONSOLE_V160__;
assert.equal(model.version,"v161");

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
assert.equal(rows.length,6);
assert.equal(model.aggregate([{...devices[0],device_pairing_id:""}],payload).length,6,
  "Exact Bridge Client identity reconciles a Linux device with its pairing code");
const linux=rows.find(x=>x.linux?.device_id==="install_linux");
assert.equal(linux.codes.length,1);
assert.equal(linux.clients.length,2);
const linuxHtml=model.deviceMarkup(linux);
for(const action of ["rename","copy","toggle","delete"]){
  assert.equal((linuxHtml.match(new RegExp('data-pairing-list-action="'+action+'"',"g"))||[]).length,1,
    action+" must be a single equipment-level control");
}
assert.match(linuxHtml,/data-device-add-worker="install_linux"/);
assert.match(linuxHtml,/>增加 Worker<\/button>/);
assert.match(linuxHtml,/data-device-upgrade="install_linux"/);
assert.match(linuxHtml,/>删除设备码<\/button>/);
assert.doesNotMatch(linuxHtml,/data-manage-device=|data-delete-device=|>管理<\/button>|>删除设备<\/button>/);
assert.doesNotMatch(linuxHtml,/>设备码(?: \d+)?<\/span>|class="v160-code-actions"/);
assert.ok(!linuxHtml.includes(">L001</span>"));
assert.ok(!linuxHtml.includes(">pair_linux</div>"));

const win=rows.find(x=>x.id==="physical_win");
assert.equal(win.codes.length,2,"multiple codes stay associated with one physical device");
assert.equal(win.clients.length,2);
const winHtml=model.deviceMarkup(win);
assert.equal((winHtml.match(/data-v161-device-code-action=/g)||[]).length,4,
  "One visible set of actions must service all credentials of a device");
assert.equal((winHtml.match(/>改名<\/button>/g)||[]).length,1);
assert.equal((winHtml.match(/>复制<\/button>/g)||[]).length,1);
assert.equal((winHtml.match(/>启用\/停用<\/button>/g)||[]).length,1);
assert.equal((winHtml.match(/>删除设备码<\/button>/g)||[]).length,1);
assert.equal((winHtml.match(/>更新<\/button>/g)||[]).length,1);
assert.doesNotMatch(winHtml,/>设备码 \d+<\/span>|class="v160-code-groups"|>W001<\/span>|>W002<\/span>/);

const chooser=model.choiceMarkup(win,"delete");
assert.equal((chooser.match(/data-pairing-list-action="delete"/g)||[]).length,2,
  "When multiple credentials exist, destructive operations need explicit choice");
assert.match(chooser,/data-pairing-id="pair_win_a"/);
assert.match(chooser,/data-pairing-id="pair_win_b"/);
assert.match(chooser,/>凭据 1 · 删除设备码<\/button>/);
assert.match(chooser,/>凭据 2 · 删除设备码<\/button>/);
assert.equal(model.choiceMarkup(linux,"delete"),"","single-code actions do not require a chooser");
assert.equal(model.choiceMarkup(win,"unknown"),"","unrecognized actions are blocked");
const toggleChooser=model.choiceMarkup(win,"toggle");
assert.match(toggleChooser,/>凭据 1 · 停用<\/button>/);
assert.match(toggleChooser,/>凭据 2 · 启用<\/button>/);
assert.match(toggleChooser,/data-enable="1"/);
assert.match(toggleChooser,/data-enable="0"/);

assert.equal(rows.filter(x=>x.key.startsWith("pairing:")).length,2,
  "Similar names must not merge unpaired credentials");
const pending=rows.find(x=>x.key==="pairing:pair_pending_a");
const pendingHtml=model.deviceMarkup(pending);
assert.ok(!pendingHtml.includes("data-device-add-worker="));
assert.equal((pendingHtml.match(/data-pairing-list-action=/g)||[]).length,4);
assert.ok(!pendingHtml.includes(">P001</div>"),"Unpaired code prefix is hidden");
assert.ok(!pendingHtml.includes(">pair_pending_a</div>"),"Unpaired pairing ID is hidden");
assert.ok(rows.find(x=>x.id==="standalone"));
assert.equal(rows.find(x=>x.key==="client:ext_conflict").clients.length,1);
assert.ok(!linux.clients.some(x=>x.client_id==="ext_conflict"));

assert.ok(!src.includes("<th>设备码</th>"),
  "The device-code column must be removed from the visible table");
assert.ok(src.includes("width:max-content;white-space:nowrap"),
  "Equipment actions remain on one horizontal row (table may scroll)");
assert.ok(src.includes("chooser.showModal()"),"multiple credentials expose a chooser");
assert.ok(src.includes('displayedGroups=new Map(groups.map('),"chooser targets latest entity mapping");

const canonical=fs.readFileSync(new URL("../app/admin_extension_columns.js",import.meta.url),"utf8");
const winStatus=canonical.slice(canonical.indexOf("  function statusPill("),
  canonical.indexOf("  function networkLabel("));
assert.match(winStatus,/>停用<\/span>/);
assert.match(winStatus,/>运行正常<\/span>/);
assert.match(winStatus,/>忙碌<\/span>/);
assert.match(winStatus,/>离线<\/span>/);
assert.doesNotMatch(winStatus,/已启用|已禁用/);

const legacyLinux=fs.readFileSync(new URL("../app/admin_linux_device_authority_v124.js",import.meta.url),"utf8");
const linuxStatus=legacyLinux.slice(legacyLinux.indexOf("  const workerStatus ="),
  legacyLinux.indexOf("  function chatgpt("));
assert.match(linuxStatus,/\["停用","warn"\]/);
assert.match(linuxStatus,/\["运行正常","good"\]/);
assert.doesNotMatch(linuxStatus,/已启用|已禁用/);
const legacyActions=legacyLinux.slice(legacyLinux.indexOf("  function renderDevices(){"),
  legacyLinux.indexOf("  function renderWorkers(){"));
assert.doesNotMatch(legacyActions,/data-delete-device=|data-manage-device=/);

console.log("device action row, multi-credential safety, and cross-platform Worker status contracts passed");
