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

  // Only one turn may own the ChatGPT composer at a time. Voice/audio
  // streaming continues independently while upload + send is in progress.
  let turnQueue = Promise.resolve();
  function queueTurn(task) {
    const next = turnQueue.catch(() => {}).then(task);
    turnQueue = next.catch(() => {});
    return next;
  }

  function assertLiveTurnOwner(active) {
    if (active.stopped || globalThis.__CHAT2API_VOICE_LIVE_CONTENT_V2__?.active !== active)
      throw new Error("Voice call ended before this input could be sent");
  }

  async function sendLiveText(active, itemId, text) {
    assertLiveTurnOwner(active);
    const target = normalize(text);
    if (!target) throw new Error("Live text input is empty");
    const composer = await waitFor(findComposer, 8000, 100);
    if (!composer) throw new Error("ChatGPT Voice text composer is not available");
    const baselineUsers = [...document.querySelectorAll("[data-message-author-role='user']")].filter(visible).length;

    setComposerText(composer, text);
    const retained = await waitFor(() => {
      const current = findComposer();
      const value = composerText(current);
      return value === target || (target.length > 6 && value.includes(target));
    }, 3500, 80);
    if (!retained) throw new Error("Live text input could not be retained in the ChatGPT composer");
    assertLiveTurnOwner(active);

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

    // Composer clearing only proves that a UI action happened; require the
    // actual new user turn before acknowledging delivery to the API caller.
    const confirmed = await waitFor(() => {
      const nodes = [...document.querySelectorAll("[data-message-author-role='user']")].filter(visible);
      return nodes.slice(baselineUsers).some(node =>
        normalize(node.innerText || node.textContent || "").includes(target));
    }, 10000, 120);
    if (!confirmed) throw new Error("ChatGPT Voice did not confirm the live text in a user conversation turn");
    assertLiveTurnOwner(active);

    await emitLive(active, "input.text.sent", { item_id: itemId || "", text });
  }

  async function sendLiveMedia(active, itemId, text, attachments) {
    assertLiveTurnOwner(active);
    if (!Array.isArray(attachments) || !attachments.length || attachments.length > 4)
      throw new Error("Live media message requires 1-4 uploaded attachments");
    if (!globalThis.__CHAT2API_MULTIMODAL_V4__?.attach)
      throw new Error("ChatGPT multimodal upload controller is not available");
    // Reuse the canonical Worker v78 upload and settle evidence. No competing
    // uploader, fallback automation, or terminal decision is introduced.
    const upload = await globalThis.__CHAT2API_MULTIMODAL_V4__.attach(attachments);
    if (upload.attachment_verified !== true || upload.attachments_count !== attachments.length)
      throw new Error("ChatGPT did not confirm all live media attachments");
    assertLiveTurnOwner(active);
    const instruction = String(text || "").trim()
      || "请直接理解我在通话中发送的图片或文件，并用语音回答，不要结束语音通话。";
    await sendLiveText(active, itemId, instruction);
    await emitLive(active, "input.media.sent", {
      item_id: itemId || "",
      attachment_count: attachments.length,
    });
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
      queueTurn(() => sendLiveText(active, String(message.itemId || ""), String(message.text || "")))
        .then(() => sendResponse({ ok: true, controller: "voice-live-text-v24" }))
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error), controller: "voice-live-text-v24" }));
      return true;
    }
    if (message.type === "chat2api.voice.live.multimodal") {
      const active = state?.active;
      if (!active || active.requestId !== message.requestId) {
        sendResponse({ ok: false, error: "Live session is not active" });
        return false;
      }
      queueTurn(() => sendLiveMedia(active, String(message.itemId || ""),
        String(message.text || ""), message.attachments || []))
        .then(() => sendResponse({ ok: true, controller: "voice-live-multimodal-v195" }))
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error), controller: "voice-live-multimodal-v195" }));
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
