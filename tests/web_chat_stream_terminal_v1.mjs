import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync("app/chat_client_v1.js", "utf8");
const recoverySource = readFileSync("app/chat_stream_recovery_v1.js", "utf8");
const start = source.indexOf("  function appendDelta(");
const end = source.indexOf("  async function loadModels(", start);
assert.ok(start >= 0 && end > start);

const delta = (text) => "data: " + JSON.stringify({choices: [{delta: {content: text}}]});
const terminal = "data: [DONE]";

async function send(chunks, close = true, onCancel = () => {}, scenario = {}) {
  const conversation = {id: "conversation-test", messages: []};
  const messages = [];
  const leaseOperations = [];
  let plannerCalls = 0, queued = null, completionPosts = 0;
  const pluginScenario=Boolean(scenario.serverPlugin || scenario.pluginFailure || scenario.cancelDuringClaim);
  const body = new ReadableStream({
    start(controller) {
      const encoder = new TextEncoder();
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
      if (close) controller.close();
    },
    cancel: onCancel,
  });
  const node = {value: "", classList: {add() {}, remove() {}}, focus() {}};
  const context = vm.createContext({
    MAX_ASSISTANT_MESSAGE_CHARS:32000,
    ASSISTANT_TRUNCATION_NOTICE:"\n\n[回答超过 Web Chat 的 32,000 字符上限，后续内容未保存]",
    limitAssistantText:value=>String(value || "").slice(0,32000),
    appendAssistantText:(assistant,value)=>{assistant.content=String(assistant.content || "")+String(value || "");},
    state: {
      webChatKeyId: "test-key", webChatScopes: ["chat", "models", "files", "audio"],
      user: {user_id: "user-a"},
      selectedModel: "test-model",
      models: [{id:"test-model",capabilities:["text"]}],
      streaming: false,
      selectedPlugins: pluginScenario ? ["github"] : [],
      remoteSyncReady:pluginScenario,storageKey:"user-a",
      remoteSyncInFlight:new Map(),remoteSyncTimers:new Map(),remoteSyncFingerprints:new Map(),
      recoveryInFlight: new Set(),
      pendingImages: [],
      pendingImagesConversationId: "",
      pendingFiles: [],
      pendingFilesConversationId: "",
    },
    currentConversation: () => conversation,
    // Image/document attachment helpers are outside the extracted sendMessage slice.
    selectedKeyId: () => "test-key",
    selectedKeyHasScope: () => true,
    setConnection() {},
    recordClientDiagnostic() {},
    flushClientDiagnostics: async () => {},
    renderPendingImages() {},
    renderPendingFiles() {},
    showOnly() {},
    id: () => String(conversation.messages.length),
    apiJson: async () => ({user: {user_id: "user-a"}}),
    fetch: async () => {
      completionPosts+=1;
      if (scenario.networkFailure) throw new Error("connection lost before headers");
      return {ok: true, body};
    },
    // Exercise the actual lease wrapper declared inside the extracted source.
    serverConversationPayload: row=>({...row,messages:row.messages.map(m=>({...m}))}),
    persistHistorySnapshot(){},
    pullConversationDelta:async()=>{},
    fetchConversationMutation: async (_url, options) => {
      const body=JSON.parse(options.body);
      if(body.plugin_start){
        queued=body.plugin_start;
        leaseOperations.push("enqueue");
        if(scenario.pluginFailure) throw new Error("atomic queue acknowledgement lost");
        if(scenario.cancelDuringClaim) context.state.abortController.abort();
        return {response:{ok:true,status:200},result:{plugin_queued:true,
          conversation:{id:conversation.id,revision:1,updatedAt:conversation.updatedAt}}};
      }
      const operation = body.operation;
      leaseOperations.push(operation);
      return {response:{ok:true},result:{
        state: operation === "claim" || operation === "renew" ? "running" : operation,
        granted:true,server_finalized:operation==="cancel",
      }};
    },
    runAutonomousPluginToolLoop: async (_messages,assistant) => {
      plannerCalls += 1;
      assistant.toolEvents.push({callId:"plugin-call",status:"completed"});
      throw new Error("planner interrupted");
    },
    toast: (message) => messages.push(message),
    $: () => node,
    autoTitle() {},
    saveHistory() {},
    resizeComposer() {},
    renderAll() {},
    renderMessages() {},
    AbortController,
    TextDecoder,
    // The production client now requires the recovery module before sending.
    // Keep the stream-terminal unit test isolated from its separate polling lifecycle.
    window: {
      clearTimeout() {},
      setTimeout() { return 0; },
      setInterval() { return 1; },
      clearInterval() {},
      Chat2APIStreamRecoveryV1: {
        createRequestId: () => "req_request_test",
        create: () => ({resume: async () => ({status: "recovery_timeout", terminal: false})}),
      },
    },
  });
  // Keep the separate native-content sidecar out of this generation-stream test.
  const sendSource = source.slice(start, end).replace("      void streamNativeContent(requestId);\n", "");
  assert.ok(!sendSource.includes("void streamNativeContent(requestId);"));
  const sendMessage = vm.runInContext(sendSource + "\nsendMessage", context);
  await sendMessage("test question");
  // The production client deliberately starts recovery without blocking sendMessage.
  // Let its Promise chain settle before asserting the user-visible recovery notice.
  await new Promise((resolve) => setImmediate(resolve));
  return {
    messages,
    replies: conversation.messages.filter(({role}) => role === "assistant").map(({content}) => content),
    locked: body.locked,
    ...(scenario.networkFailure || pluginScenario ? {
      pending:conversation.messages.find(row=>row.role==="assistant")?.pending,
      status:conversation.messages.find(row=>row.role==="assistant")?.recoveryStatus,
      leaseOperations,
      plannerCalls,
      ...(pluginScenario ? {queued,completionPosts} : {}),
    } : {}),
  };
}

