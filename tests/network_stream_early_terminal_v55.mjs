import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { TextDecoder, TextEncoder } from "node:util";

const source = fs.readFileSync(new URL("../chrome_extension/network_stream_main_v55.js", import.meta.url), "utf8");
const encoder = new TextEncoder();
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

async function testCase({ protocolEnd = false, assistantEndTurn = false, readerFailure = false } = {}) {
  const messages = [];
  const parts = [
    'data: "v1"\n\n',
    'data: {"p":"","o":"add","v":{"message":{"author":{"role":"assistant"},"content":{"parts":["Hello"]},"status":"in_progress"}}}\n\n',
    ...(assistantEndTurn ? [
      'data: {"p":"/message/status","o":"replace","v":"finished_successfully"}\n\n',
      'data: {"p":"/message/end_turn","o":"add","v":true}\n\n'
    ] : []),
    ...(protocolEnd ? ['data: {"type":"message_stream_complete"}\n\n'] : [])
  ].map(x => encoder.encode(x));
  let cursor = 0;
  const reader = {
    async read() {
      if (cursor < parts.length) return { done: false, value: parts[cursor++] };
      if (readerFailure) throw new Error("simulated-clone-reader-failure");
      return { done: true };
    },
    releaseLock() {}
  };
  const response = {
    ok: true, status: 200, body: {},
    headers: { get: () => "text/event-stream" },
    clone: () => ({ body: { getReader: () => reader } })
  };
  const ctx = {
    TextDecoder, structuredClone, URL, Date, Math, setTimeout,
    location: { href: "https://chatgpt.com/" },
    document: { documentElement: { setAttribute() {} } },
    postMessage: data => messages.push(data),
    fetch: async () => response
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  await ctx.fetch("https://chatgpt.com/backend-api/f/conversation", { method: "POST" });
  for (let i = 0; i < 20; i++) await tick();
  return messages;
}

for (const options of [
  { protocolEnd: true, readerFailure: true },
  { assistantEndTurn: true, readerFailure: true },
  { protocolEnd: true, readerFailure: false }
]) {
  const frames = await testCase(options);
  const terminals = frames.filter(frame => frame.phase === "assistant-complete");
  assert.equal(terminals.length, 1, "completion must be emitted once even if clone fails after terminal");
  assert.equal(terminals[0].text, "Hello");
  assert.equal(terminals[0].completion_hint, true);
  assert.notEqual(terminals[0].terminal_evidence_source, "stream-eof");
}
const broken = await testCase({ readerFailure: true });
assert.equal(broken.filter(frame => frame.phase === "assistant-complete").length, 0,
  "truncated streams without successful terminal evidence must not be treated as complete");
assert.ok(broken.some(frame => frame.phase === "error" && /simulated-clone-reader-failure/.test(frame.error)));
console.log("Early SSE proof survives clone reader error; a truncated stream is not completed");
