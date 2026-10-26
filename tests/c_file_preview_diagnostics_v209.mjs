import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";
const rich=readFileSync("chrome_extension/content_rich_response_v69.js","utf8");
const script=readFileSync("chrome_extension/content_probe_supervisor_v1.js","utf8");
const conversation="https://chatgpt.com/c/6adef394-178c-83e8-8cfa-517cd8234a35";
const links=["xlsx","pptx","json"].map(ext=>({
  textContent:"Open preview of 城市旧书交换节."+ext,
  innerText:"Open preview of 城市旧书交换节."+ext,
  getAttribute(key){return key==="href"?conversation:key==="aria-label"?this.textContent:"";},
  hasAttribute:()=>false,
  getBoundingClientRect:()=>({width:280,height:60}),
}));
const main={
  id:"thread",tagName:"MAIN",
  querySelectorAll(sel){
    if(sel.startsWith("a,button,"))return links;
    if(sel==="a[href]"||sel==="a[download],a[href]")return links;
    return [];
  },
};
let now=0;
const sent=[];
const context={
  document:{readyState:"complete",querySelector:k=>k==="main"?main:null,querySelectorAll:()=>[]},
  Date:{now:()=>now},location:{href:conversation},performance:{now:()=>10},URL,
  getComputedStyle:()=>({display:"block",visibility:"visible"}),setTimeout,
  chrome:{runtime:{sendMessage:async ({event})=>{sent.push(event);return {ok:true};}}},
};
runInNewContext(script,context);
const probes=context.__CHAT2API_PROBE_SUPERVISOR_V1__;
const toolIds=["media_delivery_v1","conversation_dom_v1","network_evidence_v1",
  "model_tool_state_v1","selector_compat_v1","abcd_richtext_v1",
  "abcd_images_v1","abcd_files_v1","abcd_context_v1","response_boundary_v1"];

test("preview cards are visible metadata, not evidence of 4 real files",()=>{
  const facts=probes.snapshot();
  assert.equal(facts.file_preview_control_count,3);
  assert.equal(facts.file_preview_xlsx_count,1);
  assert.equal(facts.file_preview_pptx_count,1);
  assert.equal(facts.file_preview_json_count,1);
  assert.equal(facts.file_preview_zip_count,0);
  assert.equal(facts.file_preview_conversation_link_count,3);
  assert.ok(!JSON.stringify(facts).includes("6adef394"));
});

test("baseline, 1/3/7/12-minute checkpoints and final stay within 32 events",async()=>{
  const active={
    requestId:"req_previewabcdef12345",networkStreamSeen:true,
    baselineRenderedText:new Map(),promptNormalized:"PRIVATE PROMPT",
    options:{timeout_seconds:900,probe_policy:{schema_version:1,
      tools:Object.fromEntries(toolIds.map(id=>[id,{enabled:true}]))}},
  };
  probes.begin(active);
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(sent.length,10);
  for(const tick of [60000,180000,420000,720000]){
    now=tick;
    probes.observe(active);
    await Promise.resolve();
    await Promise.resolve();
  }
  assert.equal(sent.length,22);
  await probes.capture(active,"before_terminal",{completed:false});
  assert.equal(sent.length,32);
  assert.equal(sent.filter(e=>e.stage==="progress_checkpoint").length,12);
  assert.equal(sent.filter(e=>e.stage==="before_terminal").length,10);
  assert.equal(new Set(sent.map(e=>e.sequence)).size,32);
  assert.ok(!JSON.stringify(sent).includes("PRIVATE PROMPT"));
  assert.ok(!script.includes('type: "chat.completed"'));
});

test("conversation preview URLs never become generated-file binary",()=>{
  const start=rich.indexOf("  function conversationPreviewUrl(uri) {");
  const end=rich.indexOf("  function validGeneratedFileBytes",start);
  assert.ok(start>=0&&end>start);
  class Element {}
  const result=runInNewContext(rich.slice(start,end)+[
    "const anchor = new Element();",
    "anchor.getAttribute = key => key === 'href' ? 'https://chatgpt.com/c/6adef394-178c-83e8-8cfa-517cd8234a35' : key === 'aria-label' ? 'Open preview of city.xlsx' : '';",
    "anchor.closest = () => null;",
    "recognizedFile(anchor);",
  ].join("\n"),{
    Element,URL,location:{href:conversation},
    FILE_MIME_BY_EXT:{xlsx:"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    trustedMediaSource:()=>true,
  });
  assert.equal(result,null);
  assert.match(rich,/preview-only-no-binary-link/);
});

test("only the single request controller may capture sibling cards in a verified reply",()=>{
  const request=readFileSync("chrome_extension/content_request_v6.js","utf8");
  assert.match(request,/const fileReplyBoundary = fileCreationRequested/);
  assert.match(request,/verifiedRenderedAbortCandidate\(active, current\)/);
  assert.match(request,/const mediaReplyRoot = fileReplyBoundary\?\.replyScope \|\| sameTurnRoot/);
  assert.match(request,/await finalNodeText\(mediaReplyRoot\)/);
  assert.match(request,/const delivered = deliveredImages \+ deliveredFiles/);
  assert.match(request,/delivered > 0 && \(hasTransferableImage\(captured\) \|\| hasTransferableFile\(captured\)\)/);
  assert.match(rich,/if \(conversationPreviewUrl\(uri\)\) return null/);
});
