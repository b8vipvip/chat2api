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

console.log("web-chat-native-hydration-v1-passed");
