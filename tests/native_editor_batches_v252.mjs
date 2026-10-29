import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source=readFileSync("chrome_extension/content_request_v6.js","utf8");
const start=source.indexOf("  async function setComposerTextBatched(active, element, text) {");
const end=source.indexOf("  async function writePrompt(active, prompt) {",start);
assert.ok(start>0&&end>start,"bounded native controller exists");

function harness({failAt=0,advance=0}={}) {
  let n=0, replace=true,now=0,editor=null;
  const writes=[],diagnostics=[];
  const selection={removeAllRanges(){},addRange() {replace=true;}};
  const context={
    HTMLTextAreaElement:class {},HTMLInputElement:class {},
    window:{getSelection(){return selection;}},
    document:{
      createRange(){return {selectNodeContents(){}};},
      execCommand(_cmd,_ui,text){
        n++;if(failAt===n)return false;
        writes.push(String(text));
        editor.textContent=(replace?"":editor.textContent)+text;
        replace=false;now+=advance;return true;
      },
    },
    performance:{now(){return now;}},
    delay:async()=>{},
    composerText:e=>e.textContent,
    promptMatchesText:(observed,active)=>observed===active.promptNormalized,
    setComposerText:(e,t)=>{e.textContent=t;writes.push("legacy");},
    diagnostic:async(_active,_stage,detail)=>{diagnostics.push(detail);},
  };
  vm.runInNewContext(source.slice(start,end)+"\nthis.batched=setComposerTextBatched",context);
  editor={textContent:"historic",isConnected:true,focus(){}};
  return {write:context.batched,editor,writes,diagnostics,count:()=>n};
}
test("33K Unicode prompt is sent as bounded native transactions, never DOM assignment",async()=>{
  const h=harness();
  const prompt="汉".repeat(2047)+"😀"+"测试数据\n".repeat(5000);
  const result=await h.write({promptNormalized:prompt},h.editor,prompt);
  assert.equal(h.editor.textContent,prompt);
  assert.ok(result.chunks>10);
  assert.ok(h.writes.every(v=>v.length<=2048));
  assert.equal(h.writes.join(""),prompt);
  assert.equal(h.diagnostics[0].prompt_batched_native_verified,true);
});
test("an unsuccessful native chunk fails closed; a partial draft is never sent",async()=>{
  const h=harness({failAt:3}), prompt="长".repeat(33000);
  await assert.rejects(()=>h.write({promptNormalized:prompt},h.editor,prompt),/rejected a long prompt batch/);
  assert.ok(h.editor.textContent.length<prompt.length);
  assert.equal(h.diagnostics.length,0);
});
test("native editor over 90 seconds fails closed, rather than waiting until the 900s lease",async()=>{
  const h=harness({advance:50000}),prompt="长".repeat(16000);
  await assert.rejects(()=>h.write({promptNormalized:prompt},h.editor,prompt),/90-second insertion budget/);
  assert.equal(h.diagnostics.length,0);
});
test("short prompts remain under the original input controller",async()=>{
  const h=harness(),prompt="hello";
  const result=await h.write({promptNormalized:prompt},h.editor,prompt);
  assert.equal(result.method,"single-native");
  assert.deepEqual(h.writes,["legacy"]);
  assert.equal(h.editor.textContent,prompt);
});
