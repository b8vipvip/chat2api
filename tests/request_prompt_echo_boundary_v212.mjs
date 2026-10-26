import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync("chrome_extension/content_request_v6.js", "utf8");
const start = source.indexOf("  function assistantOnlyReplyText(");
const end = source.indexOf("\n  async function emit(", start);
assert.ok(start > 0 && end > start, "single Worker output boundary is present");
const context = {
  normalize(value) {
    return String(value || "").replace(/[\u200B-\u200D\uFEFF]/g, "")
      .replace(/\s+/g, " ").trim().normalize("NFC");
  }
};
vm.runInNewContext(source.slice(start, end) + "\nthis.inspect = assistantOnlyReplyText;", context);
const inspect = context.inspect;

const prompt = "External account-connected apps are disabled.\n\n[User]\n请交付真实的 XLSX、PPTX、JSON 和 ZIP 文件。";
const active = { promptNormalized: context.normalize(prompt) };
const assistant = "# 交付成果\n\n[📎 budget.xlsx](https://example.invalid/generated/a)\n\n答复完成。";
const transcript = "#### You said:\n\n" + prompt + "\n\n#### ChatGPT said:\n\n" + assistant;
const result = inspect(active, transcript);
assert.equal(result.text, assistant, "assistant answer and links remain intact");
assert.equal(result.stripped, true, "matching user input is removed");
assert.equal(result.pending, false, "verified answer is deliverable");

const partial = inspect(active, "#### You said:\n\n" + prompt.slice(0, 45));
assert.equal(partial.pending, true, "partial echoed prompt must not leak as a delta");
assert.equal(partial.text, "");
const submittedOnly = inspect(active, "#### You said:\n\n" + prompt);
assert.equal(submittedOnly.pending, true, "user-only surface must not become a completion");
const unusualWhitespace = inspect(active, "You said:\n\n" + prompt.replace("\n\n", "   ") + "\n\nChatGPT said:\n\n" + assistant);
assert.equal(unusualWhitespace.text, assistant, "rendered whitespace can vary");
const authorTranscript = "You said:\n\nHere's an example\n\nChatGPT said:\n\nSome example output";
const genuine = inspect(active, authorTranscript);
assert.equal(genuine.text, authorTranscript, "unrelated user-authored transcript stays intact");
assert.equal(genuine.stripped, false);
assert.equal(inspect(active, assistant).text, assistant, "normal assistant body passes unchanged");
assert.match(source, /const bounded = assistantOnlyReplyText\(active, text\)/, "deltas use sanitized full candidate");
assert.match(source, /if \(active && \["chat\.completed", "chat\.snapshot"\]\.includes\(event\?\.type\)\)/, "terminal and snapshots are filtered at request-v6 egress");
assert.match(source, /response_boundary_prompt_echo_removed: true/, "diagnostics attribute the trim");
assert.doesNotMatch(source, /\.replace\(\/\^#### You said:/, "old adjacent-heading-only trim removed");
console.log("request prompt echo boundary v212 passed");
