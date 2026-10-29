import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";

const code=readFileSync("chrome_extension/content_request_v6.js","utf8");
const start=code.indexOf("  function setComposerText(element, text) {");
const end=code.indexOf("  function labelOf(element) {",start);
assert.ok(start>0&&end>start);
const inputEvents=[],nativeCalls=[];
let editor=null,accept=true;
class FakeTextArea {}
class FakeInput {}
class FakeEvent {constructor(type,details={}){this.type=type;this.details=details;}}
const context={
  HTMLTextAreaElement:FakeTextArea,HTMLInputElement:FakeInput,
  Event:FakeEvent,InputEvent:FakeEvent,
  window:{getSelection:()=>({removeAllRanges(){},addRange(){}})},
  document:{
    createRange:()=>({selectNodeContents(){}}),
    execCommand(command,_u,text){
      nativeCalls.push({command,length:String(text||"").length});
      if(accept) editor.textContent=String(text||"");
      return accept;
    },
    createElement:()=>({textContent:""}),
  },
  normalize:v=>String(v||"").trim(),
};
runInNewContext(code.slice(start,end)+"\nthis.fill=setComposerText;",context);
editor={
  textContent:"",focus(){},
  dispatchEvent:e=>inputEvents.push(e.type),
  replaceChildren(node){this.textContent=node?.textContent||"";},
};
context.fill(editor,"长".repeat(33954));
assert.equal(editor.textContent.length,33954);
assert.equal(nativeCalls.length,1);
assert.deepEqual(inputEvents,[],"native 34K insert must not receive duplicate synthetic input");
context.fill(editor,"短回复");
assert.deepEqual(inputEvents,["input"],"short edits keep their existing path");
accept=false;
assert.throws(()=>context.fill(editor,"长".repeat(33954)),/native editor rejected/);
console.log("Native 34K editor insertion uses one browser event, short/failure behavior preserved");
