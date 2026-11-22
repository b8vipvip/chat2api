import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source = readFileSync("app/chat_client_v1.js", "utf8");
const match = source.match(/function mergeConversation\(serverRow, localRow\) \{[\s\S]*?\n  \}/);
assert.ok(match, "production mergeConversation function must remain extractable");

function normalizeToolReferences(value) {
  if (!Array.isArray(value)) return [];
  const seen = new Set();
  const rows = [];
  for (const item of value) {
    if (!item || typeof item !== "object" || typeof item.url !== "string"
        || !item.url.startsWith("https://")) continue;
    const id = String(item.id || "");
    const key = id ? "id:" + id : "url:" + item.url;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({id,url:item.url,title:String(item.title || ""),snippet:String(item.snippet || "")});
  }
  return rows;
}
const mergeConversation = runInNewContext("(" + match[0] + ")", {
  normalizeToolReferences,
  toolActivityWeight:events => Array.isArray(events) ? events.length : 0,
  messageContentWeight:message => String(message?.content || "").length,
  MAX_MESSAGES:100,
});

const sourceRef = {
  id:"turn0search0",
  url:"https://docs.example.org/api",
  title:"API reference",
  snippet:"Official API docs",
};
const serverRow = {
  id:"conversation-1",
  title:"Conversation",
  updatedAt:"2026-11-22T10:00:00Z",
  messages:[{
    id:"assistant-1",role:"assistant",content:"Answer",pending:true,
    recoveryStatus:"recovering",references:[sourceRef],
  }],
};
const newerLocalRow = {
  ...serverRow,
  updatedAt:"2026-11-22T10:01:00Z",
  messages:[{
    id:"assistant-1",role:"assistant",
    content:"A longer locally recovered final answer",
    pending:false,recoveryStatus:"completed",
  }],
};
const merged = mergeConversation(serverRow,newerLocalRow);
assert.equal(merged.messages[0].content,newerLocalRow.messages[0].content);
assert.deepEqual(merged.messages[0].references,[sourceRef],
  "a longer local answer must not erase citations already stored on the server");

const localReferenceRow = {
  ...serverRow,
  messages:[{
    id:"assistant-1",role:"assistant",content:"Answer",pending:false,
    recoveryStatus:"completed",references:[sourceRef],
  }],
};
const serverWithoutReferences = {
  ...serverRow,
  messages:[{
    id:"assistant-1",role:"assistant",content:"Answer",pending:false,
    recoveryStatus:"completed",
  }],
};
assert.deepEqual(
  mergeConversation(serverWithoutReferences,localReferenceRow).messages[0].references,
  [sourceRef],
  "locally captured citations must survive a server snapshot that has not synced them yet",
);

console.log("Web Chat conversation merge preserves final-message citations across devices");
