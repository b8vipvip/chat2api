import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source = readFileSync("chrome_extension/content_request_v6.js", "utf8");
function section(start, end) {
  const a = source.indexOf(start);
  const b = source.indexOf(end, a + start.length);
  assert.ok(a >= 0 && b > a, "missing controller contract: " + start);
  return source.slice(a, b);
}
const context = {globalThis: {}};
const evidence = section("  const normalize = value =>", "  function visible(element)") +
  section("  function editorLayoutWhitespaceEvidence(observed, target, source) {", "  function promptDriftSummary(text, active) {") +
  section("  function promptMatchEvidence(text, active) {", "  function promptMatchesText(text, active) {");
vm.runInNewContext(evidence + "\nglobalThis.probe = {normalize,promptMatchEvidence};", context);
const {normalize,promptMatchEvidence} = context.globalThis.probe;
const active = {promptNormalized: normalize("Café")};
assert.equal(promptMatchEvidence("Cafe\u0301", active), "exact",
  "canonically equivalent Unicode must not fail the draft check");
assert.equal(promptMatchEvidence("Café!", active), "none",
  "a genuine extra character must not be silently submitted");
assert.equal(promptMatchEvidence("Caé", active), "none",
  "a genuinely missing character must not be silently submitted");
const multiline = "Project policy ".repeat(42) + "\n" + "帮我生成小红花的头像图片";
const verified = {prompt:multiline,promptNormalized:normalize(multiline)};
const layout = verified.promptNormalized.replace("小红花", "小红 花");
assert.equal(promptMatchEvidence(layout, verified), "rich-editor-layout-whitespace",
  "ordinary-length rich drafts must not fail on one proven layout separator");
assert.equal(promptMatchEvidence(verified.promptNormalized.replace("小红花", "小红帽"), verified), "none",
  "native rehydration must never accept changed semantic characters");

let draft = "original-mutated!";
const editor = {isConnected:true};
const rewriteContext = {
  globalThis:{},
  composerCandidates: () => [editor],
  composerText: () => draft,
  setComposerText: (_element,value) => { draft = value; },
  waitFor: async fn => fn(),
  promptMatchesComposer: () => draft === "original",
};
const rewrite = section("  async function rewriteDriftedDraft(active, editor) {", "  async function submitAndConfirm(active) {");
vm.runInNewContext(rewrite + "\nglobalThis.rewrite = rewriteDriftedDraft;",rewriteContext);
assert.equal(await rewriteContext.globalThis.rewrite({prompt:"original"},editor),true);
assert.equal(draft,"original");
rewriteContext.setComposerText = (_element,value) => { draft=value ? value+"!" : ""; };
assert.equal(await rewriteContext.globalThis.rewrite({prompt:"original"},editor),false,
  "a failed native rewrite must not authorize an unverified send");
assert.match(source,/native_rewrite_verified: verified/);
assert.match(source,/send_prompt_repair_attempts: promptRepairAttempts/);
console.log("request-v6 normalized Unicode and bounded native draft rewrite checks passed");
