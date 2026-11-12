"""W-03: renderer asset ownership and no-HTML execution regression."""
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.user_console_v104_patch import install_user_console_v104_patch


ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "app" / "chat_renderer_v2.js"


def test_safe_renderer_is_loaded_before_chat_and_has_restricted_csp(tmp_path):
    app = create_app(Settings(CHAT2API_API_KEY="renderer-test-key", CHAT2API_DATA_DIR=str(tmp_path)))
    install_user_console_v104_patch(app)
    with TestClient(app) as client:
        page = client.get("/chat")
        assert page.status_code == 200
        assert page.text.index("chat2api-safe-renderer-v2.js") < page.text.index("chat2api-chat-client-v1.js")
        assert "unsafe-inline" not in page.headers["content-security-policy"]
        asset = client.get("/assets/chat2api-safe-renderer-v2.js")
        assert asset.status_code == 200
        assert asset.headers["x-content-type-options"] == "nosniff"
        assert "no-store" in asset.headers["cache-control"]
        assert "Chat2APISafeRenderer" in asset.text
        chat_script = client.get("/assets/chat2api-chat-client-v1.js").text
        assert "Chat2APISafeRenderer.renderMarkdown" in chat_script


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is needed for the renderer DOM harness")
def test_xss_allowlist_links_math_and_rich_block_fallback():
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
const src = fs.readFileSync(process.argv[1], "utf8");
const created = [];
class Element {
  constructor(tag, value="") {
    this.tag = tag;
    this._value = String(value);
    this.children = [];
    this.attributes = {};
    created.push(tag);
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
  {type:"image", file_id:"file_unsafe"},
  {type:"file", name:"../../../etc/passwd"},
  {type:"unknown"},
  {type:"math", tex:"\\frac{1}{2}"},
]);
const rnodes = all(rich);
assert(!rnodes.some(x => ["script", "iframe", "img"].includes(x.tag)));
assert(!rnodes.some(x => x.tag === "a"));
assert(rich.textContent.includes("unknown"));
assert(rich.textContent.includes("需授权的文件预览"));
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
