import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const controller = readFileSync("chrome_extension/content_request_v6.js", "utf8");
const rich = readFileSync("chrome_extension/content_rich_response_v69.js", "utf8");
const start = controller.indexOf("  function isInternalMediaHandoff(value) {");
const end = controller.indexOf("  async function reconcileResponsesTerminal(", start);
assert.ok(start >= 0 && end > start, "media terminal classifier must exist");

const sandbox = {};
runInNewContext(
  controller.slice(start, end) +
    "\nglobalThis.classify = { isInternalMediaHandoff, hasTransferableImage };",
  sandbox,
);
const { isInternalMediaHandoff, hasTransferableImage } = sandbox.classify;
assert.equal(isInternalMediaHandoff('{"skipped_mainline":true}'), true);
assert.equal(isInternalMediaHandoff(' { "skipped_mainline" : true } '), true);
assert.equal(isInternalMediaHandoff('{"skipped_mainline":false}'), false);
assert.equal(isInternalMediaHandoff('{"skipped_mainline":true,"response":"ok"}'), false);
assert.equal(isInternalMediaHandoff("normal image result"), false);
assert.equal(hasTransferableImage("![dog](data:image/png;base64,AAAA)"), true);
assert.equal(hasTransferableImage("Here is ![dog](https://images.example/image.png)"), true);
assert.equal(hasTransferableImage("![dog](blob:https://chatgpt.com/uuid)"), false);
assert.equal(hasTransferableImage('{"skipped_mainline":true}'), false);

// Only the existing request controller may settle the request. A handoff is
// excluded from both network-text completion and the early no-terminal error.
assert.match(controller, /if \(networkText && !isInternalMediaHandoff\(networkText\)\)/);
assert.match(controller, /networkTerminalText && !imageHandoff && !promptStillPresent/);
assert.match(controller, /!networkTerminalAt && !imageHandoff/);
assert.match(controller, /!generating && !imageHandoff && !active\.networkStreamSeen/);
assert.match(controller, /response_terminal_source: "verified-same-turn-media-dom-v174"/);
assert.match(controller, /imageHandoffObserved[\s\S]*internal_tool_handoff_without_media: true/);
assert.match(rich, /mediaOutsideMarkdown/);
assert.match(rich, /response\.headers\.get\("content-type"\)/);
console.log("Same-turn image handoff regression checks passed");
