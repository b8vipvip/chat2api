import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const src = readFileSync("chrome_extension/content_request_v6.js","utf8");
const start = src.indexOf("  function assistantOnlyReplyText(");
const end = src.indexOf("\n  async function emit(",start);
assert.ok(start > 0 && end > start);
const ctx = {normalize(value) { return String(value||"")
  .replace(/[\u200B-\u200D\uFEFF]/g,"").replace(/\s+/g," ").trim().normalize("NFC"); }};
vm.runInNewContext(src.slice(start,end)+"\nthis.inspect=assistantOnlyReplyText;",ctx);
const active = {prompt:"Policy header\n\n[User]\nOne-shot request",promptNormalized:ctx.normalize("Policy header\n\n[User]\nOne-shot request")};
const answer = '{"should_learn":false,"action":"skip"}';
for (const padding of ["", "\n\n"]) {
  const transcript = "#### You said:\n\n#### ChatGPT said:\n\n"+padding+answer;
  const outcome=ctx.inspect(active,transcript);
  assert.equal(outcome.text,answer);
  assert.equal(outcome.stripped,true);
  assert.equal(outcome.pending,false);
  assert.equal(outcome.boundaryEvidence,"empty-rendered-user-heading-v226");
}
// A multi-kilobyte user message is a subset of a policy-prefixed final prompt.
// This must not return the entire user transcript just because equality fails.
const echo = "[Instructions]\n"+Array.from({length:55},(_,i)=>
  "line-"+i+" Example seller customer interaction and native rich response content.").join("\n");
const state={prompt:"Policy header\n"+echo+"\nprivate tail",promptNormalized:ctx.normalize("Policy header\n"+echo+"\nprivate tail")};
const part="#### You said:\n\n"+echo+"\n\n#### ChatGPT said:\n\n"+answer;
const valid=ctx.inspect(state,part);
assert.equal(valid.text,answer);
assert.equal(valid.stripped,true);
assert.equal(valid.boundaryEvidence,"three-ordered-echo-fingerprints-v226");
const unverified="#### You said:\n\nAn entirely unrelated transcript with text.\n\n#### ChatGPT said:\n\n"+answer;
assert.equal(ctx.inspect(state,unverified).pending,true);
assert.equal(ctx.inspect(state,unverified).text,"");
assert.equal(ctx.inspect(state,"#### You said:\n\n"+echo).pending,true);
const ordinary="You said:\n\nUnrelated context\n\nChatGPT said:\n\nExample";
assert.equal(ctx.inspect(state,ordinary).text,ordinary);
assert.equal(ctx.inspect(state,answer).text,answer);
console.log("v226 roleless page chrome and correlated prompt-echo boundary passed");
