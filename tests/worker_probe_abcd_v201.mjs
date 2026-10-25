import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const script=readFileSync(new URL("../chrome_extension/content_probe_supervisor_v1.js",import.meta.url),"utf8");
const sent=[];
const elem=(text="")=>({
  innerText:text,textContent:text,
  getBoundingClientRect:()=>({width:200,height:100}),
  getAttribute:()=>"",hasAttribute:()=>false,
  closest:()=>null,
});
const code=elem("def run(): return 42");
const table=elem("成本 | 收入 | 利润");
const button=elem("");
const main={
  id:"thread",tagName:"MAIN",
  querySelectorAll(selector){
    if(selector.includes("class*='markdown'")&&selector.includes("pre"))return [code,table];
    if(selector==="pre")return [code];
    if(selector==="table")return [table];
    if(selector==="table tr")return [elem("row1"),elem("row2")];
    if(selector==="button")return [button];
    return [];
  },
};
const document={
  readyState:"complete",
  querySelector(selector){return selector==="main"?main:null;},
  querySelectorAll(){return [];},
};
const ctx={document,performance:{now:()=>10},
  chrome:{runtime:{sendMessage:async ({event})=>{sent.push(event);return {ok:true};}}},
  getComputedStyle:()=>({display:"block",visibility:"visible"}),setTimeout,
};
runInNewContext(script,ctx);
const probes=ctx.__CHAT2API_PROBE_SUPERVISOR_V1__;
const ids=["media_delivery_v1","conversation_dom_v1","network_evidence_v1",
  "model_tool_state_v1","selector_compat_v1","abcd_richtext_v1",
  "abcd_images_v1","abcd_files_v1","abcd_context_v1","response_boundary_v1"];
test("All ABCD probes share one read-only supervisor and stay within 32 events",async()=>{
  const active={
    requestId:"req_scenev201abcdef",
    networkObservedText:"PRIVATE NETWORK CONTENT MUST NEVER APPEAR",
    networkStreamSeen:true,networkTerminalTransportErrorAt:123,
    baselineRenderedText:new Map(),
    promptNormalized:"PRIVATE USER PROMPT MUST NEVER APPEAR",
    options:{probe_policy:{schema_version:1,
      tools:Object.fromEntries(ids.map(id=>[id,{enabled:true}]))}},
  };
  probes.begin(active);
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(sent.length,10);
  const facts=sent[0].facts;
  assert.equal(facts.scene_code_block_count,1);
  assert.equal(facts.scene_markdown_row_count,2);
  assert.equal(facts.scene_network_transport_error,true);
  assert.ok(facts.scene_rich_max_chars>=18);
  await probes.capture(active,"unresolved_after_threshold");
  await probes.capture(active,"before_terminal",{completed:false});
  assert.equal(sent.length,30);
  assert.deepEqual([...new Set(sent.map(x=>x.tool_id))].sort(),ids.sort());
  assert.ok(sent.every(x=>x.type==="probe.event"));
  const raw=JSON.stringify(sent);
  assert.ok(!raw.includes("PRIVATE NETWORK CONTENT"));
  assert.ok(!raw.includes("PRIVATE USER PROMPT"));
  assert.ok(!script.includes('type: "chat.completed"'));
  assert.ok(!script.includes('type: "chat.error"'));
});
