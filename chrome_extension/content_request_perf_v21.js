(() => {
  const KEY = "__CHAT2API_REQUEST_PERF_V21__";
  if (globalThis[KEY]) return;

  const state = { timer: null, requestId: null, armedAt: 0 };
  globalThis[KEY] = state;

  const FAST_FALLBACK_MS = 1200;
  const page = () => globalThis.__CHAT2API_PAGE_ADAPTER_V22__ || null;
  const normalizeFallback = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  const normalize = value => page()?.normalize?.(value) ?? normalizeFallback(value);

  function visible(element) {
    const adapter = page();
    if (adapter?.visible) return adapter.visible(element);
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function composerRoot() {
    const adapter = page();
    if (adapter?.composerRoot) return adapter.composerRoot();
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || null;
  }

  function composer() {
    const adapter = page();
    if (adapter?.composer) return adapter.composer();
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

  function composerText(element = composer()) {
    const adapter = page();
    if (adapter?.composerText) return adapter.composerText(element);
    if (!element) return "";
    if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) return normalize(element.value || "");
    return normalize(element.innerText || element.textContent || "");
  }

  function sendButton() {
    const adapter = page();
    if (adapter?.sendButton) return adapter.sendButton();
    const root = composerRoot() || document;
    for (const selector of [
      "button[data-testid='send-button']",
      "button[aria-label='Send prompt']",
      "button[aria-label*='发送提示']",
      "button[aria-label*='发送消息']",
      "button[type='submit']",
    ]) {
      const found = [...root.querySelectorAll(selector)].find(visible);
      if (found) return found;
    }
    return null;
  }

  function buttonReady(button) {
    const adapter = page();
    if (adapter?.buttonReady) return adapter.buttonReady(button);
    return Boolean(button && visible(button) && !button.disabled && button.getAttribute("aria-disabled") !== "true");
  }

  function stopButton() {
    const adapter = page();
    if (adapter?.stopButton) return adapter.stopButton();
    for (const selector of [
      "button[data-testid='stop-button']",
      "button[aria-label='Stop streaming']",
      "button[aria-label='Stop generating']",
      "button[aria-label*='停止回答']",
      "button[aria-label*='停止生成']",
    ]) {
      const found = [...document.querySelectorAll(selector)].find(item => visible(item) && !item.disabled);
      if (found) return found;
    }
    return null;
  }

  function isSendTarget(target) {
    const adapter = page();
    if (adapter?.isSendTarget) return adapter.isSendTarget(target);
    if (!(target instanceof Element)) return false;
    const button = target.closest("button");
    if (!button || !visible(button)) return false;
    if (button.matches("button[data-testid='send-button'],button[type='submit']")) return true;
    const label = normalize(`${button.getAttribute("aria-label") || ""} ${button.innerText || button.textContent || ""}`).toLowerCase();
    return /send prompt|send message|发送提示|发送消息|发送$/.test(label);
  }

  function dispatchEnter(element) {
    const adapter = page();
    if (adapter?.dispatchEnter) return adapter.dispatchEnter(element);
    if (!element) return false;
    element.focus();
    for (const type of ["keydown", "keypress", "keyup"]) {
      element.dispatchEvent(new KeyboardEvent(type, {
        key: "Enter",
        code: "Enter",
        keyCode: 13,
        which: 13,
        bubbles: true,
        cancelable: true,
      }));
    }
    return true;
  }

  async function emitDiagnostics(requestId, extra) {
    if (!requestId) return;
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "chat.diagnostics",
          request_id: requestId,
          diagnostics: {
            request_perf_overlay: "v21",
            page_adapter: page()?.version || "fallback",
            ...extra,
          },
        },
      });
    } catch (_) {}
  }

  function armFastFallback() {
    const controller = globalThis.__CHAT2API_REQUEST_CONTENT_V5__;
    const active = controller?.active;
    const legacy = globalThis.__CHAT2API_REQUEST_CONTENT_V4__;
    const prompt = String(legacy?.lastPrompt || "").trim();
    if (!active?.requestId || active.cancelled || !prompt) return;

    clearTimeout(state.timer);
    state.requestId = active.requestId;
    state.armedAt = performance.now();
    state.timer = setTimeout(async () => {
      const currentController = globalThis.__CHAT2API_REQUEST_CONTENT_V5__;
      const current = currentController?.active;
      if (!current?.requestId || current.requestId !== state.requestId || current.cancelled) return;

      const input = composer();
      const text = composerText(input);
      const target = normalize(prompt);
      const stillPresent = Boolean(text && (text === target || (target.length > 6 && text.includes(target))));
      const button = sendButton();

      if (!stillPresent || stopButton() || !buttonReady(button)) return;

      dispatchEnter(input);
      const elapsed = Math.round((performance.now() - state.armedAt) * 10) / 10;
      await emitDiagnostics(current.requestId, {
        submit_fast_enter_fallback: true,
        submit_fast_enter_after_ms: elapsed,
        submit_fast_enter_guard: "prompt-present+not-generating+send-ready",
        composer_chars: text.length,
      });
    }, FAST_FALLBACK_MS);
  }

  document.addEventListener("click", event => {
    if (!isSendTarget(event.target)) return;
    armFastFallback();
  }, true);

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type === "chat2api.cancel") {
      clearTimeout(state.timer);
      state.timer = null;
      state.requestId = null;
    }
    return false;
  });
})();
