import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync("chrome_extension/content_login_v27.js", "utf8");

class FakeElement {
  constructor({ text = "", aria = "" } = {}) {
    this.innerText = text;
    this.textContent = text;
    this.aria = aria;
  }

  getBoundingClientRect() {
    return { width: 120, height: 32 };
  }

  getAttribute(name) {
    return name === "aria-label" ? this.aria : "";
  }
}

function detect({ pathname = "/", composer = false, authText = "", authAria = "" } = {}) {
  const composerNode = new FakeElement();
  const authNode = authText || authAria ? new FakeElement({ text: authText, aria: authAria }) : null;
  const composerSelectors = new Set([
    "#prompt-textarea",
    "form[data-type='unified-composer'] textarea",
    "form[data-type='unified-composer'] [contenteditable='true']",
    "textarea[placeholder]",
    "div[contenteditable='true'][data-lexical-editor='true']",
    "div[contenteditable='true'].ProseMirror",
  ]);
  const document = {
    readyState: "complete",
    querySelectorAll(selector) {
      if (selector === "a,button,[role='button']") return authNode ? [authNode] : [];
      if (composer && composerSelectors.has(selector)) return [composerNode];
      return [];
    },
  };
  const context = {
    Element: FakeElement,
    document,
    location: { pathname, href: `https://chatgpt.com${pathname}` },
    getComputedStyle: () => ({ display: "block", visibility: "visible", opacity: "1" }),
    chrome: { runtime: { onMessage: { addListener() {} } } },
    Date,
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: "content_login_v27.js" });
  return context.__CHAT2API_LOGIN_DETECTOR_V27__.detect();
}

const guest = detect({ composer: true, authText: "Log in", authAria: "Log in" });
assert.equal(guest.state, "login_required", "Visible Login control must beat the guest composer");
assert.equal(guest.composer_ready, false);
assert.equal(guest.strategy, "visible-auth-control");

const chineseGuest = detect({ composer: true, authText: "登录" });
assert.equal(chineseGuest.state, "login_required", "Chinese login control must beat the guest composer");

const authPath = detect({ pathname: "/auth/login", composer: true });
assert.equal(authPath.state, "login_required", "Authentication path must beat the guest composer");
assert.equal(authPath.strategy, "auth-path");

const authenticated = detect({ composer: true });
assert.equal(authenticated.state, "ready", "Composer remains ready evidence when no auth evidence is visible");
assert.equal(authenticated.composer_ready, true);

console.log("content_login_guest_precedence_v27 VM contract passed");