test("reports an in-band server error and preserves any delivered content", async () => {
  const error = 'data: {"error":{"type":"timeout_error","message":"Timed out waiting for ChatGPT"}}';
  for (const replies of [[], ["partial"]]) {
    const chunks = [...replies.map((text) => delta(text) + "\n\n"), error + "\n\n" + terminal + "\n\n"];
    let cancelled = false;
    const result = await send(chunks, false, () => {
      cancelled = true;
      throw new Error("cancellation failed");
    });
    assert.deepEqual(result, {messages: ["请求失败：Timed out waiting for ChatGPT"], replies, locked: false});
    assert.equal(cancelled, true);
  }
});

test("accepts CRLF separators split across chunks and releases the response on DONE", async () => {
  let cancelled = false;
  const result = await send([delta("完整回复") + "\r\n\r", "\n" + terminal + "\r", "\n\r\n"], false, () => {
    cancelled = true;
  });
  assert.deepEqual(result, {messages: [], replies: ["完整回复"], locked: false});
  assert.equal(cancelled, false);
});

test("reports truncation even without content or with an unframed DONE marker", async () => {
  const warning = "连接已结束但未收到流结束标记；请求状态：恢复超时";
  assert.deepEqual(await send([]), {messages: [warning], replies: [""], locked: false});
  assert.deepEqual(await send([delta("partial") + "\n\n" + terminal]),
    {messages: [warning], replies: ["partial"], locked: false});
  assert.deepEqual(await send([terminal + "\n\n"]),
    {messages: ["模型没有返回文本内容。"], replies: [], locked: false});
});


test("does not advance the durable SSE cursor on malformed or empty unknown frames", () => {
  const start = source.indexOf("  function consumeSseFrame(");
  const end = source.indexOf("  function recoveryStatusLabel(",start);
  assert.ok(start >= 0 && end > start);
  const parser = vm.runInNewContext(
    source.slice(start,end) + "\nconsumeSseFrame",
    {
      appendDelta:(assistant,value)=>{assistant.content += String(value || "");},
      limitAssistantText:value=>String(value || "").slice(0,32000),
      Number,JSON,Array,String,Object,
    },
  );
  const assistant = {content:"",lastEventId:7};
  assert.equal(parser("id: 999\nevent: delivery.delta\ndata: {not-json\n\n",assistant),false);
  assert.equal(assistant.lastEventId,7,
    "a malformed JSON frame must not poison the persisted replay cursor");
  assert.equal(parser("id: 888\nevent: unknown.heartbeat\n\n",assistant),false);
  assert.equal(assistant.lastEventId,7,
    "unknown empty frames must not silently skip valid events");
  assert.equal(parser("id: 8\nevent: delivery.delta\ndata: {\"delta\":\"hello\"}\n\n",assistant),false);
  assert.equal(assistant.lastEventId,8);
  assert.equal(assistant.content,"hello");
  assert.equal(parser("id: 9\nevent: cursor\n\n",assistant),false);
  assert.equal(assistant.lastEventId,9,
    "explicit cursor-only heartbeats can safely advance after validation");
  assert.equal(parser(
    "id: 10\nevent: recovery.delta\ndata: " +
      JSON.stringify({delta:" resumed",offset:5,partial_text_unverified:true}) + "\n\n",
    assistant,
  ),false);
  assert.equal(assistant.content,"hello resumed");
  assert.equal(assistant.lastEventId,10);
  assert.equal(parser(
    "id: 10\nevent: recovery.delta\ndata: " +
      JSON.stringify({delta:" resumed",offset:5,partial_text_unverified:true}) + "\n\n",
    assistant,
  ),false);
  assert.equal(assistant.content,"hello resumed",
    "duplicate replay cursors must not append the same Worker delta twice");
  assert.equal(assistant.lastEventId,10);
  const restoredWithoutCursor = {content:"hello resumed",lastEventId:0};
  assert.equal(parser(
    "id: 10\nevent: recovery.delta\ndata: " +
      JSON.stringify({delta:" resumed",offset:5,partial_text_unverified:true}) + "\n\n",
    restoredWithoutCursor,
  ),false);
  assert.equal(restoredWithoutCursor.content,"hello resumed",
    "UTF-16 offsets must prevent duplicate provisional text after cursor loss");
  assert.equal(parser(
    "id: 11\nevent: delivery.delta\ndata: " +
      JSON.stringify({delta:"authoritative final answer"}) + "\n\n",
    assistant,
  ),false);
  assert.equal(assistant.content,"authoritative final answer",
    "the canonical final delivery must replace unverified recovery text");
  assert.equal(assistant.provisionalRecoveryText,false);
  const resetAssistant = {content:"",lastEventId:0};
  assert.equal(parser(
    "id: 13\nevent: replay.reset\ndata: " +
      JSON.stringify({
        status:"running",partial_text:"recovered prefix",
        partial_text_unverified:true,
      }) + "\n\n",
    resetAssistant,
  ),false);
  assert.equal(resetAssistant.content,"recovered prefix");
  assert.equal(resetAssistant.provisionalRecoveryText,true);
  assert.equal(parser("id: 12\ndata: [DONE]\n\n",assistant),true);
  assert.equal(assistant.lastEventId,12);
  const alreadyDelivered = {content:"authoritative answer",lastEventId:5};
  assert.equal(parser(
    "data: " + JSON.stringify({choices:[{delta:{chat2api_event_id:10}}]}) + "\n\n",
    alreadyDelivered,
  ),false);
  assert.equal(alreadyDelivered.lastEventId,10);
  assert.equal(parser(
    "id: 8\nevent: delivery.delta\ndata: " +
      JSON.stringify({delta:"authoritative answer"}) + "\n\n",
    alreadyDelivered,
  ),false);
  assert.equal(alreadyDelivered.content,"authoritative answer",
    "a replay after an idempotent terminal send must not duplicate the final answer");
});

