import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync("app/chat_client_v1.js", "utf8");
const start = source.indexOf("  function messageContentWeight(");
const end = source.indexOf("  async function syncServerConversations(", start);
assert.ok(start >= 0 && end > start, "conversation merge helpers must remain extractable");
const referenceHelpersStart = source.indexOf("  function cleanToolReferenceText(");
const referenceHelpersEnd = source.indexOf("  function toolReferencesEqual(", referenceHelpersStart);
assert.ok(referenceHelpersStart >= 0 && referenceHelpersEnd > referenceHelpersStart,
  "production citation normalization helpers must remain extractable");
const context = vm.createContext({
  MAX_MESSAGES:100,
  URL,
  window:{location:{origin:"https://chat.example"}},
});
vm.runInContext(source.slice(referenceHelpersStart, referenceHelpersEnd), context);
const mergeConversation = vm.runInContext(
  source.slice(start, end) + "\nmergeConversation",
  context,
);


test("conversation merge preserves 100 server messages when local cache has only 30", () => {
  const messages = Array.from({length:100},(_,index)=>({
    id:"message_server_" + String(index).padStart(4,"0"),
    role:index % 2 ? "assistant" : "user",
    content:"message " + index,
    createdAt:"2026-11-20T00:" + String(Math.floor(index / 60)).padStart(2,"0") + ":" + String(index % 60).padStart(2,"0") + "Z",
    pending:false,
  }));
  const server = {
    id:"c2a_long_history_0001",
    title:"long server history",
    updatedAt:"2026-11-20T02:00:00Z",
    serverRevision:8,
    messages,
  };
  const local = {
    ...server,
    updatedAt:"2026-11-20T02:00:01Z",
    messages:messages.slice(-30),
  };
  const merged = mergeConversation(server,local);
  assert.equal(merged.messages.length,100);
  assert.equal(merged.messages[0].id,"message_server_0000");
  assert.equal(merged.messages.at(-1).id,"message_server_0099");
});

test("conversation merge keeps remote messages and local-only turns", () => {
  const server = {
    id: "c2a_merge_test_0001",
    title: "server title",
    updatedAt: "2026-11-20T00:00:03Z",
    serverRevision: 3,
    messages: [
      {id: "message_user_0001", role: "user", content: "hello", createdAt: "2026-11-20T00:00:00Z"},
      {id: "message_assistant_01", role: "assistant", content: "partial", pending: true, recoveryStatus: "streaming", createdAt: "2026-11-20T00:00:01Z"},
    ],
  };
  const local = {
    id: server.id,
    title: "local newer title",
    updatedAt: "2026-11-20T00:00:04Z",
    serverRevision: 1,
    messages: [
      {id: "message_user_0001", role: "user", content: "hello", createdAt: "2026-11-20T00:00:00Z"},
      {id: "message_assistant_01", role: "assistant", content: "partial answer with more text", pending: true, recoveryStatus: "streaming", createdAt: "2026-11-20T00:00:01Z"},
      {id: "message_user_0002", role: "user", content: "follow up", createdAt: "2026-11-20T00:00:02Z"},
    ],
  };
  const merged = mergeConversation(server, local);
  assert.equal(merged.title, "local newer title");
  assert.equal(merged.serverRevision, 3);
  assert.equal(merged.messages.length, 3);
  assert.equal(merged.messages[1].content, "partial answer with more text");
  assert.equal(merged.messages[2].id, "message_user_0002");
});

test("a verified completed remote reply wins over a longer stale pending stream", () => {
  const server = {
    id: "c2a_merge_test_0002",
    updatedAt: "2026-11-20T00:00:05Z",
    serverRevision: 4,
    messages: [
      {id: "message_assistant_02", role: "assistant", content: "final", pending: false, recoveryStatus: "completed"},
    ],
  };
  const local = {
    id: server.id,
    updatedAt: "2026-11-20T00:00:06Z",
    messages: [
      {id: "message_assistant_02", role: "assistant", content: "longer but incomplete partial text", pending: true, recoveryStatus: "streaming"},
    ],
  };
  const merged = mergeConversation(server, local);
  assert.equal(merged.messages[0].content, "final");
  assert.equal(merged.messages[0].pending, false);
});

