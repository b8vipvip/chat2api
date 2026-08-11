(() => {
  const KEY = "__CHAT2API_VOICE_TRIGGER_FIX_V4__";
  if (globalThis[KEY]) return;

  const AUTOMATION_DRAFT_KEY = "chat2apiLastAutomationDraftV2";
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const state = { last: null };
  globalThis[KEY] = state;

  function visible(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  const normalize = value => String(value || "").replace(/\s+/g, " ").trim();
  const labelOf = el => normalize(`${el?.dataset?.testid || ""} ${el?.getAttribute?.("aria-label") || ""} ${el?.title || ""} ${el?.innerText || el?.textContent || ""}`);

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || null;
  }

  function composerInput() {
    const root = composerRoot() || document;
    return [...root.querySelectorAll("#prompt-textarea,textarea,[contenteditable='true']")].find(visible) || null;
  }

  function composerText(el = composerInput()) {
    if (!el) return "";
    if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) return normalize(el.value || "");
    return normalize(el.innerText || el.textContent || "");
  }

  function setComposerText(el, text) {
    if (!el) return;
    el.focus();
    if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(el, text);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    selection.removeAllRanges();
    selection.addRange(range);
    if (text) document.execCommand("insertText", false, text);
    else document.execCommand("delete", false);
    if (!text && normalize(el.textContent || "")) el.replaceChildren();
    try {
      el.dispatchEvent(new InputEvent("input", {
        bubbles: true,
        inputType: text ? "insertText" : "deleteContentBackward",
        data: text || null,
      }));
    } catch (_) {
      el.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  async function sha256(text) {
    const bytes = new TextEncoder().encode(String(text || ""));
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join("");
  }

  function attachmentRemoveButtons() {
    const root = composerRoot() || document;
    return [...root.querySelectorAll("button")].filter(button => {
      if (!visible(button) || button.disabled) return false;
      const label = labelOf(button);
      return /(remove file|remove attachment|删除文件|移除文件|移除附件|attachment-remove|file-remove)/i.test(label);
    });
  }

  async function removeComposerAttachments() {
    let removed = 0;
    for (let round = 0; round < 12; round += 1) {
      const buttons = attachmentRemoveButtons();
      if (!buttons.length) break;
      buttons[0].click();
      removed += 1;
      await delay(180);
    }
    return removed;
  }

  async function cleanupKnownAutomationDraft() {
    const input = composerInput();
    const current = composerText(input);
    if (!current) return { stale_automation_draft_cleared: false, stale_attachments_removed: 0, composer_chars_before: 0 };

    const saved = await chrome.storage.local.get(AUTOMATION_DRAFT_KEY).catch(() => ({}));
    const record = saved?.[AUTOMATION_DRAFT_KEY] || null;
    if (!record?.sha256 || Number(record.chars || 0) !== current.length) {
      throw new Error(`Voice preflight found a non-empty composer (${current.length} chars) that is not a known chat2api automation draft; refusing to erase it`);
    }
    const digest = await sha256(current);
    if (digest !== record.sha256) {
      throw new Error(`Voice preflight found a non-empty composer (${current.length} chars) whose fingerprint does not match the last chat2api automation draft; refusing to erase it`);
    }

    setComposerText(input, "");
    const clearDeadline = Date.now() + 4000;
    while (Date.now() < clearDeadline && composerText(input)) await delay(100);
    if (composerText(input)) throw new Error("Voice preflight matched the stale automation draft but could not clear it");
    const removed = await removeComposerAttachments();
    await delay(250);
    return {
      stale_automation_draft_cleared: true,
      stale_automation_request_id: record.request_id || null,
      stale_attachments_removed: removed,
      composer_chars_before: current.length,
    };
  }

  function voiceReady() {
    if ([...document.querySelectorAll("[data-testid='voice-floating-orb'],[data-testid*='voice-orb']")].some(visible)) return true;
    const body = normalize(document.body?.innerText || "");
    return /(准备好了，?\s*随时开始|ready when you are|start speaking|可以开始说话)/i.test(body);
  }

  function score(button) {
    if (!visible(button) || button.disabled) return -10000;
    const label = labelOf(button);
    const testid = String(button.dataset?.testid || "");
    if (/(添加文件|附件|upload|attach|composer-plus|添加照片|听写|dictat|microphone|麦克风|send|发送)/i.test(label + " " + testid)) return -10000;
    let value = 0;
    if (/composer-speech-button/i.test(testid)) value += 500;
    if (/voice|speech/i.test(testid)) value += 260;
    if (/启动语音功能|start voice|voice mode|开始语音|启动语音|语音模式/i.test(label)) value += 400;
    if (/voice|语音/i.test(label)) value += 180;
    return value;
  }

  function strictTrigger() {
    const root = composerRoot();
    if (!root) return null;
    return [...root.querySelectorAll("button")]
      .map(button => ({ button, label: labelOf(button), score: score(button) }))
      .filter(item => item.score > 0)
      .sort((a, b) => b.score - a.score)[0] || null;
  }

  function annotate(item) {
    if (!item?.button) return null;
    const button = item.button;
    // Marker-only annotation. Rewriting aria-label while observing aria-label
    // created a self-triggering MutationObserver loop for roughly 45 seconds.
    button.dataset.chat2apiVoiceTrigger = "v4";
    const snapshot = {
      label: normalize(item.label).slice(0, 160),
      testid: String(button.dataset.testid || "").slice(0, 120),
      at: Date.now(),
      annotation_strategy: "dataset-only",
    };
    state.last = snapshot;
    return snapshot;
  }

  async function prepare(timeout = 25000) {
    const cleanup = await cleanupKnownAutomationDraft();
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (voiceReady()) return { ready: true, reason: "voice-ui-already-ready", label: "", cleanup };
      const item = strictTrigger();
      if (item) return { ready: false, reason: "strict-trigger-v4", ...annotate(item), cleanup };
      await delay(120);
    }
    const root = composerRoot() || document;
    const labels = [...root.querySelectorAll("button")].filter(visible).map(labelOf).filter(Boolean).slice(0, 30);
    throw new Error(`ChatGPT Voice button was not found after stale-draft recovery. Visible composer buttons: ${labels.join(" | ")}`);
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type === "chat2api.voice.trigger.prepare.v4") {
      prepare(Number(message.timeout_ms || 25000))
        .then(data => sendResponse({ ok: true, data, controller: "voice-trigger-v4" }))
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error), controller: "voice-trigger-v4" }));
      return true;
    }
    return false;
  });
})();
