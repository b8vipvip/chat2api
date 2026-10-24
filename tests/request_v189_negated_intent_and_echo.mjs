import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const owner = readFileSync("chrome_extension/content_request_v6.js", "utf8");
const begin = owner.indexOf("  function requestedUserText(prompt) {");
const end = owner.indexOf("  function isLocalImageBuildCommand(value) {", begin);
assert.ok(begin >= 0 && end > begin);
const api = runInNewContext(owner.slice(begin, end)
  + "\n({isImageCreationRequest, isFileCreationRequest, explicitCreationIntent, internalToolPayload})",
{ isInternalMediaHandoff: s => /"skipped_mainline"/.test(s),
  isImageToolArguments: s => /"size"\s*:/.test(s),
  isLocalImageBuildCommand: s => /from PIL import Image/.test(s) });

const A = "请生成一份月球基地方案，包括Mermaid流程图。\n10. 不要生成图片，不要生成附件，全部使用Markdown。";
const B = "请生成 3 张彼此独立的图片，不要合并成一张大拼图。\n在图片之外单独输出文本。";
const C = "请为品牌生成一套可下载的交付文件。\n生成 PDF 文件和 DOCX 文件。\n不只是伪造文件名。";
const D = [
  "请基于本对话前面已经生成的内容继续处理，不要重新从零开始。",
  "1. 请列出前面已经生成过的内容：",
  "- 图片类内容", "- 文件类内容",
  "2. 列出文件的准确名称和类型。",
  "6. 不要重新生成新的图片或新的文件。",
  "7. 只使用普通文本回答。",
].join("\n");
// Reproduce the exact A-group negation that v0.22.189 misclassified.
const realA = "这是一次严格的富文本测试，不需要生成图片、附件或可下载文件。请直接在聊天正文中完成全部内容。";
const realD = "本轮只需回顾已有信息，不要重新生成任何图片、文档、CSV 文件或其它附件。";
assert.equal(api.isFileCreationRequest(realA), false, "A must not await downloadable artifacts");
assert.equal(api.isImageCreationRequest(realA), false);
assert.equal(api.isFileCreationRequest(realD), false, "D2 must not create files");
assert.equal(api.isImageCreationRequest(realD), false);
assert.equal(api.isImageCreationRequest(A), false);
assert.equal(api.isFileCreationRequest(A), false);
assert.equal(api.isImageCreationRequest(B), true);
assert.equal(api.isFileCreationRequest(C), true);
assert.equal(api.isFileCreationRequest(D), false);
assert.equal(api.isImageCreationRequest(D), false);
assert.equal(api.internalToolPayload('{"query":"搜索历史生成的文件"}'), true);
assert.equal(api.internalToolPayload('bash -lc cat /home/oai/skills/pdfs/SKILL.md'), true);
assert.equal(api.internalToolPayload('{"name":"sample","query":"keyword"}'), false);
assert.equal(api.internalToolPayload('# Normal reply\n\nSome content'), false);
assert.match(owner, /renderedNetworkCorrelatedTurn\(active\)/);
assert.match(owner, /renderedCurrentRequestCandidate\(active\)/);
assert.match(owner, /same-request-rendered-dom-baseline-v191/);
assert.match(owner, /baseline\.get\(node\) === text\.slice\(-5000\)/);
assert.match(owner, /baselineRenderedText = new Map/);
assert.match(owner, /same-request-network-rendered-dom-v189/);
assert.match(owner, /!internalToolPayload\(networkTerminalText\)/);
console.log("v189 regression: negated A/D, valid B/C, tool echo and correlated final passed");
