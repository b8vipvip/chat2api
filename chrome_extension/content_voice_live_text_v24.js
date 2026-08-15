(() => {
  const KEY = "__CHAT2API_VOICE_LIVE_TEXT_V24__";
  if (globalThis[KEY]) return;
  globalThis[KEY] = true;

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  function visible(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || null;
  }

  function findComposer() {
    const root = composerRoot() || document;
    for (const selector of [
      "#prompt-textarea",
      "textarea[placeholder]",
      "div[contenteditable='true'][data-lexical-editor='true']",
      "div[contenteditable='true'].ProseMirror",
      "[contenteditable='true']",
    ]) {
      const found = [...root.querySelectorAll(selector)].find(visible);
      if (found) return found;
    }
    return null;
  }

  function composerText(element = findComposer()) {
    if (!element) return "";
    if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) return normalize(element.value || "");
    return normalize(element.innerText || element.textContent || "");
  }

  function setComposerText(element, text) {
    element.focus();
    if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) {
      const proto = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(element, text);
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(element);
    selection.removeAllRanges();
    selection.addRange(range);
    document.execCommand("insertText", false, text);
    if (text && !normalize(element.textContent || "")) {
      const p = document.createElement("p");
      p.textContent = text;
      element.replaceChildren(p);
    }
    try {
      element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
    } catch (_) {
      element.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  function labelOf(element) {
    return normalize(`${element?.dataset?.testid || ""} ${element?.getAttribute?.("aria-label") || ""} ${element?.title || ""} ${element?.innerText || element?.textContent || ""}`);
  }

  function sendButton() {
    const root = composerRoot() || document;
    for (const selector of [
      "button[data-testid='send-button']",
      "button[aria-label='Send prompt']",
      "button[aria-label*='发送提示']",
      "button[aria-label*='发送消息']",
      "button[type='submit']",
    ]) {
      const button = [...root.querySelectorAll(selector)].find(visible);
      if (button) return button;
    }
    return [...root.querySelectorAll("button")]
      .find(button => visible(button) && /send prompt|send message|发送提示|发送消息|发送$/i.test(labelOf(button))) || null;
  }

  function buttonReady(button) {
    return Boolean(button && visible(button) && !button.disabled && button.getAttribute("aria-disabled") !== "true");
  }

  function dispatchEnter(element) {
    if (!element) return;
    element.focus();
    for (const type of ["keydown", "keypress", "keyup"]) {
      element.dispatchEvent(new KeyboardEvent(type, {
        key: "Enter", code: "Enter", keyCode: 13, which: 13,
        bubbles: true, cancelable: true,
      }));
    }
  }

  async function waitFor(predicate, timeout = 6000, interval = 100) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      try {
        const value = predicate();
        if (value) return value;
      } catch (_) {}
      await delay(interval);
    }
    return null;
  }

  async function emitLive(active, liveEvent, data = {}) {
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "image.progress",
          kind: "voice-live",
          request_id: active.requestId,
          live_event: liveEvent,
          session_id: active.sessionId,
          ...data,
        },
      });
    } catch (_) {}
  }

  async function sendLiveText(active, itemId, text) {
    const target = normalize(text);
    if (!target) throw new Error("Live text input is empty");
    const composer = await waitFor(findComposer, 8000, 100);
    if (!composer) throw new Error("ChatGPT Voice text composer is not available");

    setComposerText(composer, text);
    const retained = await waitFor(() => {
      const current = findComposer();
      const value = composerText(current);
      return value === target || (target.length > 6 && value.includes(target));
    }, 3500, 80);
    if (!retained) throw new Error("Live text input could not be retained in the ChatGPT composer");

    const button = await waitFor(() => {
      const value = sendButton();
      return buttonReady(value) ? value : null;
    }, 5000, 80);
    if (button) button.click();
    else dispatchEnter(findComposer());

    let sent = await waitFor(() => !composerText(findComposer()), 2500, 80);
    if (!sent && composerText(findComposer())) {
      dispatchEnter(findComposer());
      sent = await waitFor(() => !composerText(findComposer()), 3500, 80);
    }
    if (!sent) throw new Error("ChatGPT Voice did not confirm the live text send action");

    await emitLive(active, "input.text.sent", { item_id: itemId || "", text });
  }

  async function emitTerminal(requestId) {
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "image.completed",
          kind: "voice-live",
          request_id: requestId,
          images: [],
          live_terminal: true,
        },
      });
    } catch (_) {}
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    const state = globalThis.__CHAT2API_VOICE_LIVE_CONTENT_V2__;
    if (message.type === "chat2api.voice.live.text") {
      const active = state?.active;
      if (!active || active.requestId !== message.requestId) {
        sendResponse({ ok: false, error: "Live session is not active" });
        return false;
      }
      sendLiveText(active, String(message.itemId || ""), String(message.text || ""))
        .then(() => sendResponse({ ok: true, controller: "voice-live-text-v24" }))
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error), controller: "voice-live-text-v24" }));
      return true;
    }
    if (message.type === "chat2api.voice.live.stop") {
      const requestId = String(message.requestId || state?.active?.requestId || "");
      if (requestId) setTimeout(() => emitTerminal(requestId), 180);
      return false;
    }
    return false;
  });
})();
