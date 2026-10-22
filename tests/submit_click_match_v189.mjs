import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

// Compile the *real* v6 submitter, with a deterministic fake Lexical editor.
// React clears the editor synchronously in button.click(): diagnostics must
// retain the match that authorized that click, never read an empty draft after.
const source = readFileSync("chrome_extension/content_request_v6.js", "utf8");
const begin = source.indexOf("  async function submitAndConfirm(active) {");
const end = source.indexOf("  async function updateCapturedText(active, text, previous) {", begin);
assert.ok(begin >= 0 && end > begin);
const controller = source.slice(begin, end);

let text = "exact prompt";
let clickCount = 0;
const traces = [];
const editor = {isConnected:true, textContent:"exact prompt"};
const button = {
  scrollIntoView(){},
  click() { clickCount++; text = ""; editor.textContent = ""; },
};
const ctx = {
  globalThis:{}, Date, Math, String, Number, Boolean, performance:{now:()=>123},
  composerHoldingPrompt:()=>editor, findComposer:()=>editor,
  sendButton:()=>button,
  buttonReady:()=>true,
  promptMatchesComposer:(node, active)=>text===active.promptNormalized,
  promptMatchEvidence:(value, active)=>value===active.promptNormalized?"exact":"none",
  promptDriftSummary:()=>({send_prompt_drift_kind:"content_changed"}),
  composerText:()=>text,
  normalize:value=>String(value || "").trim(),
  refreshAssistantBaseline:active=>{active.baselineCount=0;},
  diagnostic:async (_active, stage, details)=>{traces.push({stage, details});},
  waitFor:async predicate=>predicate(),
  isGenerating:()=>false,
  composerCandidates:()=>[editor],
  rewriteDriftedDraft:async()=>false,
  waitAfterSend:async()=>({reason:"current-user-turn-visible"}),
  delay:async()=>{},
};
vm.runInNewContext(controller + "\nglobalThis.submit = submitAndConfirm;", ctx);
const active = {
  cancelled:false, options:{timeout_seconds:60}, prompt:"exact prompt",
  promptNormalized:"exact prompt", promptComposer:editor,
  requestId:"req_success", attachmentCount:0,
};
await ctx.globalThis.submit(active);
assert.equal(clickCount, 1);
assert.equal(text, "");
const clicked = traces.find(item=>item.stage==="clicked");
assert.ok(clicked);
assert.equal(clicked.details.send_prompt_match, "exact",
  "post-click editor clear must not overwrite pre-click evidence with none");
assert.equal(clicked.details.send_prompt_match_source, "rendered-composer");
assert.equal(traces.filter(item=>item.stage==="confirmed").length, 1);

// A prompt that never matches is not allowed to click merely because Send is
// enabled. This safeguards against accepting arbitrary one-character changes.
text="mutated prompt";editor.textContent=text;traces.length=0;
await assert.rejects(ctx.globalThis.submit({...active,requestId:"req_mismatch"}), /ChatGPT send button did not become ready/);
assert.equal(clickCount, 1);
assert.ok(traces.some(item=>item.stage==="send-readiness-failed"));
console.log("v6 pre-click prompt verification survives synchronous editor clear and refuses altered drafts");
