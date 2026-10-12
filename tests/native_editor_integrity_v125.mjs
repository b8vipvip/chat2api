import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../chrome_extension/content_request_terminal_prompt_v88.js", import.meta.url), "utf8");
const captured = [];
let nativeCalls = 0;
let directMutations = 0;
class FakeHTMLElement {
  constructor() { this.isContentEditable = true; }
  replaceChildren() { directMutations += 1; throw Error("editor DOM must not be replaced"); }
}
const editable = new FakeHTMLElement();
const ctx = {
  document: {activeElement: editable, execCommand(command, _show, value) {
    assert.equal(command,"insertText");
    assert.equal(value.length,6197);
    nativeCalls += 1;
    return true;
  }},
  HTMLElement: FakeHTMLElement,
  performance: {now:() => 8.5},
  queueMicrotask,
  chrome: {runtime:{sendMessage:async value=>captured.push(value)}},
  __CHAT2API_REQUEST_CONTENT_V5__: {active:{requestId:"req_native_editor"}},
};
ctx.globalThis=ctx;
vm.createContext(ctx);
vm.runInContext(source,ctx);
assert.equal(ctx.document.execCommand("insertText",false,"测".repeat(6197)), true);
for (let i=0;i<4;i++) await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(nativeCalls,1);
assert.equal(directMutations,0);
assert.ok(captured.some(x=>x.event?.diagnostics?.prompt_fast_insert_method==="native-editor-transaction-v125"));
console.log("Long prompt retains browser native editor transaction and preserves React/Lexical ownership");
