(() => {
  const KEY = "__CHAT2API_SUBMIT_RESCUE_V87__";
  if (globalThis[KEY]) return;

  const REQUEST_KEY = "__CHAT2API_REQUEST_CONTENT_V6__";
  const state = {
    version: 87,
    timer: null,
    trackedRequestId: "",
    promptSeenAt: 0,
    lastAttemptAt: 0,
    attempts: 0,
    rescued: 0,
  };
  globalThis[KEY] = state;

  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  function visible(element) {
    if (!element) return false;
    try {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
    } catch (_) { return false; }
  }

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || document;
  }

  function composer() {
    const root = composerRoot();
    for (const selector of [
      "#prompt-textarea",
      "textarea[placeholder]",
      "div[contenteditable='true'][data-lexical-editor='true']",
      "div[contenteditable='true'].ProseMirror",
      "[contenteditable='true']",
    ]) {
      const node = [...root.querySelectorAll(selector)].find(visible);
      if (node) return node;
    }
    return null;
  }

  function composerText(node = composer()) {
    if (!node) return "";
    return normalize("value" in node ? node.value : (node.innerText || node.textContent || ""));
  }

  function labelOf(element) {
    return normalize(`${element?.dataset?.testid || ""} ${element?.getAttribute?.("aria-label") || ""} ${element?.title || ""} ${element?.innerText || element?.textContent || ""}`);
  }

  function sendButton() {
    const root = composerRoot();
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

  function dispatchEnter(node) {
    if (!node) return;
    node.focus();
    for (const type of ["keydown", "keypress", "keyup"]) {
      node.dispatchEvent(new KeyboardEvent(type, {
        key: "Enter",
        code: "Enter",
        keyCode: 13,
        which: 13,
        bubbles: true,
        cancelable: true,
      }));
    }
  }

  async function emit(active, stage, extra = {}) {
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "chat.diagnostics",
          request_id: active.requestId,
          diagnostics: {
            submit_rescue: "submit-rescue-v87",
            submit_rescue_stage: stage,
            ...extra,
          },
        },
      });
    } catch (_) {}
  }

  async function tick() {
    const runtime = globalThis[REQUEST_KEY];
    const active = runtime?.active;
    const requestId = String(active?.requestId || "");
    if (!requestId || active?.cancelled || active?.completed || active?.responseStarted) {
      state.trackedRequestId = "";
      state.promptSeenAt = 0;
      state.lastAttemptAt = 0;
      state.attempts = 0;
      return;
    }

    if (state.trackedRequestId !== requestId) {
      state.trackedRequestId = requestId;
      state.promptSeenAt = 0;
      state.lastAttemptAt = 0;
      state.attempts = 0;
    }

    const node = composer();
    const current = composerText(node);
    const target = normalize(active.prompt || "");
    const matches = Boolean(current && target && (current === target || (target.length > 6 && current.includes(target))));
    if (!matches) {
      state.promptSeenAt = 0;
      return;
    }

    const now = Date.now();
    if (!state.promptSeenAt) {
      state.promptSeenAt = now;
      return;
    }
    // content_request_v6 owns the normal click path. Only intervene after the
    // exact request prompt has remained unchanged in the composer long enough
    // that a normal click/confirmation would already have happened.
    if (now - state.promptSeenAt < 4500) return;
    if (state.attempts >= 2 || now - state.lastAttemptAt < 3000) return;

    state.attempts += 1;
    state.lastAttemptAt = now;
    const button = sendButton();
    if (buttonReady(button)) {
      button.scrollIntoView?.({ block: "nearest", inline: "nearest" });
      button.click();
      state.rescued += 1;
      await emit(active, "clicked-stuck-draft", { attempt: state.attempts, composer_chars: current.length });
      return;
    }

    // Some ChatGPT composer revisions briefly show the prompt while the visual
    // send button is disabled or missed by selector updates. Enter is the same
    // conservative fallback used by v6 after an unconfirmed click; do it here
    // only for a proven automation-owned active prompt.
    dispatchEnter(node);
    state.rescued += 1;
    await emit(active, "enter-stuck-draft", { attempt: state.attempts, composer_chars: current.length, send_button_ready: false });
  }

  state.tick = tick;
  state.timer = setInterval(() => tick().catch(() => {}), 250);
})();
