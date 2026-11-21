"""W-03: renderer module security contract and XSS regression."""
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "app" / "chat_renderer_v2.js"


def test_renderer_uses_dom_allowlist_and_fails_closed():
    source = RENDERER.read_text(encoding="utf-8")
    assert "document.createElement" in source
    assert "document.createTextNode" in source
    assert "textContent" in source
    assert "noopener noreferrer" in source
    assert "referrerPolicy" in source
    for forbidden in ("innerHTML", "insertAdjacentHTML", "eval(", "new Function", "javascript:"):
        assert forbidden not in source
    assert "Chat2APISafeRenderer" in source
    assert "renderRichBlocks" in source


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is needed for the renderer DOM harness")
def test_xss_allowlist_links_math_and_rich_block_fallback():
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
const src = fs.readFileSync(process.argv[1], "utf8");
class Element {
  constructor(tag, value="") {
    this.tag = tag;
    this._value = String(value);
    this.children = [];
  }
  set textContent(value) { this._value = String(value); this.children = []; }
  get textContent() { return this._value + this.children.map(x => x.textContent).join(""); }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { children.forEach(x => this.appendChild(x)); }
  addEventListener() {}
}
const document = {
  baseURI: "https://chat.example/",
  createElement: tag => new Element(tag),
  createTextNode: text => new Element("#text", text)
};
const katexCalls = [];
const window = {katex: {render(tex, target, options) {
  katexCalls.push({tex, options});
  target.appendChild(new Element("span", "rendered:" + tex));
}}};
const navigator = {clipboard: {writeText: async () => {}}};
vm.runInNewContext(src, {window, document, navigator, URL});
const renderer = window.Chat2APISafeRenderer;
assert(renderer);
const host = new Element("root");
const ticks = String.fromCharCode(96).repeat(3);
const twoDollar = String.fromCharCode(36,36);
renderer.renderMarkdown(host,
  "# Heading\n" +
  "<svg onload=alert(1)>raw</svg>\n" +
  "[unsafe](javascript:alert(1)) [safe](https://example.org/x)\n" +
  "**bold** *italic* $x^2$ and \\(y^2\\)\n" +
  twoDollar + "\n\\frac{1}{2}\n" + twoDollar + "\n" +
  "\\[\\sqrt{4}\\]\n" +
  "| a | b |\n| --- | --- |\n| 1 | 2 |\n" +
  ticks + "js\n<img src=x onerror=alert(1)>\n" + ticks + "\n");
const all = node => [node, ...node.children.flatMap(all)];
const nodes = all(host);
assert(!nodes.some(x => ["script", "iframe", "svg", "img", "object", "style"].includes(x.tag)));
assert(!nodes.some(x => x.tag === "a" && !x.href.startsWith("https://")));
assert(nodes.some(x => x.tag === "a" && x.href === "https://example.org/x"));
assert(nodes.some(x => x.tag === "table"));
assert(nodes.some(x => x.tag === "span" && x.className === "math-inline"));
assert(nodes.some(x => x.tag === "div" && x.className === "math-block"));
assert(katexCalls.some(x => x.tex === "x^2" && x.options.displayMode === false));
assert(katexCalls.some(x => x.tex === "y^2" && x.options.displayMode === false));
assert(katexCalls.some(x => x.tex === "\\frac{1}{2}" && x.options.displayMode === true));
assert(katexCalls.some(x => x.tex === "\\sqrt{4}" && x.options.displayMode === true));
assert(katexCalls.every(x => x.options.trust === false && x.options.throwOnError === false
  && x.options.maxExpand === 1000 && x.options.maxSize === 20));
assert(!nodes.some(x => x.className === "math-fallback"));
const citeOpen = String.fromCharCode(0xE200) + "cite" + String.fromCharCode(0xE202);
const citeSep = String.fromCharCode(0xE202);
const citeClose = String.fromCharCode(0xE201);
const citationHost = new Element("root");
renderer.renderMarkdown(citationHost,
  "Sources " + citeOpen + "turn0search0" + citeSep + "turn0search1" + citeClose,
  [
    {id:"turn0search0",url:"https://docs.python.org/3/",title:"Python docs"},
    {id:"turn0search1",url:"https://example.org/second",title:"Second source"},
    {id:"bad<script>",url:"https://evil.example/source",title:"Invalid ID"},
    {id:"turn0http",url:"http://example.org/insecure",title:"Insecure"},
    {id:"turn0private",url:"https://127.0.0.1/private",title:"Private"},
  ]);
