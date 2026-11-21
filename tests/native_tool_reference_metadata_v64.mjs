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
  unrelated: {url: "https://must-not-forward.example.org/private"},
};
const forwarded = JSON.parse(JSON.stringify(safeToolMetadata(metadata)));

assert.deepEqual(forwarded, {
  search_model_queries: undefined,
  search_result_groups: metadata.search_result_groups,
  search_results: metadata.search_results,
  citations: metadata.citations,
  content_references: metadata.content_references,
  references: metadata.references,
  sources: metadata.sources,
  tool_icons: undefined,
  reasoning_title: undefined,
  reasoning_status: undefined,
});
assert.deepEqual(JSON.parse(JSON.stringify(safeToolMetadata(null))), {});
console.log("Native tool metadata forwards every server-supported citation root and excludes unrelated metadata");
