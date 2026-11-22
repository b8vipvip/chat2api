import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

// Exercise the exact production metadata sanitizer. These three roots are
// already supported by the server-side citation normalizer; the Worker must
// not discard them before the event reaches the API.
const source = readFileSync("chrome_extension/native_tool_stream_main_v63.js", "utf8");
const match = source.match(/function safeToolMetadata\(metadata\) \{[\s\S]*?\n  \}/);
assert.ok(match, "production safeToolMetadata function must remain extractable");
const safeToolMetadata = runInNewContext("(" + match[0] + ")");

const metadata = {
  search_results: [{title: "Search result", url: "https://search.example.org/a"}],
  references: [{title: "Reference", href: "https://docs.example.org/reference"}],
  sources: [{source_name: "Source", safe_url: "https://source.example.org/item"}],
  citations: [{title: "Citation", url: "https://cite.example.org/item"}],
  content_references: [{title: "Content reference", url: "https://content.example.org/item"}],
  search_result_groups: [{entries: [{title: "Grouped result", url: "https://group.example.org/item"}]}],
  annotations: [{type:"url_citation",url_citation:{url:"https://annotation.example.org/item"}}],
  citation_annotations: [{url:"https://citation-annotation.example.org/item"}],
  url_citation: {url:"https://url-citation.example.org/item"},
  web_search_results: [{url:"https://web-search.example.org/item"}],
  source_references: [{url:"https://source-reference.example.org/item"}],
  tool_references: [{url:"https://tool-reference.example.org/item"}],
  unrelated: {url: "https://must-not-forward.example.org/private"},
};
const forwarded = JSON.parse(JSON.stringify(safeToolMetadata(metadata)));

assert.deepEqual(forwarded, {
  search_result_groups: metadata.search_result_groups,
  search_results: metadata.search_results,
  citations: metadata.citations,
  content_references: metadata.content_references,
  references: metadata.references,
  sources: metadata.sources,
  annotations: metadata.annotations,
  citation_annotations: metadata.citation_annotations,
  url_citation: metadata.url_citation,
  web_search_results: metadata.web_search_results,
  source_references: metadata.source_references,
  tool_references: metadata.tool_references,
});
assert.deepEqual(JSON.parse(JSON.stringify(safeToolMetadata(null))), {});
console.log("Native tool metadata forwards every server-supported citation root and excludes unrelated metadata");


// Final assistant messages can carry citation metadata independently of tool events.
const finalMatch = source.match(/function maybeEmitFinal\(message\) \{[\s\S]*?\n  \}/);
assert.ok(finalMatch, "production maybeEmitFinal function must remain extractable");
const finalEvents = [];
const finalState = {final_snapshots:0, final_completions:0};
const finalEmitted = new Map();
const maybeEmitFinal = runInNewContext("(" + finalMatch[0] + ")", {
  state: finalState,
  emitted: finalEmitted,
  contentText: value => typeof value === "string" ? value : (value?.text || ""),
  safeToolMetadata,
  post: value => finalEvents.push(value),
});
maybeEmitFinal({
  id:"assistant-final-1",
  author:{role:"assistant"},
  channel:"final",
  status:"completed",
  content:"The API is documented here.",
  metadata:{annotations:[{
    type:"url_citation",
    start_index:0,
    end_index:26,
    url_citation:{url:"https://docs.example.org/api",title:"API reference",ref_id:"turn0search0"},
  }]},
});
assert.equal(finalEvents.length, 2);
assert.equal(finalEvents[0].phase, "final-snapshot");
assert.equal(finalEvents[1].phase, "final-complete");
assert.equal(finalEvents[1].metadata.annotations[0].url_citation.ref_id, "turn0search0");


// The isolated content bridge stores the final metadata on the active
// request before forwarding the auxiliary references event. request-v6 can
// then attach the same metadata to its sole canonical chat.completed event.
const contentSource = readFileSync("chrome_extension/content_native_tool_stream_v63.js", "utf8");
const bridgeMatch = contentSource.match(/async function emitFinalReferences\(detail, requestId\) \{[\s\S]*?\n  \}/);
assert.ok(bridgeMatch, "production final-reference bridge must remain extractable");
const bridgeEvents = [];
const activeRequestState = {requestId:"req-citation-1"};
const bridgeState = {finalReferenceSignatures:new Map()};
const emitFinalReferences = runInNewContext("(" + bridgeMatch[0] + ")", {
  state: bridgeState,
  globalThis: {__CHAT2API_REQUEST_CONTENT_V6__:{active:activeRequestState}},
  cleanMetadata: value => value,
  emit: async value => { bridgeEvents.push(value); return true; },
});
await emitFinalReferences({
  message_id:"assistant-final-1",
  text:"The API is documented here.",
  metadata:{annotations:[{
    type:"url_citation",
    url_citation:{url:"https://docs.example.org/api",title:"API reference",ref_id:"turn0search0"},
  }]},
}, "req-citation-1");
assert.equal(activeRequestState.nativeCitationMetadata.annotations.length, 1);
assert.equal(activeRequestState.nativeCitationText, "The API is documented here.");
assert.equal(bridgeEvents.length, 1);
assert.equal(bridgeEvents[0].type, "chat.references");
assert.equal(bridgeEvents[0].request_id, "req-citation-1");

const requestSource = readFileSync("chrome_extension/content_request_v6.js", "utf8");
assert.match(
  requestSource,
  /event\?\.type === "chat\.completed" && active\?\.nativeCitationMetadata/,
  "request-v6 canonical terminal must merge the captured citation metadata",
);