test("a locally completed reply wins over a stale pending server placeholder", () => {
  const server = {
    id: "c2a_merge_test_0003",
    updatedAt: "2026-11-20T00:00:05Z",
    serverRevision: 2,
    messages: [
      {id: "message_assistant_03", role: "assistant", content: "", pending: true, recoveryStatus: "streaming"},
    ],
  };
  const local = {
    id: server.id,
    updatedAt: "2026-11-20T00:00:06Z",
    messages: [
      {id: "message_assistant_03", role: "assistant", content: "final local reply", pending: false, recoveryStatus: "completed"},
    ],
  };
  const merged = mergeConversation(server, local);
  assert.equal(merged.messages[0].content, "final local reply");
  assert.equal(merged.messages[0].pending, false);
});



test("conversation sync retries transient transport failures with bounded backoff", async () => {
  const syncStart = source.indexOf("  function scheduleConversationSync(");
  const syncEnd = source.indexOf("  async function deleteConversationFromServer(", syncStart);
  assert.ok(syncStart >= 0 && syncEnd > syncStart);

  const conversation = {
    id:"c2a_sync_retry_0001",
    title:"retry me",
    model:"gpt-5.6-sol",
    apiKeyId:"",
    createdAt:"2026-11-23T00:00:00Z",
    updatedAt:"2026-11-23T00:00:01Z",
    messages:[],
    serverRevision:0,
  };
  const state = {
    remoteSyncReady:true,
    remoteSyncTimers:new Map(),
    remoteSyncInFlight:new Map(),
    remoteSyncAgain:new Set(),
    remoteSyncFingerprints:new Map(),
    remoteSyncRetryCounts:new Map(),
    remoteSyncWarned:false,
    conversations:[conversation],
    activeConversationId:conversation.id,
  };
  const timers = new Map();
  let nextTimerId = 0;
  let calls = 0;
  const context = vm.createContext({
    state,
    window:{
      setTimeout(callback,delay) {
        const id = ++nextTimerId;
        timers.set(id,{callback,delay});
        return id;
      },
      clearTimeout(id) { timers.delete(id); },
    },
    fetch:async (_url,options) => {
      calls += 1;
      if (calls === 1) throw new TypeError("network offline");
      const body = JSON.parse(options.body);
      return {
        ok:true,status:200,
        json:async () => ({
          conversation:{
            ...body.conversation,
            revision:1,
            updatedAt:body.conversation.updatedAt,
          },
        }),
      };
    },
    serverConversationPayload:row => ({
      id:row.id,title:row.title,model:row.model,apiKeyId:row.apiKeyId,
      createdAt:row.createdAt,updatedAt:row.updatedAt,messages:row.messages,
    }),
    normalizeConversationRow:row => ({...row,serverRevision:Number(row.revision || 0)}),
    mergeConversation:(_server,local) => local,
    persistHistorySnapshot() {},
    renderAll() {},
    toast() {},
  });
  const api = vm.runInContext(
    source.slice(syncStart,syncEnd) + "\n({scheduleConversationSync,syncConversationToServer})",
    context,
  );

  await api.syncConversationToServer(conversation.id);
  assert.equal(calls,1);
  assert.equal(state.remoteSyncRetryCounts.get(conversation.id),1);
  assert.equal(timers.size,1);
  assert.equal([...timers.values()][0].delay,1000);

  const timerId = state.remoteSyncTimers.get(conversation.id);
  context.window.clearTimeout(timerId);
  state.remoteSyncTimers.delete(conversation.id);
  await api.syncConversationToServer(conversation.id);

  assert.equal(calls,2);
  assert.equal(conversation.serverRevision,1);
  assert.equal(state.remoteSyncRetryCounts.has(conversation.id),false);
  assert.equal(state.remoteSyncFingerprints.has(conversation.id),true);
});


