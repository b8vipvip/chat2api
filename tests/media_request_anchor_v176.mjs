import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const richSource = readFileSync("chrome_extension/content_rich_response_v69.js", "utf8");
const controllerSource = readFileSync("chrome_extension/content_request_v6.js", "utf8");
const inlineData = "data:image/png;base64,YWJj";

class MockElement {
  constructor(tag = "div", source = "", order = 0, area = "chat") {
    this.tagName = tag.toUpperCase();
    this.nodeType = 1;
    this.attrs = new Map(source ? [["src", source]] : []);
    this.currentSrc = source;
    this.order = order;
    this.area = area;
    this.childNodes = [];
    this.children = [];
    this.isConnected = true;
  }
  getAttribute(name) { return this.attrs.get(name) ?? null; }
  setAttribute(name, value) { this.attrs.set(name, String(value)); this.currentSrc = this.attrs.get("src") || ""; }
  removeAttribute(name) { this.attrs.delete(name); }
  getBoundingClientRect() { return { width: 200, height: 200 }; }
  matches() { return false; }
  closest(selector) {
    if (this.area === "sidebar" && selector.includes("aside")) return this;
    if (this.area === "user" && selector.includes("data-message-author-role='user'")) return this;
    if (selector.includes("article[data-testid")) return this.area === "user"
      ? { getAttribute: () => "user", querySelector: () => null } : null;
    return null;
  }
  querySelectorAll(selector) {
    if (selector !== "img") return [];
    const result = [];
    function visit(node) {
      for (const child of node.children) {
        if (child.tagName === "IMG") result.push(child);
        visit(child);
      }
    }
    visit(this);
    return result;
  }
  appendChild(child) { this.childNodes.push(child); this.children.push(child); return child; }
  compareDocumentPosition(other) { return other.order > this.order ? 4 : 0; }
  cloneNode(deep) {
    const copy = new MockElement(this.tagName, this.getAttribute("src") || "", this.order, this.area);
    if (deep) for (const child of this.children) copy.appendChild(child.cloneNode(true));
    return copy;
  }
}
let elements = [];
const document = {
  querySelectorAll: () => elements,
  createElement: tag => new MockElement(tag),
  createTextNode: text => ({ nodeType: 3, nodeValue: text }),
};
const sandbox = {
  Element: MockElement, Node: { TEXT_NODE: 3, DOCUMENT_POSITION_FOLLOWING: 4 },
  document, URL, getComputedStyle: () => ({ display: "block", visibility: "visible" }),
  AbortController, setTimeout, clearTimeout,
};
runInNewContext(richSource, sandbox);
const rich = sandbox.__CHAT2API_RICH_RESPONSE_V69__;
assert.ok(rich, "rich-image extractor must initialize");

const old = new MockElement("img", "https://media.oaiusercontent.com/old.png", 1);
elements = [old];
const baseline = rich.snapshotPageMedia();
assert.equal(baseline.count, 1);

const rehydratedOld = new MockElement("img", old.currentSrc, 1);
const userUpload = new MockElement("img", inlineData, 4, "user");
const sidebar = new MockElement("img", "https://media.oaiusercontent.com/sidebar.png", 5, "sidebar");
const foreignImage = new MockElement("img", "https://evil.example/track.png", 6);
const generated = new MockElement("img", inlineData, 7);
elements = [rehydratedOld, userUpload, sidebar, foreignImage, generated];
const result = rich.locateNewPageMedia(baseline);
assert.equal(result.candidate_count_document, 2, "ignore old-untrusted, sidebar and user-upload images");
assert.equal(result.new_image_candidate_count, 1);
assert.equal(result.images[0], generated, "the new assistant image must be selected");

const currentUser = new MockElement("div", "", 5);
const anchored = rich.locateNewPageMedia(baseline, currentUser);
assert.equal(anchored.after_user_count, 1);
assert.equal(anchored.anchor, "after-current-user");
const laterUser = new MockElement("div", "", 9);
const beforeUser = rich.locateNewPageMedia(baseline, laterUser);
assert.equal(beforeUser.images.length, 0, "do not claim an image before a verified new user turn");

const captured = await rich.capturePageImages(result.images);
assert.equal(captured.image_embedded_count, 1);
assert.match(captured.text, /^!\[图片\]\(data:image\/png;base64,YWJj\)$/);

const empty = rich.locateNewPageMedia(baseline, null);
assert.equal(empty.images.length, 1);
elements = [new MockElement("img", old.currentSrc, 10)];
assert.equal(rich.locateNewPageMedia(baseline).images.length, 0,
  "rehydrated old image URL must not count as new");

assert.match(controllerSource, /active\.baselinePageMedia = rich\?\.snapshotPageMedia\?\.\(\)/);
assert.match(controllerSource, /freshUserAnchor/);
assert.match(controllerSource, /rich\?\.locateNewPageMedia\?\./);
assert.match(controllerSource, /await rich\.capturePageImages\(sourceMedia\)/);
assert.match(controllerSource, /media-awaiting-image-anchor/);
assert.match(controllerSource, /media-new-image-card-found/);
assert.match(controllerSource, /response_terminal_source: "verified-request-media-dom-v176"/);
assert.equal((controllerSource.match(/type: "chat\.completed"/g) || []).length >= 2, true);
console.log("Request-scoped media fallback, old-image exclusion and detached image capture passed");
