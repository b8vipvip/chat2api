import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const source = readFileSync("app/chat_client_v1.js", "utf8");
const match = source.match(/function mergeConversation\(serverRow, localRow\) \{[\s\S]*?\n  \}/);
assert.ok(match, "production mergeConversation function must remain extractable");

function normalizeMergeReferences(value) {
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
  normalizeToolReferences: normalizeMergeReferences,
  toolActivityWeight:events => Array.isArray(events) ? events.length : 0,
  messageContentWeight:message => String(message?.content || "").length,
  messageBodyWeight:message => String(message?.content || "").length,
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

const repeatedServerRow = {
  id:"conversation-voice-repeat",
  updatedAt:"2026-11-23T02:00:00Z",
  messages:[{
    id:"voice-user-turn-1",role:"user",content:"repeat this phrase",
    createdAt:"2026-11-23T02:00:01Z",
  }],
};
const repeatedLocalRow = {
  ...repeatedServerRow,
  updatedAt:"2026-11-23T02:00:02Z",
  messages:[{
    id:"voice-user-turn-2",role:"user",content:"repeat this phrase",
    createdAt:"2026-11-23T02:00:03Z",
  }],
};
const mergedRepeatedTurns = mergeConversation(repeatedServerRow,repeatedLocalRow);
assert.equal(mergedRepeatedTurns.messages.length,2,
  "cross-device merge must preserve both distinct Voice turns");
assert.equal(mergedRepeatedTurns.messages[0].id,"voice-user-turn-1");
assert.equal(mergedRepeatedTurns.messages[1].id,"voice-user-turn-2");


const citationMatch = source.match(/function citationTextForMessage\(message\) \{[\s\S]*?\n  \}/);
assert.ok(citationMatch, "production citationTextForMessage function must remain extractable");
const citationTextForMessage = runInNewContext("(" + citationMatch[0] + ")", {
  messageText:message => String(message?.content || ""),
  referencesForMessage:message => Array.isArray(message?.references) ? message.references : [],
});
const inlineMarker = "\uE200cite\uE202turn0search0\uE201";
assert.equal(
  citationTextForMessage({content:"Read the docs" + inlineMarker,references:[sourceRef]}),
  "Read the docs [1]",
  "copy/export must replace private citation tokens with readable reference numbers",
);
const secondRef = {
  id:"turn0search1",
  url:"https://developer.mozilla.org/",
  title:"MDN",
  snippet:"",
};
const multiMarker = "\uE200cite\uE202turn0search0\uE202turn0search1\uE201";
assert.equal(
  citationTextForMessage({
    content:"Read both" + multiMarker,
    references:[sourceRef,secondRef],
  }),
  "Read both [1, 2]",
);


const normalizeMatch = source.match(/function normalizeToolReferences\(value\) \{[\s\S]*?\n  \}/);
assert.ok(normalizeMatch, "production normalizeToolReferences function must remain extractable");
const normalizeToolReferences = runInNewContext("(" + normalizeMatch[0] + ")", {
  safeToolReference:item => {
    if (!item || typeof item.url !== "string" || !item.url.startsWith("https://")) return null;
    return {
      id:String(item.id || ""),
      url:item.url,
      title:String(item.title || ""),
      snippet:String(item.snippet || ""),
      ...(Number.isSafeInteger(item.start_index) ? {start_index:item.start_index} : {}),
      ...(Number.isSafeInteger(item.end_index) ? {end_index:item.end_index} : {}),
    };
  },
});
const enriched = normalizeToolReferences([
  {...sourceRef,title:"",snippet:""},
  {...sourceRef,title:"Detailed API reference",snippet:"Updated source description"},
]);
assert.equal(enriched.length,1);
assert.equal(enriched[0].title,"Detailed API reference");
assert.equal(enriched[0].snippet,"Updated source description",
  "a later citation event must enrich an existing reference instead of being discarded");


const persistedPluginEvent = {
  toolName:"GitHub · list_issue_comments",callId:"plugin_1",resultId:"plugin_1_result",
  status:"completed",arguments:"{}",output:"完成",resultPreview:'{"results":[{"body":"review"}]}',
  references:[sourceRef],
};
const eventServerRow = {...serverRow,messages:[{...serverRow.messages[0],pending:false,recoveryStatus:"completed",toolEvents:[persistedPluginEvent]}]};
const eventLocalRow = {...newerLocalRow,messages:[{...newerLocalRow.messages[0],toolEvents:[
  {...persistedPluginEvent,status:"running",resultId:"",output:"",resultPreview:"",references:[]},
  {toolName:"GitHub · list_branches",callId:"plugin_2",resultId:"plugin_2_result",status:"completed",output:"完成"},
]}]};
const combined = mergeConversation(eventServerRow,eventLocalRow).messages[0].toolEvents;
assert.equal(combined.length,2);
assert.equal(combined[0].status,"completed");
assert.equal(combined[0].resultPreview,persistedPluginEvent.resultPreview);
assert.equal(combined[0].references[0].url,sourceRef.url);
assert.equal(combined[1].callId,"plugin_2");
