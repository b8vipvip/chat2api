import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source = readFileSync("app/chat_client_v1.js","utf8");
const start = source.indexOf("  async function hydrateNativeSnapshots(");
const end = source.indexOf("  function resumePendingMessages(",start);
assert.ok(start >= 0 && end > start);

const latest = {
  id:"latest-conversation",
  model:"gpt-5.6-sol",
  messages:[
    {role:"assistant",requestId:"latest-1"},
    {role:"user",content:"A question"},
    {role:"assistant",requestId:"latest-2"},
  ],
};
const older = {
  id:"older-conversation",
  model:"gpt-5.6-sol",
  messages:Array.from({length:30},(_,index)=>({
    role:"assistant",
    requestId:"older-" + (index + 1),
    pending:index === 29,
  })),
};
const state = {
  activeConversationId:latest.id,
  conversations:[latest,older],
  voiceActive:false,
  streaming:false,
  pendingImages:[],
  pendingImagesConversationId:"",
  pendingFiles:[],
  pendingFilesConversationId:"",
  selectedModel:"gpt-5.6-sol",
};
const fetched = [];
const streams = [];
const hydrateNativeSnapshots = vm.runInNewContext(
  source.slice(start,end) + "\nhydrateNativeSnapshots",
  {
    state,
    refreshNativeSnapshot:async requestId=>{fetched.push(requestId);return true;},
    streamNativeContent:requestId=>{streams.push(requestId);return Promise.resolve();},
    Promise,Set,
  },
);
await hydrateNativeSnapshots();
assert.deepEqual(fetched,["latest-1","latest-2"],
  "bootstrap must hydrate the active conversation, not the last 24 requests of an older conversation");
assert.deepEqual(streams,[]);

const switchStart = source.indexOf("  function setActiveConversation(");
const switchEnd = source.indexOf("  function deleteConversation(",switchStart);
assert.ok(switchStart >= 0 && switchEnd > switchStart);
let rendered = 0;
let lastRequestedConversation = "";
const switchConversation = vm.runInNewContext(
  source.slice(switchStart,switchEnd) + "\nsetActiveConversation",
  {
    state,
    toast(){throw Error("switch should be allowed");},
    currentConversation:()=>state.conversations.find(c=>c.id === state.activeConversationId),
    $:()=>({value:""}),
    loadModels(){},
    renderAll(){rendered+=1;},
    hydrateNativeSnapshots:async conversationId=>{
      lastRequestedConversation=conversationId;
      return hydrateNativeSnapshots(conversationId);
    },
    document:{body:{classList:{remove(){}}}},
  },
);
fetched.length = 0;
switchConversation(older.id);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(rendered,1);
assert.equal(state.activeConversationId,older.id);
assert.equal(lastRequestedConversation,older.id);
assert.deepEqual(fetched,Array.from({length:24},(_,index)=>"older-" + (index + 7)),
  "switching to an older synchronized conversation must fetch its most recent 24 native snapshots");
assert.deepEqual(streams,["older-30"],
  "pending native content must restart its sidecar stream when the conversation is selected");

// GET hydration and the SSE sidecar race independently. A delayed GET (or
// 404) must never overwrite the revision or archive state received meanwhile.
const snapshotStart = source.indexOf("  function assistantMessageForRequest(");
const snapshotEnd = source.indexOf("  function consumeNativeSseFrame(",snapshotStart);
assert.ok(snapshotStart >= 0 && snapshotEnd > snapshotStart);
const target = {role:"assistant",requestId:"native-race"};
const targetConversation = {id:"race-conversation",messages:[target]};
const raceState = {conversations:[targetConversation],nativeSnapshots:new Map()};
let finishFetch;
let raceRenders = 0;
const {applyNativeSnapshot,refreshNativeSnapshot} = vm.runInNewContext(
  source.slice(snapshotStart,snapshotEnd) + "\n({applyNativeSnapshot,refreshNativeSnapshot})",
  {
    state:raceState,
    window:{Chat2APINativeRendererV1:{validateSnapshot:s=>Boolean(s && Number.isInteger(s.revision))}},
    currentConversation:()=>targetConversation,
    renderMessages:()=>{raceRenders+=1;},
    fetch:()=>new Promise(resolve=>{finishFetch=resolve;}),
  },
);
const snapshot = (revision,finality="live-provisional",assets=[])=>({
  revision,finality,assets,files:[],root:{tag:"div",text:"revision "+revision},
});
const v1 = snapshot(1);
const v2 = snapshot(2);
const v2WithMedia = snapshot(2,"live-provisional",["verified-media"]);
const v2Final = snapshot(2,"verified-final",["verified-media"]);
assert.equal(applyNativeSnapshot("native-race",v1),true);
let hydration = refreshNativeSnapshot("native-race");
assert.equal(applyNativeSnapshot("native-race",v2),true);
finishFetch({ok:true,status:200,json:async()=>v1});
assert.equal(await hydration,false);
assert.equal(raceState.nativeSnapshots.get("native-race"),v2);
const beforeStaleRender = raceRenders;
assert.equal(applyNativeSnapshot("native-race",v1),false);
assert.equal(raceRenders,beforeStaleRender);

hydration = refreshNativeSnapshot("native-race");
assert.equal(applyNativeSnapshot("native-race",v2WithMedia),true);
finishFetch({ok:true,status:200,json:async()=>v2});
assert.equal(await hydration,false,"same-revision archive data must not regress");
assert.equal(raceState.nativeSnapshots.get("native-race"),v2WithMedia);

hydration = refreshNativeSnapshot("native-race");
const v3 = snapshot(3);
assert.equal(applyNativeSnapshot("native-race",v3),true);
finishFetch({ok:false,status:404});
assert.equal(await hydration,false);
assert.equal(raceState.nativeSnapshots.get("native-race"),v3,"stale 404 must not erase a fresh snapshot");

hydration = refreshNativeSnapshot("native-race");
finishFetch({ok:false,status:404});
assert.equal(await hydration,false);
assert.equal(raceState.nativeSnapshots.has("native-race"),false,"current provisional 404 still clears stale content");
assert.equal(target.nativeFinality,undefined);

assert.equal(applyNativeSnapshot("native-race",v2WithMedia),true);
hydration = refreshNativeSnapshot("native-race");
finishFetch({ok:true,status:200,json:async()=>v2Final});
assert.equal(await hydration,true,"a current GET can promote the same revision to verified final");
assert.equal(target.nativeFinality,"verified-final");
assert.equal(applyNativeSnapshot("native-race",v2WithMedia),false,"same-revision provisional replay cannot downgrade finality");
assert.equal(raceState.nativeSnapshots.get("native-race"),v2Final);

console.log("web-chat-native-hydration-v1-passed");
