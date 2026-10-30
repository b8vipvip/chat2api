import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
const src=readFileSync("chrome_extension/content_request_v6.js","utf8");
const a=src.indexOf("  async function writePrompt(active, prompt) {");
const b=src.indexOf("  // The ChatGPT rich editor can add a layout separator",a);
assert.ok(a>0 && b>a);
const fn=src.slice(a,b);
function harness({candidate=true,rebound=false}={}) {
 const notes=[],editor={textContent:"",isConnected:true},newEditor={textContent:"",isConnected:true};
 let writes=0;
 const ctx={
  normalize:x=>String(x||"").trim(),
  ensureComposer:async()=>editor,
  composerCandidates:()=>candidate?[rebound?newEditor:editor]:[],
  composerText:e=>e?.textContent||"",
  promptMatchesComposer:(e,active)=>e?.textContent===active.promptNormalized,
  setComposerTextBatched:async(_active,e,text)=>{writes++;e.textContent=text;
     if(rebound)newEditor.textContent=text;
     return {method:"bounded-native-insertText",chunks:17,elapsed_ms:33186};},
  waitFor:async fn=>fn(),
  diagnostic:async(_active,stage,details)=>notes.push({stage,details}),
  delay:async()=>{},
 };
 vm.runInNewContext(fn+"\nthis.writePrompt=writePrompt;",ctx);
 return {run:ctx.writePrompt,editor,newEditor,notes,writes:()=>writes};
}
test("ec46: verified 34K native input without a bound composer never replays 17 chunks",async()=>{
 const h=harness({candidate:false}), prompt="汉".repeat(34027);
 await assert.rejects(()=>h.run({promptNormalized:prompt},prompt),/not bound/);
 assert.equal(h.writes(),1);
 assert.equal(h.notes.at(-1).stage,"prompt-native-verified-but-unbound");
 assert.equal(h.notes.at(-1).details.prompt_reinsert_skipped,true);
 assert.equal(h.notes.at(-1).details.prompt_expected_chars,34027);
});
test("long draft already in current editor proceeds after hydration match",async()=>{
 const h=harness(),prompt="x".repeat(34027);
 const result=await h.run({promptNormalized:prompt},prompt);
 assert.equal(h.writes(),1);
 assert.equal(result.composer,h.editor);
 assert.equal(h.notes.at(-1).stage,"prompt-ready");
});
test("Lexical rebind to another active composer is accepted without second insert",async()=>{
 const h=harness({rebound:true}),prompt="x".repeat(34027);
 const result=await h.run({promptNormalized:prompt},prompt);
 assert.equal(result.composer,h.newEditor);
 assert.equal(h.writes(),1);
 assert.equal(h.notes.at(-1).details.prompt_editor_rebound,true);
});
test("no blind full-draft repair after initial prompt-ready for 34K characters",()=>{
 const a=src.indexOf("  async function submitAndConfirm(active) {");
 const b=src.indexOf("  async function updateCapturedText(active, text, previous) {",a);
 const body=src.slice(a,b);
 assert.match(body,/active\.prompt\.length >= 8192/);
 assert.match(body,/send_revalidation_revision: 254/);
 assert.match(body,/ready = await waitFor\(probe, 8500, 150\)/);
 assert.match(body,/&& active\.prompt\.length < 8192\) \{/);
 assert.match(body,/send_long_prompt_rewrite_blocked/);
 assert.match(body,/throw new Error\("ChatGPT send button did not become ready/);
});
