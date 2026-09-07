import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../chrome_extension/content_ui_hygiene_v31.js", import.meta.url), "utf8");

class FakeElement {
  constructor({ attrs = {}, text = "", rect = {left: 0, top: 0, right: 100, bottom: 40, width: 100, height: 40}, buttons = [], hasSvg = false } = {}) {
    this.attrs = attrs;
    this.innerText = text;
    this.textContent = text;
    this.rect = rect;
    this.buttons = buttons;
    this.hasSvg = hasSvg;
    this.clicked = 0;
  }
  getAttribute(name) { return this.attrs[name] || ""; }
  getBoundingClientRect() { return this.rect; }
  querySelectorAll(selector) {
    if (selector === "button,[role='button']") return this.buttons;
    return [];
  }
  querySelector(selector) {
    if (selector === "svg" && this.hasSvg) return {};
    return null;
  }
  click() { this.clicked += 1; }
}

function runDialog({ dialogText, buttons }) {
  const dialog = new FakeElement({
    text: dialogText,
    rect: {left: 300, top: 220, right: 788, bottom: 726, width: 488, height: 506},
    buttons,
  });
  const documentElement = new FakeElement({rect: {left: 0, top: 0, right: 1200, bottom: 982, width: 1200, height: 982}});
  const document = {
    documentElement,
    hidden: false,
    querySelectorAll: () => [dialog],
    addEventListener: () => {},
  };
  class FakeMutationObserver { observe() {} }
  const context = {
    console,
    Element: FakeElement,
    document,
    window: { addEventListener: () => {} },
    MutationObserver: FakeMutationObserver,
    getComputedStyle: () => ({display: "block", visibility: "visible", opacity: "1"}),
    setInterval: () => 1,
    clearInterval: () => {},
    setTimeout: () => 1,
    clearTimeout: () => {},
    Date,
    WeakSet,
    Object,
    String,
    Number,
    Boolean,
    Math,
    RegExp,
  };
  vm.createContext(context);
  vm.runInContext(source, context, {filename: "content_ui_hygiene_v31.js"});
  return { dialog, state: context.__CHAT2API_UI_HYGIENE_V31__.state };
}

{
  const close = new FakeElement({
    attrs: {"aria-label": "Close"},
    rect: {left: 736, top: 245, right: 772, bottom: 281, width: 36, height: 36},
    hasSvg: true,
  });
  const start = new FakeElement({
    text: "开始使用",
    rect: {left: 502, top: 650, right: 590, bottom: 694, width: 88, height: 44},
  });
  const { state } = runDialog({
    dialogText: "Apple Health Synced Function Synced One Medical Synced 你已可在 ChatGPT 中使用健康 安全连接你的医疗记录和 Apple 健康 开始使用",
    buttons: [start, close],
  });
  assert.equal(close.clicked, 1, "health promotion must close via explicit Close control");
  assert.equal(start.clicked, 0, "health promotion must never click 开始使用");
  assert.equal(state.lastCategory, "health-promo-modal");
  assert.equal(state.revision, 101);
}

{
  const iconClose = new FakeElement({
    rect: {left: 736, top: 245, right: 772, bottom: 281, width: 36, height: 36},
    hasSvg: true,
  });
  const start = new FakeElement({
    text: "Get started",
    rect: {left: 500, top: 650, right: 600, bottom: 694, width: 100, height: 44},
  });
  runDialog({
    dialogText: "You can now use Health in ChatGPT. Securely connect your medical records and Apple Health. Get started",
    buttons: [start, iconClose],
  });
  assert.equal(iconClose.clicked, 1, "unlabelled top-right SVG X must be accepted only for identified health promotions");
  assert.equal(start.clicked, 0, "Get started CTA must remain untouched");
}

{
  const close = new FakeElement({
    attrs: {"aria-label": "Close"},
    rect: {left: 736, top: 245, right: 772, bottom: 281, width: 36, height: 36},
    hasSvg: true,
  });
  const purchase = new FakeElement({
    text: "Purchase",
    rect: {left: 500, top: 650, right: 600, bottom: 694, width: 100, height: 44},
  });
  runDialog({
    dialogText: "Apple Health subscription purchase billing payment",
    buttons: [purchase, close],
  });
  assert.equal(close.clicked, 0, "dangerous financial context must remain untouched");
  assert.equal(purchase.clicked, 0, "financial CTA must remain untouched");
}

console.log("ui hygiene health modal v101 contract passed");
