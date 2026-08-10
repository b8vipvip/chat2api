(() => {
  const KEY = "__CHAT2API_REQUEST_CONTENT_V3__";
  if (globalThis[KEY]) return;

  const v2 = globalThis.__CHAT2API_REQUEST_CONTENT_V2__;
  const priorListener = v2?.listener;
  if (typeof priorListener !== "function") return;
  try { chrome.runtime.onMessage.removeListener(priorListener); } catch (_) {}

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const state = {
    listener: null,
    lastRequestId: null,
    lastPrompt: "",
    lastAttachmentNames: [],
    lastUserCount: 0,
  };
  globalThis[KEY] = state;

  function visible(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function findComposer() {
    const selectors = [
      "#prompt-textarea",
      "textarea[placeholder]",
      "div[contenteditable='true'][data-lexical-editor='true']",
      "form div[contenteditable='true']",
    ];
    for (const selector of selectors) {
      const found = [...document.querySelectorAll(selector)].find(visible);
      if (found) return found;
    }
    return null;
  }

  function composerText(element = findComposer()) {
    if (!element) return "";
    if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) return String(element.value || "").trim();
    return String(element.innerText || element.textContent || "").replace(/\s+/g, " ").trim();
  }

  function setComposerText(element, text) {
    if (!element) return;
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
    if (text) document.execCommand("insertText", false, text);
    else document.execCommand("delete", false);
    if (!text && (element.textContent || "").trim()) element.replaceChildren();
    element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: text ? "insertText" : "deleteContentBackward", data: text || null }));
  }

  const normalize = value => String(value || "").replace(/\s+/g, " ").trim();
  const userMessageCount = () => document.querySelectorAll("[data-message-author-role='user']").length;

  function stopButton() {
    const selectors = [
      "button[data-testid='stop-button']",
      "button[aria-label='Stop streaming']",
      "button[aria-label='Stop generating']",
      "button[aria-label*='停止生成']",
    ];
    for (const selector of selectors) {
      const button = [...document.querySelectorAll(selector)].find(item => visible(item) && !item.disabled);
      if (button) return button;
    }
    return null;
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

  async function emitDiagnostic(requestId, stage, extra = {}) {
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "chat.diagnostics",
          request_id: requestId,
          diagnostics: { request_controller_overlay: "request-v3", submit_recovery_stage: stage, ...extra },
        },
      });
    } catch (_) {}
  }

  async function cleanupPreviousAutomationDraft() {
    const composer = findComposer();
    const currentText = normalize(composerText(composer));
    const previousText = normalize(state.lastPrompt);
    const multimodal = globalThis.__CHAT2API_MULTIMODAL_V3__;
    let staleDraftRecovered = false;
    let removedAttachments = 0;

    if (currentText) {
      if (previousText && currentText === previousText) {
        setComposerText(composer, "");
        await delay(120);
        staleDraftRecovered = composerText(composer) === "";
        if (!staleDraftRecovered) throw new Error("Unable to clear the previous chat2api automation draft");
      } else {
        throw new Error("ChatGPT composer contains a manual or unknown draft; refusing to overwrite it");
      }
    }

    if (state.lastAttachmentNames.length && typeof multimodal?.removeAttachmentsByName === "function") {
      const result = await multimodal.removeAttachmentsByName(state.lastAttachmentNames);
      removedAttachments = Number(result?.removed || 0);
    }

    return { stale_draft_recovered: staleDraftRecovered, stale_attachments_removed: removedAttachments };
  }

  async function preflight(message) {
    const recovered = await cleanupPreviousAutomationDraft();
    await emitDiagnostic(message.requestId, "preflight-ready", recovered);
    return recovered;
  }

  async function enterFallback(message, beforeUsers) {
    const prompt = normalize(message.prompt);
    if (!prompt) return;
    await delay(4200);
    const composer = findComposer();
    if (!composer) return;
    const current = normalize(composerText(composer));
    if (current !== prompt) return;
    if (userMessageCount() > beforeUsers || stopButton()) return;
    dispatchEnter(composer);
    await emitDiagnostic(message.requestId, "enter-fallback", {
      enter_fallback_used: true,
      composer_chars: current.length,
      user_message_count: beforeUsers,
    });
  }

  const listener = (message, sender, sendResponse) => {
    if (message.type === "chat2api.request.preflight") {
      preflight(message)
        .then(data => sendResponse({ ok: true, data, controller: "request-v3" }))
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error), controller: "request-v3" }));
      return true;
    }

    if (message.type === "chat2api.request") {
      const beforeUsers = userMessageCount();
      state.lastRequestId = message.requestId || null;
      state.lastPrompt = String(message.prompt || "").trim();
      state.lastAttachmentNames = Array.isArray(message.options?.chat2api_diagnostics?.attachment_names)
        ? [...message.options.chat2api_diagnostics.attachment_names]
        : [];
      state.lastUserCount = beforeUsers;
      enterFallback(message, beforeUsers).catch(() => {});
      return priorListener(message, sender, sendResponse);
    }

    return priorListener(message, sender, sendResponse);
  };

  state.listener = listener;
  chrome.runtime.onMessage.addListener(listener);
  if (v2) v2.listener = listener;
})();