test("preserves request-scoped tool calls and results as safe presentation state", () => {
  const start = source.indexOf("  function appendDelta(");
  const end = source.indexOf("  function recoveryStatusLabel(", start);
  assert.ok(start >= 0 && end > start);
  let saved = 0;
  const document = {
    createElement(tag) {
      return {
        tag, className: "", textContent: "", open: false, children: [],
        appendChild(child) { this.children.push(child); },
        append(...children) { this.children.push(...children); },
      };
    },
  };
  const referenceStart = source.indexOf("  function cleanToolReferenceText(");
  const referenceEnd = source.indexOf("  function normalizeToolEvents(", referenceStart);
  assert.ok(referenceStart >= 0 && referenceEnd > referenceStart);
  const context = vm.createContext({
    saveHistory() { saved += 1; }, document, URL,
    MAX_ASSISTANT_MESSAGE_CHARS:32000,
    ASSISTANT_TRUNCATION_NOTICE:"\n\n[回答超过 Web Chat 的 32,000 字符上限，后续内容未保存]",
    limitAssistantText:value=>String(value || "").slice(0,32000),
    appendAssistantText:(assistant,value)=>{assistant.content=String(assistant.content || "")+String(value || "");},
    safeToolName(value) { return String(value || "tool").slice(0,80); },
  });
  const api = vm.runInContext(
    source.slice(referenceStart, referenceEnd) + "\n" + source.slice(start, end)
      + "\n({applyToolEvent,consumeSseFrame,renderToolActivity})",
    context,
  );
  const assistant = {content: "", toolEvents: []};
  const frame = (tool) => "data: " + JSON.stringify({
    choices: [{delta: {chat2api_tool: tool}}],
  }) + "\n\n";

  assert.equal(api.consumeSseFrame(frame({
    phase: "call", tool_name: "browser.search", call_id: "call-123",
    status: "running", arguments: '{"query":"test"}',
    references: [
      {id:"turn0search0",url:"https://docs.python.org/3/library/",title:"Python docs",snippet:"Official reference"},
      {url:"javascript:alert(1)",title:"Unsafe"},
      {url:"https://127.0.0.1/private",title:"Private"},
    ],
  }), assistant), false);
  assert.equal(assistant.toolEvents.length, 1);
  assert.equal(assistant.toolEvents[0].status, "running");
  assert.deepEqual(JSON.parse(JSON.stringify(assistant.toolEvents[0].references)), [{
    id:"turn0search0",url:"https://docs.python.org/3/library/",title:"Python docs",snippet:"Official reference",
  }]);

  api.consumeSseFrame(frame({
    phase: "call", tool_name: "browser.search", call_id: "call-123",
    status: "completed", arguments: '{"query":"test"}',
  }), assistant);
  api.consumeSseFrame(frame({
    phase: "result", tool_name: "browser.search", call_id: "call-123",
    result_id: "result-123", status: "completed",
    output: "<img src=x onerror=alert(1)>",
  }), assistant);

  assert.equal(assistant.toolEvents.length, 1);
  assert.equal(assistant.toolEvents[0].status, "completed");
  assert.equal(assistant.toolEvents[0].output, "<img src=x onerror=alert(1)>");
  assert.ok(saved >= 3);

  const body = {children: [], appendChild(child) { this.children.push(child); }};
  api.renderToolActivity(body, assistant);
  assert.equal(body.children.length, 1);
  const details = body.children[0].children[0];
  assert.equal(details.tag, "details");
  assert.equal(details.children[0].textContent, "browser.search · 已完成");
  const output = details.children.find(node => node.tag === "pre"
    && node.textContent === "<img src=x onerror=alert(1)>");
  assert.equal(output.textContent, "<img src=x onerror=alert(1)>");
  const referenceBox = details.children.find(node => node.className === "message-tool-references");
  assert.ok(referenceBox);
  const referenceLink = referenceBox.children.find(node => node.tag === "a");
  assert.equal(referenceLink.href, "https://docs.python.org/3/library/");
  assert.equal(referenceLink.children[0].textContent, "[1] Python docs");
  assert.equal(referenceLink.rel, "noopener noreferrer nofollow");
  assert.equal(referenceLink.referrerPolicy, "no-referrer");

  const replayAssistant = {content:"",lastEventId:0,toolEvents:[]};
  assert.equal(api.consumeSseFrame([
    "id: 41", "event: delivery.delta", 'data: {"delta":"replayed"}',
  ].join("\n"), replayAssistant), false);
  assert.equal(replayAssistant.content, "replayed");
  assert.equal(replayAssistant.lastEventId, 41);
  assert.equal(api.consumeSseFrame([
    "id: 42", "event: delivery.completed", 'data: {"text_chars":8}',
  ].join("\n"), replayAssistant), true);
  assert.equal(replayAssistant.replayTerminalStatus, "completed");
  assert.equal(replayAssistant.lastEventId, 42);
});


