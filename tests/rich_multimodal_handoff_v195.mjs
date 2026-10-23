import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const richSource = readFileSync("chrome_extension/content_rich_response_v69.js", "utf8");
const controllerSource = readFileSync("chrome_extension/content_request_v6.js", "utf8");

class TextNode {
  constructor(value) { this.nodeType = 3; this.nodeValue = value; }
  cloneNode() { return new TextNode(this.nodeValue); }
}
class ElementNode {
  constructor(tag, attrs = {}, children = []) {
    this.nodeType = 1;
    this.tagName = tag.toUpperCase();
    this.attrs = { ...attrs };
    this.childNodes = children;
    this.children = children.filter(child => child instanceof ElementNode);
    this.className = attrs.class || "";
    this.textContent = children.map(child => child.nodeValue || child.textContent || "").join("");
  }
  getAttribute(name) { return this.attrs[name] || null; }
  setAttribute(name, val) { this.attrs[name] = String(val); }
  getBoundingClientRect() { return { width: 300, height: 200 }; }
  matches() { return false; }
  querySelector() { return null; }
  querySelectorAll(selector) {
    if (selector === ":scope > th, :scope > td") {
      return this.children.filter(child => ["TH", "TD"].includes(child.tagName));
    }
    if (selector === "tr") {
      const matches = [];
      const visit = node => {
        for (const child of node.children || []) {
          if (child.tagName === "TR") matches.push(child);
          visit(child);
        }
      };
      visit(this);
      return matches;
    }
    return [];
  }
  cloneNode(deep) {
    return new ElementNode(this.tagName, this.attrs,
      deep ? this.childNodes.map(child => child.cloneNode(true)) : []);
  }
}
const T = value => new TextNode(value);
const E = (tag, ...children) => new ElementNode(tag, {}, children);
const ctx = {
  Element: ElementNode, Node: { TEXT_NODE: 3 },
  document: { createTextNode: T },
  getComputedStyle: () => ({ display: "block", visibility: "visible" }),
};
runInNewContext(richSource, ctx);
const rich = ctx.__CHAT2API_RICH_RESPONSE_V69__;
const paragraph = E("div", T("开头 "), E("strong", T("加粗")), T(" 后面 "),
  E("a", T("链接")), T(" 结尾"), E("p", T("下一段")));
const output = rich.extractMarkdown(paragraph);
assert.equal(output, "开头 **加粗** 后面 链接 结尾\n\n下一段");
assert.ok(!output.includes("开头\n\n"), "inline segments may not become paragraphs");

const table = E("table", E("tr", E("th", T("字段")), E("th", T("值"))),
  E("tr", E("td", T("类型")), E("td", E("strong", T("重要")))));
const tableText = rich.extractMarkdown(table);
assert.ok(tableText.includes("| 类型 | **重要** |"), "table cells must preserve inline formatting");

const findFunction = (start, end) => {
  const begin = controllerSource.indexOf("  function " + start + "(");
  const finish = controllerSource.indexOf("  function " + end + "(", begin);
  assert.ok(begin >= 0 && finish > begin);
  return runInNewContext(controllerSource.slice(begin, finish) + "\n" + start + ";");
};
const toolArgs = findFunction("isImageToolArguments", "hasTransferableImage");
assert.equal(toolArgs('{"size":"1024x1536","n":3,"transparent_background":false}'), true);
assert.equal(toolArgs('{"size":"1024x1536","n":3,"transparent_background":false,"text":"hello"}'), false);
assert.equal(toolArgs('{"size":"1024x1536","n":"three"}'), false);
assert.equal(toolArgs("这是正常的 JSON 文档"), false);
const intentStart = controllerSource.indexOf("  function requestedUserText(prompt) {");
const intentEnd = controllerSource.indexOf("  function isLocalImageBuildCommand(value) {");
const intentHelpers = controllerSource.slice(intentStart, intentEnd);
const imageIntent = runInNewContext(intentHelpers + "\n isImageCreationRequest;");
assert.equal(imageIntent("帮我生成视频提示词和3张参考图。"), true);
assert.equal(imageIntent("我想分析图片生成函数"), false);

assert.match(controllerSource, /imageToolArguments[\s\S]*?\|\| visibleTurnMedia \|\| visibleTurnFiles \|\| fileCreationRequested \|\| pageMediaFound/);
assert.match(controllerSource, /networkTerminalAt && networkTerminalText && !captureMedia[\s\S]*?!isImageToolArguments\(networkTerminalText\)/);
assert.match(controllerSource, /response_terminal_source: "verified-request-media-dom-v176"/);
console.log("Rich Markdown inline grouping, table formatting and image-tool handoff regression passed");
