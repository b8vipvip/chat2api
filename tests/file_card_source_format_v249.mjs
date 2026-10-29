import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const isolated=fs.readFileSync("chrome_extension/content_native_file_v210.js","utf8");
const main=fs.readFileSync("chrome_extension/native_file_main_v210.js","utf8");
const archive=fs.readFileSync("app/generated_files_v196.py","utf8");
const worker=fs.readFileSync("chrome_extension/content_request_v6.js","utf8");

for(const ext of ["py","js","ts","html"]) {
  assert.match(isolated,new RegExp("\\b"+ext+":\\""));
  assert.match(main,new RegExp("\\b"+ext+":\\""));
  assert.match(archive,new RegExp('"\\.'+ext+'":'));
}
const start=isolated.indexOf("  function candidateButtons(scope) {");
const end=isolated.indexOf("  function signedBytes(",start);
assert.ok(start>0 && end>start,"scoped native file handlers exist");
const ctx={
  MIMES:{
    py:"text/x-python",js:"text/javascript",
    ts:"application/typescript",html:"text/html",
    xlsx:"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  },
  FILENAME: /([^\s\n<>:"\/\\|?*]{1,135}\.(?:xlsx|py|js|ts|html))(?=$|[\s）),;])/ig,
  getComputedStyle:()=>({display:"block",visibility:"visible"}),
  URL, location:{origin:"https://chatgpt.com",href:"https://chatgpt.com/c/one"}
};
function candidate(label,href="/c/one/download", tag="A") {
 const node={
  tagName:tag,textContent:label,innerText:label,disabled:false,
  parentElement:null,
  getBoundingClientRect:()=>({width:100,height:40}),
  getAttribute:n=>n==="href"?href:n==="aria-label"?label:null,
  hasAttribute:()=>false,closest:()=>null,
  querySelectorAll:()=>[],
 };
 return node;
}
const code=isolated.slice(isolated.indexOf("  const visible = "),end);
vm.runInNewContext(code+"\nthis.candidates=candidateButtons;this.exclusions=candidateExclusionEvidence;",ctx);
const safe=[
 candidate("work.py"),
 candidate("layout.js"),
 candidate("project.ts"),
 candidate("index.html"),
];
const offsite=candidate("private.py","https://external.invalid/private.py");
const wrong=candidate("查看设置","/settings");
const scope={querySelectorAll:()=>[...safe,offsite,wrong]};
const selected=ctx.candidates(scope);
assert.equal(selected.length,4);
assert.ok(safe.every(n=>selected.some(item=>item.button===n)));
const buckets=ctx.exclusions({querySelectorAll:()=>[offsite,wrong]});
assert.equal(buckets.external_link,1);
assert.equal(buckets.filename_absent,1);
assert.ok(!JSON.stringify(buckets).includes("private.py"));
assert.ok(!JSON.stringify(buckets).includes("external.invalid"));
assert.ok(!JSON.stringify(buckets).includes("https://"));
assert.match(worker,/media_native_file_candidate_exclusion_counts/);
assert.match(worker,/media_file_ready_since_submit_ms/);
assert.doesNotMatch(isolated,/type: "chat.completed"/);
console.log("v249 source-file capture parity and bounded privacy-safe candidate evidence passed");
