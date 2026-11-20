import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync("app/chat_client_v1.js", "utf8");
const start = source.indexOf("  function messageContentWeight(");
const end = source.indexOf("  async function syncServerConversations(", start);
assert.ok(start >= 0 && end > start, "conversation merge helpers must remain extractable");
const context = vm.createContext({});
const mergeConversation = vm.runInContext(
  source.slice(start, end) + "\nmergeConversation",
  context,
);

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