test("normalizes persisted tool events without accepting unsafe IDs or control characters", () => {
  const start = source.indexOf("  function safeToolName(");
  const end = source.indexOf("  function normalizeConversationRow(", start);
  assert.ok(start >= 0 && end > start);
  const statusStart = source.indexOf("  function normalizeToolStatus(");
  const statusEnd = source.indexOf("  function applyToolEvent(", statusStart);
  assert.ok(statusStart >= 0 && statusEnd > statusStart);
  const normalizeToolStatus = vm.runInContext(
    source.slice(statusStart, statusEnd) + "\nnormalizeToolStatus",
    vm.createContext({}),
  );
  const context = vm.createContext({URL, normalizeToolStatus});
  const normalizeToolEvents = vm.runInContext(
    source.slice(start, end) + "\nnormalizeToolEvents",
    context,
  );
  const rows = normalizeToolEvents([{
    toolName: "browser.search" + String.fromCharCode(10) + "<script>",
    callId: "call-123",
    resultId: "result-123",
    status: "completed",
    arguments: "x".repeat(3000),
    output: "ok",
    references: [
      {url:"https://docs.python.org/3/library/",title:"Python docs",snippet:"Reference"},
      {url:"javascript:alert(1)",title:"Unsafe"},
    ],
  }, {
    toolName: "forged", callId: "bad id", status: "completed",
  }], "assistant");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].toolName.includes(String.fromCharCode(10)), false);
  assert.equal(rows[0].arguments.length, 1200);
  assert.equal(rows[0].references.length, 1);
  assert.equal(rows[0].references[0].url, "https://docs.python.org/3/library/");
  assert.deepEqual(JSON.parse(JSON.stringify(normalizeToolEvents(rows, "user"))), []);
});



test("keeps nonterminal recovery-unavailable turns pending for a later retry", async () => {
  const start = source.indexOf("  async function recoverPendingMessage(");
  const end = source.indexOf("  function assistantMessageForRequest(", start);
  assert.ok(start >= 0 && end > start);

  const conversation = {id:"conversation-recovery",messages:[],updatedAt:""};
  const assistant = {
    id:"message-recovery",role:"assistant",requestId:"req_12345678",
    pending:true,content:"partial",lastEventId:0,
  };
  const context = vm.createContext({
    state:{recoveryInFlight:new Set()},
    MAX_ASSISTANT_MESSAGE_CHARS:32000,
    ASSISTANT_TRUNCATION_NOTICE:"\n\n[回答超过 Web Chat 的 32,000 字符上限，后续内容未保存]",
    limitAssistantText:value=>String(value || "").slice(0,32000),
    currentConversation:() => conversation,
    makeRecoveryController:() => null,
    saveHistory() {},
    renderMessages() {},
    resizeComposer() {},
    applyMessageReferences() {},
  });
  const recover = vm.runInContext(
    source.slice(start,end) + "\nrecoverPendingMessage",
    context,
  );

  const missingTransport = await recover(conversation,assistant);
  assert.equal(missingTransport.status,"recovery_unavailable");
  assert.equal(assistant.pending,true);
  assert.equal(assistant.recoveryStatus,"recovery_unavailable");

  const laterController = {
    replay:async () => ({status:"replay_unavailable",terminal:false}),
    resume:async () => ({status:"recovery_unavailable",terminal:false}),
  };
  const unavailable = await recover(conversation,assistant,laterController);
  assert.equal(unavailable.status,"recovery_unavailable");
  assert.equal(assistant.pending,true);
  assert.equal(assistant.recoveryStatus,"recovery_unavailable");
  assert.equal(context.state.recoveryInFlight.size,0);
});



