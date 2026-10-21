import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const richSource = readFileSync("chrome_extension/content_rich_response_v69.js", "utf8");
const controllerSource = readFileSync("chrome_extension/content_request_v6.js", "utf8");
const playgroundSource = readFileSync("app/admin_playground_chat_v69.js", "utf8");
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
    this.renderWidth = 200;
    this.renderHeight = 200;
    this.naturalWidth = 0;
    this.naturalHeight = 0;
    this.complete = false;
  }
  getAttribute(name) { return this.attrs.get(name) ?? null; }
  setAttribute(name, value) { this.attrs.set(name, String(value)); this.currentSrc = this.attrs.get("src") || ""; }
  removeAttribute(name) { this.attrs.delete(name); }
  getBoundingClientRect() { return { width: this.renderWidth, height: this.renderHeight }; }
  matches() { return false; }
  closest(selector) {
    if (this.area === "sidebar" && selector.includes("aside")) return this;
    if (this.area === "user" && selector.includes("data-message-author-role='user'")) return this;
    if (this.area === "chat" && selector.includes("main,[role='main']")) return this;
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
assert.match(controllerSource, /await rich\.capturePageImages\(sourceMedia, \{allowIntrinsic: intrinsicHandoff\}\)/);
assert.match(controllerSource, /media-awaiting-image-anchor/);
assert.match(controllerSource, /media-new-image-card-found/);
assert.match(controllerSource, /response_terminal_source: "verified-request-media-dom-v176"/);
assert.equal((controllerSource.match(/type: "chat\.completed"/g) || []).length >= 2, true);

// Real TX03 evidence: a 1254x1254 loaded blob: image is inside main but
// its layout rectangle is <96px (probe visible_large_images=0).
// It must be eligible ONLY after the same request's media handoff.
elements = [];
const emptyBaseline = rich.snapshotPageMedia();
const hidden = new MockElement("img", "blob:https://chatgpt.com/tx03-generated", 10);
hidden.renderWidth = 0;
hidden.renderHeight = 0;
hidden.naturalWidth = 1254;
hidden.naturalHeight = 1254;
hidden.complete = true;
const hiddenSidebar = new MockElement("img", "blob:https://chatgpt.com/sidebar-asset", 11, "sidebar");
hiddenSidebar.renderWidth = hiddenSidebar.renderHeight = 0;
hiddenSidebar.naturalWidth = hiddenSidebar.naturalHeight = 1254;
hiddenSidebar.complete = true;
const hiddenUpload = new MockElement("img", "blob:https://chatgpt.com/user-upload", 12, "user");
hiddenUpload.renderWidth = hiddenUpload.renderHeight = 0;
hiddenUpload.naturalWidth = hiddenUpload.naturalHeight = 1254;
hiddenUpload.complete = true;
elements = [hidden, hiddenSidebar, hiddenUpload];
assert.equal(rich.locateNewPageMedia(emptyBaseline).images.length, 0,
  "no handoff: intrinsic dimensions alone cannot deliver an image");
const handoffImages = rich.locateNewPageMedia(emptyBaseline, null, true);
assert.equal(handoffImages.new_image_candidate_count, 1,
  "verified media handoff must identify the newly loaded ChatGPT image");
assert.equal(handoffImages.images[0], hidden,
  "sidebar and user uploads must remain excluded");
const lateBaseline = rich.snapshotPageMedia();
assert.equal(lateBaseline.keys.has(hidden.currentSrc), true,
  "a previously observed hidden image must be excluded after remount");
const oldHidden = new MockElement("img", hidden.currentSrc, 13);
oldHidden.renderWidth = oldHidden.renderHeight = 0;
oldHidden.naturalWidth = oldHidden.naturalHeight = 1254;
oldHidden.complete = true;
elements = [oldHidden];
assert.equal(rich.locateNewPageMedia(lateBaseline, null, true).images.length, 0,
  "remounted historical blob images are not newly generated");
const encodedHidden = new MockElement("img", inlineData, 14);
encodedHidden.renderWidth = encodedHidden.renderHeight = 0;
encodedHidden.naturalWidth = encodedHidden.naturalHeight = 1254;
encodedHidden.complete = true;
elements = [encodedHidden];
const intrinsicCapture = rich.locateNewPageMedia(lateBaseline, null, true);
assert.equal(intrinsicCapture.images.length, 1);
const embeddedHidden = await rich.capturePageImages(intrinsicCapture.images, {allowIntrinsic:true});
assert.equal(embeddedHidden.image_embedded_count, 1,
  "same-request image capture must not drop intrinsic-only candidates");
assert.match(embeddedHidden.text, /^!\\[图片\\]\\(data:image\\/png;base64,YWJj\\)$/);

assert.match(controllerSource, /const intrinsicHandoff = Boolean\\(imageHandoff\\)/);
assert.doesNotMatch(playgroundSource, /stream:false,timeout:300/,
  "Playground chat must not disable broker-owned adaptive image deadline");
assert.match(playgroundSource, /stream:false\\};/,
  "text requests use default deadline and media-handoff allowance");
console.log("TX03 intrinsic image fallback, historical exclusion, protected capture and adaptive Playground deadline passed");