test("conversation sync does not retry permanent client errors", async () => {
  const syncStart = source.indexOf("  function scheduleConversationSync(");
  const syncEnd = source.indexOf("  async function deleteConversationFromServer(", syncStart);
  assert.ok(syncStart >= 0 && syncEnd > syncStart);

  const conversation = {
    id:"c2a_sync_retry_0002",title:"oversized",model:"gpt-5.6-sol",
    apiKeyId:"",createdAt:"2026-11-23T00:00:00Z",
    updatedAt:"2026-11-23T00:00:01Z",messages:[],serverRevision:0,
  };
  const state = {
    remoteSyncReady:true,remoteSyncTimers:new Map(),
    remoteSyncInFlight:new Map(),remoteSyncAgain:new Set(),
    remoteSyncFingerprints:new Map(),remoteSyncRetryCounts:new Map(),
    remoteSyncWarned:false,conversations:[conversation],
    activeConversationId:conversation.id,
  };
  const timers = new Map();
  let nextTimerId = 0;
  const context = vm.createContext({
    state,
    window:{
      setTimeout(callback,delay) { const id=++nextTimerId;timers.set(id,{callback,delay});return id; },
      clearTimeout(id) { timers.delete(id); },
    },
    fetch:async () => ({
      ok:false,status:413,json:async () => ({detail:"conversation is too large"}),
    }),
    serverConversationPayload:row => ({
      id:row.id,title:row.title,model:row.model,apiKeyId:row.apiKeyId,
      createdAt:row.createdAt,updatedAt:row.updatedAt,messages:row.messages,
    }),
    normalizeConversationRow:row => ({...row,serverRevision:Number(row.revision || 0)}),
    mergeConversation:(_server,local) => local,
    persistHistorySnapshot() {},
    renderAll() {},
    toast() {},
  });
  const api = vm.runInContext(
    source.slice(syncStart,syncEnd) + "\n({scheduleConversationSync,syncConversationToServer})",
    context,
  );

  await api.syncConversationToServer(conversation.id);
  assert.equal(timers.size,0);
  assert.equal(state.remoteSyncRetryCounts.has(conversation.id),false);
});


test("stale same-query search responses cannot overwrite a newer search", async () => {
  const start = source.indexOf("  async function searchConversationsOnServer(");
  const end = source.indexOf("  function onConversationSearchInput(", start);
  assert.ok(start >= 0 && end > start);
  const state = {searchController:null,searchResults:null,searchQuery:"",searchWarned:false};
  const requests = [];
  let renders = 0, warnings = 0;
  const context = vm.createContext({
    state, AbortController,
    $:()=>({value:"same"}),
    fetch:(_url,options)=>new Promise((resolve,reject)=>requests.push({resolve,reject,signal:options.signal})),
    normalizeConversationRow:row=>({...row}),
    renderSidebar:()=>{renders += 1;},
    toast:()=>{warnings += 1;},
  });
  const search = vm.runInContext(source.slice(start,end)+"\nsearchConversationsOnServer",context);
  const reply = id=>({ok:true,json:async()=>({data:[{id,serverRevision:1}]})});
  const first = search("same");
  // Simulate a transport that resolves despite cancellation.
  state.searchController.abort();
  const second = search("same");
  requests[1].resolve(reply("new"));
  await second;
  requests[0].resolve(reply("old"));
  await first;
  assert.equal(state.searchResults[0].id,"new");
  assert.equal(renders,1);

  const third = search("same");
  state.searchController.abort();
  const fourth = search("same");
  requests[3].resolve(reply("newer"));
  await fourth;
  requests[2].reject(new Error("late network error"));
  await third;
  assert.equal(state.searchResults[0].id,"newer");
  assert.equal(warnings,0);
  assert.equal(renders,2);
});
