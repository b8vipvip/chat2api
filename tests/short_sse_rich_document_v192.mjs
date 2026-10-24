import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const source = readFileSync("chrome_extension/content_request_v6.js", "utf8");
const start = source.indexOf("  async function reconcileResponsesTerminal(active, networkText) {");
const end = source.indexOf("  async function monitor(active) {", start);
assert.ok(start !== -1 && end > start, "single terminal owner must contain the reconciler");
const richReply = "# 晨雾咖啡商业计划\\n" + "这里包含预算表、人员安排、商业预测与行动计划。\\n".repeat(40);

async function caseRun({bytes, rich, available}) {
  let clock = 0;
  const fakeDate = { now: () => clock };
  const context = {
    Date: fakeDate,
    delay: async ms => { clock += ms; },
    isGenerating: () => false,
    promptStillPresent: () => false,
    internalToolPayload: () => false,
    currentAssistantState: () => available ? {
      isNew: true, latest: {}, reason: "verified-rendered-dom-baseline-v191",
    } : { isNew: false, latest: null, reason: "none" },
    finalNodeText: async () => ({ text: rich }),
    completeResponsesBridgeDocument: () => false,
    normalize: v => v,
  };
  const reconcile = runInNewContext(source.slice(start, end) +
    "\\nreconcileResponsesTerminal", context);
  return await reconcile({
    networkTerminalBytes: bytes,
    options: {response_protocol: "chat-completions"},
    cancelled: false,
  }, "以下是一");
}

const complete = await caseRun({bytes:131185,rich:richReply,available:true});
assert.equal(complete.source, "verified-short-sse-rich-dom-v192");
assert.equal(complete.text, richReply);
const missing = await caseRun({bytes:131185,rich:"",available:false});
assert.equal(missing.source,"incomplete-short-sse-rich-reply-v192");
assert.equal(missing.text,"");
const shortLegitimate = await caseRun({bytes:1200,rich:"",available:false});
assert.equal(shortLegitimate.text,"以下是一");
assert.match(source,/if \(!reconciled.text\) \{/);
assert.match(source,/response_integrity_guard: "incomplete-short-sse-rich-reply-v192"/);
console.log("v192: 4-character SSE is never accepted as a 131KB rich reply");
