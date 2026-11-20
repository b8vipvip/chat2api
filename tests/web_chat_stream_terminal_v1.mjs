import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync("app/chat_client_v1.js", "utf8");
const start = source.indexOf("  function appendDelta(");
const end = source.indexOf("  async function selectApiKey(", start);
assert.ok(start >= 0 && end > start);

const delta = (text) => "data: " + JSON.stringify({choices: [{delta: {content: text}}]});
const terminal = "data: [DONE]";

async function send(chunks, close = true, onCancel = () => {}) {
  const conversation = {id: "conversation-test", messages: []};
  const messages = [];
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
    state: {
      apiToken: "test-token", user: {user_id: "user-a"},
      selectedModel: "test-model", streaming: false,
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
    renderPendingImages() {},
    renderPendingFiles() {},
    showOnly() {},
    id: () => String(conversation.messages.length),
    apiJson: async () => ({user: {user_id: "user-a"}}),
    fetch: async () => ({ok: true, body}),
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
  const context = vm.createContext({saveHistory() { saved += 1; }, document});
  const api = vm.runInContext(
    source.slice(start, end) + "\n({applyToolEvent,consumeSseFrame,renderToolActivity})",
    context,
  );
  const assistant = {content: "", toolEvents: []};
  const frame = (tool) => "data: " + JSON.stringify({
    choices: [{delta: {chat2api_tool: tool}}],
  }) + "\n\n";

  assert.equal(api.consumeSseFrame(frame({
    phase: "call", tool_name: "browser.search", call_id: "call-123",
    status: "running", arguments: '{"query":"test"}',
  }), assistant), false);
  assert.equal(assistant.toolEvents.length, 1);
  assert.equal(assistant.toolEvents[0].status, "running");

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
  const output = details.children.find(node => node.tag === "pre");
  assert.equal(output.textContent, "<img src=x onerror=alert(1)>");
});
