(() => {
  const KEY = "__CHAT2API_DRAFT_MANAGED_RECOVERY_V55__";
  if (globalThis[KEY]) return;

  const ownership = globalThis.__CHAT2API_DRAFT_OWNERSHIP_V43__;
  const hygiene = globalThis.__CHAT2API_REQUEST_HYGIENE_V42__;
  const previous = ownership?.listener;
  const managedListener = hygiene?.listener;
  if (typeof previous !== "function" || typeof managedListener !== "function" || typeof ownership?.matchingRecord !== "function") return;

  const state = { version: 55, listener: null, managedFallbacks: 0, persistentFallbacks: 0 };
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

  function composerText() {
    const forms = [...document.querySelectorAll("form[data-type='unified-composer'], form")];
    const root = forms.find(form => visible(form) && form.querySelector?.("#prompt-textarea,textarea,[contenteditable='true']")) || document;
    for (const selector of [
      "#prompt-textarea",
      "textarea[placeholder]",
      "div[contenteditable='true'][data-lexical-editor='true']",
      "div[contenteditable='true'].ProseMirror",
      "[contenteditable='true']",
    ]) {
      const node = [...root.querySelectorAll(selector)].find(visible);
      if (!node) continue;
      return normalize("value" in node ? node.value : (node.innerText || node.textContent || ""));
    }
    return "";
  }

  function call(listener, message, sender, sendResponse) {
    try {
      return listener(message, sender, sendResponse);
    } catch (error) {
      sendResponse({ ok: false, error: String(error?.message || error), controller: "draft-managed-recovery-v55" });
      return false;
    }
  }

  try { chrome.runtime.onMessage.removeListener(previous); } catch (_) {}

  const listener = (message, sender, sendResponse) => {
    if (message?.type !== "chat2api.request.preflight") {
      return call(previous, message, sender, sendResponse);
    }

    (async () => {
      const current = composerText();
      if (!current) {
        call(previous, message, sender, sendResponse);
        return;
      }

      // v43 owns drafts that it can prove were written by chat2api. Preserve its
      // fingerprint-based recovery path for those drafts.
      const record = await ownership.matchingRecord(current).catch(() => null);
      if (record) {
        state.persistentFallbacks += 1;
        call(previous, message, sender, sendResponse);
        return;
      }

      // The production regression was here: v43 bypassed v42 and sent unknown
      // text straight to the conservative v4 preflight. That is correct for a
      // human/unowned ChatGPT tab, but wrong for a dedicated route/warm/reserve/
      // dispatch Worker tab where v42 can prove automation ownership. Delegate
      // unknown text back to v42; v42 itself still refuses to clear it unless the
      // background tab classifier says the tab is managed.
      state.managedFallbacks += 1;
      call(managedListener, message, sender, sendResponse);
    })().catch(error => sendResponse({
      ok: false,
      error: String(error?.message || error),
      controller: "draft-managed-recovery-v55",
    }));
    return true;
  };

  state.listener = listener;
  globalThis[KEY] = state;
  chrome.runtime.onMessage.addListener(listener);
})();
