(() => {
  const KEY = "__CHAT2API_DRAFT_OWNERSHIP_V43__";
  if (globalThis[KEY]) return;

  const hygiene = globalThis.__CHAT2API_REQUEST_HYGIENE_V42__;
  const priorListener = hygiene?.listener;
  const v5 = globalThis.__CHAT2API_REQUEST_CONTENT_V5__;
  const legacyListener = v5?.listener;
  if (typeof priorListener !== "function" || typeof legacyListener !== "function") return;
  try { chrome.runtime.onMessage.removeListener(priorListener); } catch (_) {}

  const STORAGE_PREFIX = "chat2apiDraftOwnershipV43:";
  const RECORD_TTL_MS = 6 * 60 * 60 * 1000;
  const POLL_MS = 125;
  const STARTUP_RECOVERY_MS = 90 * 1000;
  const state = { version: 43, listener: null, recovered: 0, cleanedOnFailure: 0 };
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

  function removeAutomationAttachmentChips() {
    let removed = 0;
    for (const button of composerRoot().querySelectorAll("button")) {
      if (!visible(button)) continue;
      const label = normalize(`${button.getAttribute?.("aria-label") || ""} ${button.title || ""}`);
      if (!/(remove|delete|clear).{0,30}(file|attachment|image)|删除.{0,10}(文件|附件|图片)|移除.{0,10}(文件|附件|图片)/i.test(label)) continue;
      try { button.click(); removed += 1; } catch (_) {}
    }
    return removed;
  }

  async function fingerprint(text) {
    const normalized = normalize(text);
    if (!normalized) return "";
    try {
      const bytes = new TextEncoder().encode(normalized);
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join("");
    } catch (_) {
      let hash = 2166136261;
      for (let index = 0; index < normalized.length; index += 1) {
        hash ^= normalized.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
      }
      return `fnv1a-${(hash >>> 0).toString(16).padStart(8, "0")}-${normalized.length}`;
    }
  }

  const storageKey = requestId => `${STORAGE_PREFIX}${String(requestId || "")}`;

  async function managedOwnership() {
    try {
      const response = await chrome.runtime.sendMessage({ type: "chat2api.automation-tab.query" });
      return response?.ok ? response : { managed: false, source: "query-failed" };
    } catch (_) {
      return { managed: false, source: "query-failed" };
    }
  }

  async function saveRecord(requestId, prompt, status = "prepared") {
    // Receiving chat2api.request is itself authoritative automation ownership.
    // The background tab classification is diagnostic only; do not make safety
    // depend on volatile warm/route maps surviving a browser/service-worker restart.
    const ownership = await managedOwnership();
    const normalized = normalize(prompt);
    const digest = await fingerprint(normalized);
    if (!normalized || !digest) return null;
    const now = Date.now();
    const record = {
      version: 43,
      request_id: String(requestId || ""),
      fingerprint: digest,
      chars: normalized.length,
      status,
      created_at: now,
      updated_at: now,
      automation_owned: true,
      automation_source: String(ownership.source || (ownership.managed ? "managed" : "request-dispatch")),
      page_origin: location.origin,
      page_path: location.pathname,
    };
    await chrome.storage.local.set({ [storageKey(requestId)]: record });
    return record;
  }

  async function updateRecord(requestId, status) {
    const key = storageKey(requestId);
    const stored = await chrome.storage.local.get(key).catch(() => ({}));
    const record = stored?.[key];
    if (!record) return null;
    const next = { ...record, status: String(status || record.status || ""), updated_at: Date.now() };
    await chrome.storage.local.set({ [key]: next }).catch(() => {});
    return next;
  }

  async function removeRecord(requestId) {
    await chrome.storage.local.remove(storageKey(requestId)).catch(() => {});
  }

  async function matchingRecord(text) {
    const normalized = normalize(text);
    if (!normalized) return null;
    const digest = await fingerprint(normalized);
    const all = await chrome.storage.local.get(null).catch(() => ({}));
    const now = Date.now();
    const expired = [];
    let match = null;
    for (const [key, value] of Object.entries(all || {})) {
      if (!key.startsWith(STORAGE_PREFIX) || !value || typeof value !== "object") continue;
      const updatedAt = Number(value.updated_at || value.created_at || 0);
      if (!updatedAt || now - updatedAt > RECORD_TTL_MS) {
        expired.push(key);
        continue;
      }
      if (!value.automation_owned || !["prepared", "draft_written"].includes(String(value.status || ""))) continue;
      if (Number(value.chars || 0) !== normalized.length || String(value.fingerprint || "") !== digest) continue;
      if (!match || updatedAt > Number(match.updated_at || match.created_at || 0)) match = { ...value, storage_key: key };
    }
    if (expired.length) chrome.storage.local.remove(expired).catch(() => {});
    return match;
  }

  function resetLegacyMarkers() {
    const v4 = globalThis.__CHAT2API_REQUEST_CONTENT_V4__;
    if (!v4) return;
    v4.lastPrompt = "";
    v4.lastAttachmentNames = [];
  }

  async function clearIfStillOwned(expectedText, record, reason) {
    if (!record) return { cleared: false, reason: "no-record" };
    if (generating()) return { cleared: false, reason: "generation-active" };
    const current = composerText();
    if (normalize(current) !== normalize(expectedText)) return { cleared: false, reason: "text-changed" };
    const verified = await matchingRecord(current);
    if (!verified || verified.request_id !== record.request_id) return { cleared: false, reason: "ownership-mismatch" };
    const removedAttachments = removeAutomationAttachmentChips();
    setComposerText(composer(), "");
    for (let attempt = 0; attempt < 20 && composerText(); attempt += 1) {
      await delay(75);
      if (attempt === 5 || attempt === 12) setComposerText(composer(), "");
    }
    if (composerText()) return { cleared: false, reason: "clear-not-confirmed" };
    await removeRecord(record.request_id);
    resetLegacyMarkers();
    state.recovered += 1;
    return { cleared: true, reason, removedAttachments, requestId: record.request_id };
  }

  async function emitDiagnostic(requestId, diagnostics) {
    if (!requestId) return;
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: { type: "chat.diagnostics", request_id: requestId, diagnostics },
      });
    } catch (_) {}
  }

  async function recoverOwnedPreflight(message) {
    const current = composerText();
    if (!current) return null;
    const record = await matchingRecord(current);
    if (!record) return null;
    const result = await clearIfStillOwned(current, record, "preflight-owned-draft");
    if (!result.cleared) throw new Error(`Persistent chat2api draft could not be recovered (${result.reason})`);
    await emitDiagnostic(message?.requestId, {
      request_controller_overlay: "draft-ownership-v43",
      persistent_draft_ownership: true,
      stale_draft_recovered: true,
      stale_draft_chars: current.length,
      stale_attachments_removed: Number(result.removedAttachments || 0),
      recovery_reason: result.reason,
    });
    return {
      stale_draft_recovered: true,
      stale_draft_chars: current.length,
      stale_attachments_removed: Number(result.removedAttachments || 0),
      persistent_draft_ownership: true,
    };
  }

  async function monitorRequest(requestId, prompt, record) {
    if (!record) return;
    const target = normalize(prompt);
    const deadline = Date.now() + Math.max(120000, Number(v5?.active?.options?.timeout_seconds || 300) * 1000 + 30000);
    let sawWritten = false;
    let writtenAt = 0;
    while (Date.now() < deadline) {
      const active = v5?.active;
      const activeMatches = Boolean(active && String(active.requestId || "") === String(requestId));
      const current = composerText();
      if (!sawWritten && current === target) {
        sawWritten = true;
        writtenAt = Date.now();
        await updateRecord(requestId, "draft_written");
      }
      if (sawWritten && activeMatches && current !== target && (generating() || !current) && Date.now() - writtenAt >= 100) {
        await removeRecord(requestId);
        return;
      }
      if (sawWritten && !activeMatches) {
        if (current === target) {
          const verified = await matchingRecord(current);
          const result = await clearIfStillOwned(current, verified, "request-ended-before-submit");
          if (result.cleared) {
            state.cleanedOnFailure += 1;
            await emitDiagnostic(requestId, {
              request_controller_overlay: "draft-ownership-v43",
              persistent_draft_ownership: true,
              stale_draft_recovered: true,
              stale_draft_chars: target.length,
              cleanup_after_request_end: true,
            });
          }
        } else {
          // Empty means ChatGPT accepted/cleared it or navigation discarded it.
          // Non-empty different text means a human or page state took over; never
          // clear that text later based on this request's ownership record.
          await removeRecord(requestId);
        }
        return;
      }
      await delay(POLL_MS);
    }
    const current = composerText();
    if (current !== target) await removeRecord(requestId);
  }

  async function startupRecovery() {
    const deadline = Date.now() + STARTUP_RECOVERY_MS;
    let lastText = "";
    while (Date.now() < deadline) {
      if (!generating()) {
        const current = composerText();
        if (current && current !== lastText) {
          lastText = current;
          const record = await matchingRecord(current);
          if (record) {
            const result = await clearIfStillOwned(current, record, "startup-restored-owned-draft");
            if (result.cleared) return;
          }
        }
      }
      await delay(500);
    }
  }

  function callListener(listener, message, sender, sendResponse) {
    try {
      const returned = listener(message, sender, sendResponse);
      if (returned !== true && returned !== false && returned !== undefined) return Boolean(returned);
      return returned;
    } catch (error) {
      sendResponse({ ok: false, error: String(error?.message || error), controller: "draft-ownership-v43" });
      return false;
    }
  }

  const listener = (message, sender, sendResponse) => {
    if (message?.type === "chat2api.request.preflight") {
      (async () => {
        const current = composerText();
        if (!current) {
          callListener(priorListener, message, sender, sendResponse);
          return;
        }
        const recovered = await recoverOwnedPreflight(message);
        if (recovered) {
          sendResponse({ ok: true, data: recovered, controller: "draft-ownership-v43" });
          return;
        }
        // Non-owned text is treated as a possible human draft. Bypass v42's broad
        // managed-tab cleanup and retain the older conservative v5/v4 preflight.
        callListener(legacyListener, message, sender, sendResponse);
      })().catch(error => sendResponse({ ok: false, error: String(error?.message || error), controller: "draft-ownership-v43" }));
      return true;
    }

    if (message?.type === "chat2api.request") {
      (async () => {
        const requestId = String(message.requestId || "");
        const prompt = String(message.prompt || "").trim();
        const record = requestId && prompt ? await saveRecord(requestId, prompt, "prepared") : null;
        const wrappedResponse = response => sendResponse(response);
        callListener(priorListener, message, sender, wrappedResponse);
        monitorRequest(requestId, prompt, record).catch(() => {});
      })().catch(error => sendResponse({ ok: false, error: String(error?.message || error), controller: "draft-ownership-v43" }));
      return true;
    }

    return callListener(priorListener, message, sender, sendResponse);
  };

  state.listener = listener;
  state.fingerprint = fingerprint;
  state.matchingRecord = matchingRecord;
  chrome.runtime.onMessage.addListener(listener);
  startupRecovery().catch(() => {});
})();