const citationNodes = all(citationHost);
const citationLinks = citationNodes.filter(x => x.tag === "a" && x.className === "inline-citation");
assert.deepEqual(citationLinks.map(x => x.href),[
  "https://docs.python.org/3/","https://example.org/second",
]);
assert.deepEqual(citationLinks.map(x => x.textContent),["[1]","[2]"]);
assert.equal(citationLinks[0].title,"Python docs");
assert.equal(citationLinks[0].rel,"noopener noreferrer nofollow");
assert.equal(citationLinks[0].referrerPolicy,"no-referrer");
assert(!citationHost.textContent.includes(citeOpen));
const unresolvedHost = new Element("root");
renderer.renderMarkdown(unresolvedHost,"Missing "+citeOpen+"turn-missing"+citeClose,[]);
assert(all(unresolvedHost).some(x => x.className === "citation-unresolved"));
const unclosedMath = new Element("root");
renderer.renderMarkdown(unclosedMath,twoDollar + "\\nnot closed");
assert(unclosedMath.textContent.includes(twoDollar));
assert(unclosedMath.textContent.includes("not closed"));
const oversizedMath = new Element("root");
renderer.renderRichBlocks(oversizedMath,[{type:"math",tex:"x".repeat(4097)}]);
assert(all(oversizedMath).some(x => x.tag === "pre" && x.className === "math-fallback"));
assert(oversizedMath.textContent.includes("[公式过长，未完整渲染]"));
assert(host.textContent.includes("<svg onload=alert(1)>raw</svg>"));
assert(host.textContent.includes("<img src=x onerror=alert(1)>"));
const rich = new Element("root");
renderer.renderRichBlocks(rich, [
  {type:"tool", status:"<script>"},
  {type:"citation", title:"not safe", url:"javascript:alert(1)"},
  {type:"citation", title:"credential", url:"https://user:pass@evil.example/"},
  {type:"citation", title:"protocol relative", url:"//evil.example/"},
  {type:"citation", title:"data URL", url:"data:text/html,<script>alert(1)</script>"},
  {type:"citation", title:"safe HTTP", url:"http://example.org/source"},
  {type:"image", file_id:"file_87654321", alt:"chart", url:"https://evil.example/image.png"},
  {type:"image", file_id:"../../etc/passwd"},
  {type:"file", file_id:"file_12345678", filename:"report.pdf"},
  {type:"file", file_id:"../../etc/passwd", filename:"bad.html"},
  {type:"unknown"},
  {type:"math", tex:"\\frac{1}{2}"},
]);
const rnodes = all(rich);
assert(!rnodes.some(x => ["script", "iframe"].includes(x.tag)));
assert(rnodes.some(x => x.tag === "img" && x.src === "/api/user/files/file_87654321/content"));
assert(!rnodes.some(x => x.tag === "img" && x.src === "https://evil.example/image.png"));
assert(!rnodes.some(x => x.tag === "img" && x.src.includes("../")));
assert(rnodes.some(x => x.tag === "a" && x.href === "/api/user/chat/files/file_12345678/download"));
assert(rnodes.some(x => x.tag === "a" && x.href === "http://example.org/source"));
assert(!rnodes.some(x => x.tag === "a" && (
  x.href.startsWith("javascript:") || x.href.startsWith("data:") ||
  x.href.includes("evil.example") || x.href.includes("user:pass") || x.href.includes("../")
)));
assert(rich.textContent.includes("report.pdf"));
assert(rich.textContent.includes("unknown"));
assert(rich.textContent.includes("文件附件：缺少有效文件引用"));
assert(rich.textContent.includes("图片附件：缺少有效文件引用"));
assert(rich.textContent.includes("此内容暂不支持安全预览"));
assert(katexCalls.some(x => x.tex === "\\frac{1}{2}" && x.options.displayMode === true));
const fallbackWindow = {};
vm.runInNewContext(src, {window:fallbackWindow, document, navigator, URL});
const fallback = new Element("root");
fallbackWindow.Chat2APISafeRenderer.renderRichBlocks(fallback,[{type:"math",tex:"x+y"}]);
assert(all(fallback).some(x => x.tag === "pre" && x.className === "math-fallback"));
const empty = new Element("root");
renderer.renderMarkdown(empty, "");
renderer.renderRichBlocks(empty, null);
console.log("renderer-harness-passed");
"""
    result = subprocess.run(
        ["node", "-e", harness, str(RENDERER)],
        check=False, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "renderer-harness-passed" in result.stdout