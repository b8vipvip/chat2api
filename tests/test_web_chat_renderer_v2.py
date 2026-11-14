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
const window = {};
const navigator = {clipboard: {writeText: async () => {}}};
vm.runInNewContext(src, {window, document, navigator, URL});
const renderer = window.Chat2APISafeRenderer;
assert(renderer);
const host = new Element("root");
const ticks = String.fromCharCode(96).repeat(3);
renderer.renderMarkdown(host,
  "# Heading\n" +
  "<svg onload=alert(1)>raw</svg>\n" +
  "[unsafe](javascript:alert(1)) [safe](https://example.org/x)\n" +
  "**bold** *italic* $x^2$\n" +
  "| a | b |\n| --- | --- |\n| 1 | 2 |\n" +
  ticks + "js\n<img src=x onerror=alert(1)>\n" + ticks + "\n");
const all = node => [node, ...node.children.flatMap(all)];
const nodes = all(host);
assert(!nodes.some(x => ["script", "iframe", "svg", "img", "object", "style"].includes(x.tag)));
assert(!nodes.some(x => x.tag === "a" && !x.href.startsWith("https://")));
assert(nodes.some(x => x.tag === "a" && x.href === "https://example.org/x"));
assert(nodes.some(x => x.tag === "table"));
assert(nodes.some(x => x.tag === "code" && x.className === "math-fallback"));
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
