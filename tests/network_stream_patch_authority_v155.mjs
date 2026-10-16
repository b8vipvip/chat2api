import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {TextEncoder,TextDecoder} from "node:util";

const source=fs.readFileSync(new URL("../chrome_extension/network_stream_main_v55.js",import.meta.url),"utf8");
const encoder=new TextEncoder();
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
const marker="FDEX_CODEX_SMOKE_example_TOOLS";
const valid='<<<CHAT2API_RESPONSES_TOOL_V109>>>\n{"kind":"final","text":"'+marker+'"}\n<<<END_CHAT2API_RESPONSES_TOOL_V109>>>';
const terminalProof=[
  {"p":"/message/status","o":"replace","v":"finished_successfully"},
  {"p":"/message/end_turn","o":"add","v":true},
];

async function collect(events){
  const messages=[];
  const data=events.map(item=>encoder.encode("data: "+JSON.stringify(item)+"\n\n"));
  let index=0;
  const reader={async read(){return index<data.length?{done:false,value:data[index++]}:{done:true}},releaseLock(){}};
  const response={ok:true,status:200,body:{},headers:{get:()=>"text/event-stream"},clone:()=>({body:{getReader:()=>reader}})};
  const ctx={TextDecoder,structuredClone,URL,Date,Math,setTimeout,
    location:{href:"https://chatgpt.com/"},document:{documentElement:{setAttribute(){}}},
    postMessage:m=>messages.push(m),fetch:async()=>response};
  ctx.globalThis=ctx;
  vm.createContext(ctx);
  vm.runInContext(source,ctx);
  await ctx.fetch("https://chatgpt.com/backend-api/f/conversation",{method:"POST"});
  for(let i=0;i<20;i++)await tick();
  return messages;
}

const root={p:"",o:"add",v:{message:{author:{role:"assistant"},content:{parts:[valid+"TRAILING_NOISE"]},status:"in_progress"}}};
const rewrite={p:"/message/content/parts/0",o:"replace",v:valid};
const frames=await collect([root,rewrite,...terminalProof]);
const snapshots=frames.filter(e=>e.phase==="assistant-snapshot");
assert.equal(snapshots.at(-1).text,valid,
  "latest authoritative JSON Patch must replace even a longer prefix-compatible draft");
const completed=frames.filter(e=>e.phase==="assistant-complete");
assert.equal(completed.length,1);
assert.equal(completed[0].text,valid,"terminal SSE text must be the final reconstructed body");
assert.ok(!completed[0].text.includes("TRAILING_NOISE"));

const fragmented=await collect([
  {p:"",o:"add",v:{message:{author:{role:"assistant"},content:{parts:["fragment"]},status:"in_progress"}}},
  {p:"/message/content/parts/0",o:"replace",v:valid},
  {type:"text_delta",delta:"UNTRUSTED_FALLBACK_LONGER_THAN_AUTHENTIC_REPLY".repeat(5)},
  ...terminalProof,
]);
assert.equal(fragmented.filter(x=>x.phase==="assistant-complete")[0]?.text,valid,
  "supplementary deltas cannot supersede assistant JSON Patch ownership");
console.log("Responses SSE parser preserves JSON Patch final text despite rewrite and mixed deltas");