test("hydrates the authoritative answer snapshot after a replay terminal marker", async () => {
  const start = source.indexOf("  async function recoverPendingMessage(");
  const end = source.indexOf("  function assistantMessageForRequest(", start);
  assert.ok(start >= 0 && end > start);

  const conversation = {id:"conversation-terminal",messages:[],updatedAt:""};
  const assistant = {
    id:"message-terminal",role:"assistant",requestId:"req_87654321",
    pending:true,content:"",lastEventId:0,
  };
  let snapshotCalls = 0;
  const context = vm.createContext({
    state:{recoveryInFlight:new Set()},
    MAX_ASSISTANT_MESSAGE_CHARS:32000,
    ASSISTANT_TRUNCATION_NOTICE:"\n\n[回答超过 Web Chat 的 32,000 字符上限，后续内容未保存]",
    limitAssistantText:value=>String(value || "").slice(0,32000),
    currentConversation:() => conversation,
    makeRecoveryController:() => null,
    saveHistory() {},
    renderMessages() {},
    resizeComposer() {},
    applyMessageReferences() {},
  });
  const recover = vm.runInContext(
    source.slice(start,end) + "\nrecoverPendingMessage",
    context,
  );
  const controller = {
    replay:async () => ({status:"completed",terminal:true,active:false}),
    resume:async () => {
      snapshotCalls += 1;
      return {
        status:"completed",terminal:true,active:false,
        response_text:"完整的最终回复",
        references:[],
      };
    },
  };

  const result = await recover(conversation,assistant,controller);
  assert.equal(snapshotCalls,1);
  assert.equal(result.status,"completed");
  assert.equal(assistant.pending,false);
  assert.equal(assistant.content,"完整的最终回复");
});



test("bounds a stalled same-origin cancellation handshake", async () => {
  const context = vm.createContext({
    window:{},
    AbortController,
    setTimeout,
    clearTimeout,
  });
  const api = vm.runInContext(
    recoverySource + "\nwindow.Chat2APIStreamRecoveryV1",
    context,
  );
  const controller = api.create({
    AbortController,
    cancelTimeoutMs:25,
    fetcher:(_url,options) => new Promise((_resolve,reject) => {
      options.signal.addEventListener("abort",() => {
        const error = new Error("cancel transport timed out");
        error.name = "AbortError";
        reject(error);
      },{once:true});
    }),
  });
  const result = await controller.cancel({
    request_id:"req_cancel_test123",
    assistant_message_id:"message_cancel_123",
  });
  assert.equal(result.status,"cancel_unavailable");
  assert.equal(result.cancel_requested,false);
  assert.equal(result.terminal,false);
});


test("cross-device conversation merge never revives terminal requests from stale pending snapshots", () => {
  const extractFunction = name => {
    const start = source.indexOf("  function " + name + "(");
    assert.ok(start >= 0, "missing " + name);
    const next = source.slice(start + 1).search(/\n  (?:(?:async )?function |class )/);
    return source.slice(start, next < 0 ? source.length : start + 1 + next);
  };
  const context = vm.createContext({
    MAX_MESSAGES:100,
    normalizeToolReferences:value => Array.isArray(value) ? value : [],
  });
  const merge = vm.runInNewContext([
    extractFunction("toolActivityWeight"),
    extractFunction("messageContentWeight"),
    extractFunction("messageBodyWeight"),
    extractFunction("mergeConversation"),
    "mergeConversation",
  ].join("\n"),context);

  const stalled = merge({
    id:"conversation",updatedAt:"2026-11-23T10:00:00.000Z",serverRevision:4,
    messages:[{
      id:"assistant-1",role:"assistant",content:"partial",pending:false,
      recoveryStatus:"stalled",toolEvents:[],createdAt:"2026-11-23T09:00:00.000Z",
    }],
  },{
    id:"conversation",updatedAt:"2026-11-23T10:01:00.000Z",
    messages:[{
      id:"assistant-1",role:"assistant",content:"partial answer recovered from another device",
      pending:true,recoveryStatus:"recovery_pending",
      toolEvents:[{status:"running",arguments:"longer tool state"}],
      createdAt:"2026-11-23T09:00:00.000Z",
    }],
  }).messages[0];
  assert.equal(stalled.pending,false);
  assert.equal(stalled.recoveryStatus,"stalled");
  assert.equal(stalled.content,"partial answer recovered from another device");
  assert.equal(stalled.toolEvents.length,1);

  const cancelled = merge({
    id:"conversation",updatedAt:"2026-11-23T10:01:00.000Z",serverRevision:5,
    messages:[{
      id:"assistant-2",role:"assistant",content:"a longer partial answer from server",
      pending:true,recoveryStatus:"recovery_pending",
      toolEvents:[{status:"completed",output:"authoritative tool result"}],
      createdAt:"2026-11-23T09:02:00.000Z",
    }],
  },{
    id:"conversation",updatedAt:"2026-11-23T10:02:00.000Z",
    messages:[{
      id:"assistant-2",role:"assistant",content:"partial",
      pending:false,recoveryStatus:"cancelled",toolEvents:[],
      createdAt:"2026-11-23T09:02:00.000Z",
    }],
  }).messages[0];
  assert.equal(cancelled.pending,false);
  assert.equal(cancelled.recoveryStatus,"cancelled");
  assert.equal(cancelled.content,"a longer partial answer from server");
  assert.equal(cancelled.toolEvents[0].output,"authoritative tool result");

  const completed = merge({
    id:"conversation",updatedAt:"2026-11-23T10:00:00.000Z",serverRevision:6,
    messages:[{
      id:"assistant-3",role:"assistant",content:"authoritative final answer",
      pending:false,recoveryStatus:"completed",toolEvents:[],
      createdAt:"2026-11-23T09:03:00.000Z",
    }],
  },{
    id:"conversation",updatedAt:"2026-11-23T10:03:00.000Z",
    messages:[{
      id:"assistant-3",role:"assistant",content:"stale pending text that is longer than final",
      pending:true,recoveryStatus:"recovery_pending",
      toolEvents:[{status:"completed",output:"late tool observation"}],
      createdAt:"2026-11-23T09:03:00.000Z",
    }],
  }).messages[0];
  assert.equal(completed.pending,false);
  assert.equal(completed.recoveryStatus,"completed");
  assert.equal(completed.content,"authoritative final answer");
  assert.equal(completed.toolEvents[0].output,"late tool observation");

  const localCompleted = merge({
    id:"conversation",updatedAt:"2026-11-23T10:00:00.000Z",serverRevision:7,
    messages:[{
      id:"assistant-4",role:"assistant",content:"the full server-side partial response is longer",
      pending:false,recoveryStatus:"stalled",toolEvents:[],
      createdAt:"2026-11-23T09:04:00.000Z",
    }],
  },{
    id:"conversation",updatedAt:"2026-11-23T10:04:00.000Z",
    messages:[{
      id:"assistant-4",role:"assistant",content:"short local cache copy",
      pending:false,recoveryStatus:"completed",toolEvents:[],
      createdAt:"2026-11-23T09:04:00.000Z",
    }],
  }).messages[0];
  assert.equal(localCompleted.pending,false);
  assert.equal(localCompleted.recoveryStatus,"completed");
  assert.equal(localCompleted.content,"the full server-side partial response is longer");
});


