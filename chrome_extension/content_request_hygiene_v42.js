(() => {
  const KEY = "__CHAT2API_REQUEST_HYGIENE_V42__";
  if (globalThis[KEY]) return;

  const v5 = globalThis.__CHAT2API_REQUEST_CONTENT_V5__;
  const priorListener = v5?.listener;
  if (typeof priorListener !== "function") return;
  try { chrome.runtime.onMessage.removeListener(priorListener); } catch (_) {}

  const state = {
    version: 42,
    recovered: 0,
    listener: null,
  };
  globalThis[KEY] = state;

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
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
    } catch (_) {
      return false;
    }
  }

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector?.("#prompt-textarea,textarea,[contenteditable='true']")) || document;
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

  function setComposerText(node, text) {
    if (!node) return false;
    node.focus();
    if (node instanceof HTMLTextAreaElement || node instanceof HTMLInputElement) {
      const proto = node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(node, text);
      node.dispatchEvent(new Event("input", { bubbles: true }));
      node.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(node);
    selection.removeAllRanges();
    selection.addRange(range);
    if (text) document.execCommand("insertText", false, text);
    else document.execCommand("delete", false);
    if (!text && normalize(node.textContent || "")) node.replaceChildren();
    try {
      node.dispatchEvent(new InputEvent("input", {
        bubbles: true,
        inputType: text ? "insertText" : "deleteContentBackward",
        data: text || null,
      }));
    } catch (_) {
      node.dispatchEvent(new Event("input", { bubbles: true }));
    }
    return true;
  }

  function generating() {
    for (const selector of [
      "button[data-testid='stop-button']",
      "button[aria-label='Stop streaming']",
      "button[aria-label='Stop generating']",
      "button[aria-label*='停止回答']",
      "button[aria-label*='停止生成']",
    ]) {
      if ([...document.querySelectorAll(selector)].some(node => visible(node) && !node.disabled)) return true;
    }
    return false;
  }

  async function managedOwnership() {
    try {
      const response = await chrome.runtime.sendMessage({ type: "chat2api.automation-tab.query" });
      return response?.ok ? response : { managed: false, source: "query-failed" };
    } catch (_) {
      return { managed: false, source: "query-failed" };
    }
  }

  function removeAutomationAttachmentChips() {
    let removed = 0;
    const root = composerRoot();
    const buttons = [...root.querySelectorAll("button")];
    for (const button of buttons) {
      if (!visible(button)) continue;
      const label = normalize(`${button.getAttribute?.("aria-label") || ""} ${button.title || ""}`);
      if (!/(remove|delete|clear).{0,30}(file|attachment|image)|删除.{0,10}(文件|附件|图片)|移除.{0,10}(文件|附件|图片)/i.test(label)) continue;
      try { button.click(); removed += 1; } catch (_) {}
    }
    return removed;
  }

  async function emit(requestId, diagnostics) {
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "chat.diagnostics",
          request_id: requestId,
          diagnostics,
        },
      });
    } catch (_) {}
  }

  async function recoverManagedDraft(message) {
    const requestId = String(message?.requestId || "");
    const before = composerText();
    if (!before) {
      const ownership = await managedOwnership();
      const removedAttachments = ownership.managed ? removeAutomationAttachmentChips() : 0;
      await emit(requestId, {
        request_controller_overlay: "request-hygiene-v42",
        submit_recovery_stage: "preflight-ready",
        automation_owned_tab: Boolean(ownership.managed),
        automation_owned_source: ownership.source || "",
        stale_draft_recovered: false,
        stale_attachments_removed: removedAttachments,
      });
      return {
        stale_draft_recovered: false,
        stale_attachments_removed: removedAttachments,
        automation_owned_tab: Boolean(ownership.managed),
      };
    }

    const ownership = await managedOwnership();
    if (!ownership.managed) return null;
    if (generating()) throw new Error("Automation-owned ChatGPT tab still has an active generation; refusing to overwrite it");

    const removedAttachments = removeAutomationAttachmentChips();
    const node = composer();
    setComposerText(node, "");
    let cleared = false;
    for (let attempt = 0; attempt < 20; attempt += 1) {
      if (!composerText()) { cleared = true; break; }
      await delay(75);
      if (attempt === 5 || attempt === 12) setComposerText(composer(), "");
    }
    if (!cleared) throw new Error(`Could not clear stale automation draft from managed ChatGPT tab (composer_chars=${composerText().length})`);

    // v4 keeps the previous prompt only in content-script memory.  A reload can
    // lose that memory while the DOM draft survives; reset its markers after a
    // managed recovery so later preflights never treat the recovered text as a
    // user draft.
    const v4 = globalThis.__CHAT2API_REQUEST_CONTENT_V4__;
    if (v4) {
      v4.lastPrompt = "";
      v4.lastAttachmentNames = [];
    }
    state.recovered += 1;
    await emit(requestId, {
      request_controller_overlay: "request-hygiene-v42",
      submit_recovery_stage: "preflight-ready",
      automation_owned_tab: true,
      automation_owned_source: ownership.source || "managed",
      stale_draft_recovered: true,
      stale_draft_chars: before.length,
      stale_attachments_removed: removedAttachments,
      managed_draft_recovery_count: state.recovered,
    });
    return {
      stale_draft_recovered: true,
      stale_draft_chars: before.length,
      stale_attachments_removed: removedAttachments,
      automation_owned_tab: true,
    };
  }

  const listener = (message, sender, sendResponse) => {
    if (message?.type === "chat2api.request.preflight") {
      recoverManagedDraft(message).then(data => {
        if (data) {
          sendResponse({ ok: true, data, controller: "request-hygiene-v42" });
          return;
        }
        // Manual/unowned tabs retain v4's conservative unknown-draft protection.
        const returned = priorListener(message, sender, sendResponse);
        if (returned !== true && returned !== false) sendResponse({ ok: false, error: "Legacy request preflight did not respond", controller: "request-hygiene-v42" });
      }).catch(error => sendResponse({ ok: false, error: String(error?.message || error), controller: "request-hygiene-v42" }));
      return true;
    }
    return priorListener(message, sender, sendResponse);
  };

  state.listener = listener;
  chrome.runtime.onMessage.addListener(listener);
})();
