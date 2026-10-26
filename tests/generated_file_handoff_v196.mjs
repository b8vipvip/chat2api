import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const controller = readFileSync("chrome_extension/content_request_v6.js", "utf8");
const rich = readFileSync("chrome_extension/content_rich_response_v69.js", "utf8");
const main = readFileSync("app/main.py", "utf8");

function helper(name, following) {
  const start = controller.indexOf("  function " + name + "(");
  const stop = controller.indexOf("  function " + following + "(", start + 8);
  assert.ok(start >= 0 && stop > start, "missing helper " + name);
  return runInNewContext(controller.slice(start, stop) + "\n" + name + ";");
}
const hasTransferableFile = helper("hasTransferableFile", "isFileCreationRequest");
const intentStart = controller.indexOf("  function requestedUserText(prompt) {");
const intentEnd = controller.indexOf("  function hasTransferableImage(value) {");
const intentHelpers = controller.slice(intentStart, intentEnd);
const isFileCreationRequest = runInNewContext(intentHelpers + "\n isFileCreationRequest;");
const file = "![📎 demo.pdf](data:application/pdf;base64,JVBERi0xLjQK)";
assert.equal(hasTransferableFile(file), true);
assert.equal(hasTransferableFile("[📎 demo.pdf](https://chatgpt.com/blob)"), false);
assert.equal(hasTransferableFile("![📎 demo.pdf](blob:https://chatgpt.com/123)"), false);
assert.equal(isFileCreationRequest("请帮我生成docx文件并提供下载"), true);
assert.equal(isFileCreationRequest("给我解释如何生成docx文件"), false);
assert.equal(isFileCreationRequest("我要组织未来农场创新展，请提供完整执行计划并实际交付4份独立可下载文件：（1）包含计算公式的XLSX工作簿；（2）不少于8页的PPTX演示稿；（3）有效JSON文件；（4）ZIP压缩包。不能把伪链接当成文件交付。"), true);
assert.equal(isFileCreationRequest("请不要交付文件，只要介绍方法"), false);
assert.equal(isFileCreationRequest("请解释如何交付xlsx文件"), false);
assert.match(controller, /const visibleTurnFiles = Boolean\(sameTurnRoot/);
assert.match(controller, /const deliveredFiles = Number\(finalMedia\?\.file_inlined_count/);
assert.match(controller, /response_generated_file_count: deliveredFiles/);
assert.match(rich, /const FILE_MAX_BYTES = 6 \* 1024 \* 1024/);
assert.match(rich, /const FILE_MAX_TOTAL = 12 \* 1024 \* 1024/);
assert.match(rich, /const FILE_MAX_COUNT = 4/);
assert.match(rich, /method: "GET", redirect: "error"/);
assert.match(rich, /credentials: uri\.origin === location\.origin \? "include" : "omit"/);
assert.match(rich, /hasCandidateFile,/);
assert.match(rich, /file_inlined_count: fileStats.count/);
assert.match(rich, /!\[📎 /);
assert.match(main, /archive_final_markdown, raw_final/);
assert.match(main, /archive_final_markdown, text/);
assert.match(main, /meta\["generated_files"\] = archived_files/);
assert.match(main, /meta\['generated_files'\] = archived_files/);
console.log("File handoff, scoped artifact extraction, and both completion modes verified");