test("reconnects an interrupted native-content sidecar and receives its terminal marker", async () => {
  const start = source.indexOf("  async function streamNativeContent(");
  const end = source.indexOf("  async function hydrateNativeSnapshots(", start);
  assert.ok(start >= 0 && end > start);

  let fetchCalls = 0, refreshCalls = 0, snapshotFrames = 0;
  const encoder = new TextEncoder();
  let interruptedChunkDelivered = false;
  const interruptedBody = new ReadableStream({
    pull(controller) {
      if (!interruptedChunkDelivered) {
        interruptedChunkDelivered = true;
        controller.enqueue(encoder.encode("event: native.snapshot\ndata: {}\n\n"));
        return;
      }
      controller.error(new Error("simulated network interruption"));
    },
  });
  const terminalBody = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode("event: native.end\ndata: {}\n\n"));
      controller.close();
    },
  });
  const context = vm.createContext({
    state:{nativeStreams:new Map()},
    AbortController, TextDecoder, TextEncoder, ReadableStream,
    encodeURIComponent,
    fetch:async (_url,options) => {
      assert.equal(options.credentials,"same-origin");
      fetchCalls += 1;
      return fetchCalls === 1
        ? {ok:true,status:200,body:interruptedBody}
        : {ok:true,status:200,body:terminalBody};
    },
    consumeNativeSseFrame(frame) {
      if (frame.includes("native.snapshot")) snapshotFrames += 1;
      return frame.includes("event: native.end");
    },
    refreshNativeSnapshot:async () => { refreshCalls += 1; return true; },
    window:{setTimeout(callback) { callback(); return 0; }},
  });
  const stream = vm.runInNewContext(
    source.slice(start,end) + "\nstreamNativeContent",
    context,
  );
  await stream("req_native_reconnect");
  assert.equal(fetchCalls,2);
  assert.equal(snapshotFrames,1);
  assert.ok(refreshCalls >= 2);
  assert.equal(context.state.nativeStreams.size,0);
});

test("does not retry a native-content sidecar after an owner/auth 404", async () => {
  const start = source.indexOf("  async function streamNativeContent(");
  const end = source.indexOf("  async function hydrateNativeSnapshots(", start);
  let fetchCalls = 0;
  const context = vm.createContext({
    state:{nativeStreams:new Map()},
    AbortController, TextDecoder, encodeURIComponent,
    fetch:async () => { fetchCalls += 1; return {ok:false,status:404,body:null}; },
    consumeNativeSseFrame:() => false,
    refreshNativeSnapshot:async () => false,
    window:{setTimeout(callback) { callback(); return 0; }},
  });
  const stream = vm.runInNewContext(source.slice(start,end) + "\nstreamNativeContent",context);
  await stream("req_native_missing");
  assert.equal(fetchCalls,1);
  assert.equal(context.state.nativeStreams.size,0);
});


