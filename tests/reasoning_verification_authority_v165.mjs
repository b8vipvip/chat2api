import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

// A visible "medium" control is not sufficient if the independent page
// verifier reports that the applied state is untrusted.
const source = fs.readFileSync(new URL("../chrome_extension/content_reasoning_v7.js", import.meta.url), "utf8");
let listener;
const control = {innerText:"Medium"};
const page = {
  normalizedLabelLower: value => String(value || "").trim().toLowerCase(),
  labelOf: element => element?.innerText || "",
  reasoningControl: () => ({element:control, reasoning:"medium"}),
};
const verification = {ok:false,code:"reasoning_state_untrusted",requested_reasoning:"medium"};
const driver = {
  attachVerification: data => ({...data,verification,verification_warning:verification.code}),
  classifyReasoningError: error => ({
    code:error?.code || "reasoning_selection_failed",
    message:String(error?.message || error),
    verification:error?.verification || verification,
  }),
};
const ctx = {
  console, setTimeout, clearTimeout,
  chrome:{runtime:{onMessage:{addListener:fn => {listener=fn;}}}},
  __CHAT2API_PAGE_ADAPTER_V22__:page,
  __CHAT2API_PAGE_DRIVER_V22__:driver,
};
ctx.globalThis=ctx;
vm.createContext(ctx);
vm.runInContext(source,ctx);
assert.equal(typeof listener,"function");
const response = await new Promise(resolve => {
  listener({type:"chat2api.reasoning.prepare.v7",reasoning_level:"medium"},null,resolve);
});
assert.equal(response.ok,false,"untrusted verification must fail closed");
assert.equal(response.code,"reasoning_state_untrusted");
assert.equal(response.diagnostics.verification.ok,false);
console.log("Reasoning Driver disagreement cannot be recorded as success");
