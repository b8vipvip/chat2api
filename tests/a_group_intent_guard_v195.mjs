import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const ROOT = path.resolve(import.meta.dirname, "..");
const requestSource = fs.readFileSync(path.join(ROOT, "chrome_extension/content_request_v6.js"), "utf8");
const start = requestSource.indexOf("  function requestedUserText(prompt)");
const end = requestSource.indexOf("  function internalToolPayload(value)", start);
assert.ok(start > 0 && end > start);
const intent = vm.runInNewContext(
  requestSource.slice(start, end) + "\n({ isFileCreationRequest, explicitCreationIntent })",
  {},
);
const user = text => `External account-connected apps and plugins are disabled.\n\n[User]\n${text}`;
const handoff = "检查这个仓库 https://github.com/b8vipvip/sumeme.git「请根据仓库现状生成一份完整的项目交接上下文，包含当前目标、已经完成的修改、代码架构、关键文件、已确认的问题、未完成任务和下一步计划，供新聊天直接继续开发。」";
assert.equal(intent.isFileCreationRequest(user(handoff)), false, "A group handoff is text-only");
assert.equal(intent.isFileCreationRequest(user("请生成一份报告，包含关键文件、附件列表和项目结构。")), false);
assert.equal(intent.isFileCreationRequest(user("生成文件清单：列出源码路径和功能")), false);
assert.equal(intent.isFileCreationRequest(user("请生成一个可下载的 PDF 文件。")), true);
assert.equal(intent.isFileCreationRequest(user("创建一份项目交接文件，包含当前架构。")), true);
assert.equal(intent.isFileCreationRequest(user("导出 report.xlsx 并提供下载")), true);
assert.equal(intent.isFileCreationRequest(user("请分析项目关键文件，不要生成附件")), false);

// The *document* is never a connector authorization modal. The old scanner
// scanned <main>, saw its own injected policy in the echoed prompt, and
// dispatched Escape with no valid modal. Assert neither dismiss nor Escape.
const isolationSource = fs.readFileSync(path.join(ROOT, "chrome_extension/content_tool_isolation_v48.js"), "utf8");
let escapes = 0;
let buttonClicks = 0;
class FakeElement {
  constructor(text) { this.innerText = text; }
  getBoundingClientRect() { return {width: 500, height: 500}; }
  getAttribute() { return ""; }
  querySelectorAll(selector) { return selector.includes("button") ? [button] : []; }
  dispatchEvent(event) { if (event.key === "Escape") escapes++; return true; }
  closest() { return null; }
}
const button = new FakeElement("Continue");
button.click = () => buttonClicks++;
const main = new FakeElement("Share You said: External account-connected apps, plugins, connectors, actions, integrations, and their account data are disabled for this API request. Continue");
const document = {
  documentElement: main,
  querySelectorAll: selector => selector === "main" ? [main] : [],
  querySelector: () => null,
  addEventListener: () => {},
  dispatchEvent: event => { if (event.key === "Escape") escapes++; },
};
const sandbox = {
  Element: FakeElement, document,
  getComputedStyle: () => ({display:"block",visibility:"visible",opacity:"1"}),
  chrome: {runtime:{onMessage:{addListener:()=>{}},sendMessage:async()=>{}}},
  setInterval:()=>0, setTimeout:()=>0, clearTimeout:()=>{},
  MutationObserver:class{observe(){}},
  KeyboardEvent:class{constructor(type,init){this.key=init.key}},
  queueMicrotask:()=>{},
};
vm.runInNewContext(isolationSource, sandbox);
const guard=sandbox.__CHAT2API_TOOL_ISOLATION_V48__;
assert.ok(guard);
assert.equal(guard.scan(), 0);
assert.equal(escapes, 0);
assert.equal(buttonClicks, 0);
assert.equal(guard.state.blocked, 0);
console.log("A group text intent and modal isolation regression passed");