test("keeps the reader anchored unless the viewport is already near the bottom", () => {
  const start = source.indexOf("  function shouldAutoScroll(");
  const end = source.indexOf("  function renderMessages(", start);
  assert.ok(start >= 0 && end > start);
  const shouldAutoScroll = vm.runInNewContext(
    source.slice(start,end) + "\nshouldAutoScroll",
    vm.createContext({}),
  );
  const viewport = {scrollHeight:1800,scrollTop:220,clientHeight:600};
  assert.equal(shouldAutoScroll(viewport),false);
  viewport.scrollTop = 1110;
  assert.equal(shouldAutoScroll(viewport),true);
  viewport.scrollTop = 1080;
  assert.equal(shouldAutoScroll(viewport),false);
});


test("persists longer assistant responses while keeping user and local-cache bounds", () => {
  const extractFunction = name => {
    const start = source.indexOf("  function " + name + "(");
    assert.ok(start >= 0, "missing " + name);
    const next = source.slice(start + 1).search(/\n  (?:(?:async )?function |class )/);
    return source.slice(start, next < 0 ? source.length : start + 1 + next);
  };
  const context = vm.createContext({
    MAX_ASSISTANT_MESSAGE_CHARS:32000,
    MAX_USER_MESSAGE_CHARS:8000,
    ASSISTANT_TRUNCATION_NOTICE:"\n\n[回答超过 Web Chat 的 32,000 字符上限，后续内容未保存]",
  });
  const helpers = vm.runInNewContext([
    extractFunction("limitAssistantText"),
    extractFunction("appendAssistantText"),
    extractFunction("normalizeMessageContent"),
    extractFunction("appendDelta"),
    "({limitAssistantText,normalizeMessageContent,appendDelta})",
  ].join("\n"),context);

  assert.equal(helpers.normalizeMessageContent("a".repeat(20000),"assistant").length,20000);
  assert.equal(helpers.normalizeMessageContent("u".repeat(9000),"user").length,8000);
  assert.equal(helpers.normalizeMessageContent("a".repeat(20000),"assistant",8000).length,8000);

  const truncated = helpers.limitAssistantText("a".repeat(40000));
  assert.equal(truncated.length,32000);
  assert.ok(truncated.includes("后续内容未保存"));

  const assistant = {content:""};
  helpers.appendDelta(assistant,"a".repeat(20000));
  helpers.appendDelta(assistant,"b".repeat(20000));
  assert.equal(assistant.content.length,32000);
  assert.ok(assistant.content.includes("后续内容未保存"));
});


test("a POST transport failure before headers retains the durable request for recovery", async () => {
  const result = await send([],true,()=>{},{networkFailure:true});
  assert.equal(result.pending,true);
  assert.equal(result.status,"recovery_timeout");
  assert.equal(result.replies.length,1);
});

test("selected plugin sends a single atomic queue mutation and no browser planner or completion", async () => {
  const result = await send([],true,()=>{},{serverPlugin:true});
  assert.equal(result.pending,true);
  assert.equal(result.status,"tool_planning");
  assert.deepEqual(result.leaseOperations,["enqueue"]);
  assert.deepEqual(result.queued.providers,["github"]);
  assert.equal(result.plannerCalls,0);
  assert.equal(result.completionPosts,0);
});

test("lost plugin queue ACK preserves pending turn without replaying it", async () => {
  const result = await send([],true,()=>{},{pluginFailure:true});
  assert.equal(result.pending,true);
  assert.equal(result.status,"tool_planning");
  assert.deepEqual(result.leaseOperations,["enqueue"]);
  assert.equal(result.completionPosts,0);
  assert.match(result.messages.join(" "),/提交状态待确认/);
});

test("stop during atomic queue submission cancels the confirmed server turn", async () => {
  const result = await send([],true,()=>{},{cancelDuringClaim:true});
  assert.equal(result.plannerCalls,0);
  assert.deepEqual(result.leaseOperations,["enqueue","cancel"]);
  assert.equal(result.completionPosts,0);
});


test("replay callbacks deliver new events before advancing the cursor", () => {
  const assistant={id:"message-1",requestId:"req_replay_1234",content:"",lastEventId:0};
  const conversation={id:"conversation-1",messages:[assistant]};
  let handlers;
  const ctx=vm.createContext({
    window:{Chat2APIStreamRecoveryV1:{create:options=>{handlers=options;return {};}}},
    consumeSseFrame:(frame,row)=>{
      const eventId=Number(frame.split("\n")[0].slice(4));
      if(eventId<=row.lastEventId)return false;
      row.lastEventId=eventId;
      row.content+=JSON.parse(frame.split("\n")[2].slice(6)).delta;
      return false;
    },
    saveHistory(){},currentConversation:()=>conversation,renderMessages(){},
  });
  const start=source.indexOf("  function makeRecoveryController(");
  const end=source.indexOf("  async function recoverPendingMessage(",start);
  vm.runInContext(source.slice(start,end)+"\nmakeRecoveryController",ctx)(conversation,assistant);
  const event={request_id:assistant.requestId,assistant_message_id:assistant.id,id:4,event:"delivery.delta",data:{delta:"answer"}};
  handlers.onEvent(event);
  handlers.onEvent(event);
  assert.equal(assistant.content,"answer");
  assert.equal(assistant.lastEventId,4);
});

test("a terminal replay marker without a retrievable final snapshot stays recoverable",async()=>{
  const assistant={id:"message-1",requestId:"req_replay_1234",content:"partial",pending:true,lastEventId:4};
  const conversation={id:"conversation-1",messages:[assistant]};
  const ctx=vm.createContext({state:{recoveryInFlight:new Set()},currentConversation:()=>conversation,
    saveHistory(){},renderMessages(){},resizeComposer(){},
  });
  const start=source.indexOf("  async function recoverPendingMessage(");
  const end=source.indexOf("  function assistantMessageForRequest(",start);
  const run=vm.runInContext(source.slice(start,end)+"\nrecoverPendingMessage",ctx);
  const result=await run(conversation,assistant,{
    replay:async()=>({status:"completed",terminal:true}),
    resume:async()=>({status:"recovery_timeout",terminal:false}),
  });
  assert.equal(result.status,"recovery_unavailable");
  assert.equal(assistant.pending,true);
  assert.equal(assistant.content,"partial");
});


test("saved plugin evidence continuation accepts only completed bounded GitHub previews",()=>{
  const ctx=vm.createContext({normalizeToolReferences:()=>[]});
  const start=source.indexOf("  function buildPluginResumeEvidence(");
  const end=source.indexOf("  async function sendMessage(",start);
  const build=vm.runInContext(source.slice(start,end)+"\nbuildPluginResumeEvidence",ctx);
  const preview=JSON.stringify({tool:"github.list_issues",results:[{number:42}]});
  const message={role:"assistant",pending:false,recoveryStatus:"error",toolEvents:[
    {status:"completed",resultPreview:preview,arguments:"{}"},
    {status:"running",resultPreview:preview},
    {status:"completed",resultPreview:"not JSON"},
    {status:"completed",resultPreview:JSON.stringify({tool:"gmail.get_message",results:[{}]})},
  ]};
  assert.equal(build(message).length,1);
  assert.equal(build(message)[0].preview.results[0].number,42);
  assert.equal(build({...message,pending:true}).length,0);
  assert.equal(build({...message,recoveryStatus:"completed"}).length,0);
});

test("a peer waits for an initial plugin claim before terminalizing a missing lease", async () => {
  let now = 1000, taskState = "missing", saved = 0;
  const message = {id:"message",role:"assistant",pending:true,recoveryStatus:"tool_planning",toolEvents:[]};
  const conversation = {id:"conversation",messages:[message]};
  const state = {user:{user_id:"owner"},remoteSyncReady:true,conversations:[conversation]};
  const context = vm.createContext({
    state, Date:{now:()=>now},
    pluginTaskOperation:async()=>({state:taskState}),
    saveHistory:()=>{saved+=1;}, renderAll:()=>{},
  });
  const start = source.indexOf("  async function reconcileInterruptedPluginTasks(");
  const end = source.indexOf("  function resumePendingMessages(",start);
  assert.ok(start >= 0 && end > start);
  const reconcile = vm.runInContext(source.slice(start,end)+"\nreconcileInterruptedPluginTasks",context);
  await reconcile();
  assert.equal(message.pending,true);
  now += 25000;
  await reconcile();
  assert.equal(message.pending,true);
  taskState = "running";
  await reconcile();
  now += 40000;
  taskState = "missing";
  await reconcile();
  assert.equal(message.pending,true);
  now += 30000;
  await reconcile();
  assert.equal(message.pending,false);
  assert.equal(message.recoveryStatus,"error");
  assert.equal(saved,1);
});

test("interrupted plugin recovery restores a server checkpoint missed by the debounced PUT", async () => {
  const durable = {callId:"plugin-call",resultId:"plugin-result",status:"completed",
    toolName:"GitHub",arguments:"{}",output:"done",resultPreview:'{"tool":"github.list_issues","results":[{"number":42}]}'};
  const message = {id:"message",role:"assistant",pending:true,recoveryStatus:"tool_planning",
    toolEvents:[{callId:"plugin-call",status:"running"}]};
  const conversation = {id:"conversation",messages:[message]};
  const state = {user:{user_id:"owner"},remoteSyncReady:true,conversations:[conversation]};
  let saved = 0;
  const context = vm.createContext({state,
    pluginTaskOperation:async()=>({state:"interrupted",checkpoint:{rounds:1,tool_calls:1,tool_events:[durable]}}),
    normalizeToolEvents:value=>value || [],normalizeToolReferences:value=>value || [],
    saveHistory:()=>{saved+=1;},renderAll(){},
  });
  const start = source.indexOf("  async function reconcileInterruptedPluginTasks(");
  const end = source.indexOf("  function resumePendingMessages(",start);
  const reconcile = vm.runInContext(source.slice(start,end)+"\nreconcileInterruptedPluginTasks",context);
  await reconcile();
  assert.equal(message.pending,false);
  assert.equal(message.toolEvents.length,1);
  assert.equal(message.toolEvents[0].status,"completed");
  assert.equal(message.toolEvents[0].resultPreview,durable.resultPreview);
  assert.equal(saved,1);
});
