(() => {
  const KEY = "__CHAT2API_BACKGROUND_LOGGING_V1__";
  if (globalThis[KEY]) return;

  const STORAGE_KEY = "chat2apiRuntimeLogV1";
  const MAX_ENTRIES = 3000;
  const state = { entries: [], loaded: false, flushTimer: null };
  globalThis[KEY] = state;

  function clean(value, depth = 0) {
    if (depth > 5) return "[depth-limit]";
    if (value == null || typeof value === "number" || typeof value === "boolean") return value;
    if (typeof value === "string") return value.length > 800 ? value.slice(0, 800) + "…" : value;
    if (Array.isArray(value)) return value.slice(0, 40).map(item => clean(item, depth + 1));
    if (typeof value === "object") {
      const out = {};
      for (const [key, item] of Object.entries(value)) {
        if (/token|secret|pairing|authorization|api[_-]?key|b64|base64|data_base64|audio_data|image_data/i.test(key)) {
          out[key] = "[redacted]";
          continue;
        }
        if (/prompt|content|delta|text$/i.test(key) && typeof item === "string") {
          out[`${key}_chars`] = item.length;
          continue;
        }
        out[key] = clean(item, depth + 1);
      }
      return out;
    }
    return String(value);
  }

  async function ensureLoaded() {
    if (state.loaded) return;
    try {
      const data = await chrome.storage.local.get(STORAGE_KEY);
      state.entries = Array.isArray(data[STORAGE_KEY]) ? data[STORAGE_KEY].slice(-MAX_ENTRIES) : [];
    } catch (_) {
      state.entries = [];
    }
    state.loaded = true;
  }

  function scheduleFlush() {
    if (state.flushTimer) return;
    state.flushTimer = setTimeout(async () => {
      state.flushTimer = null;
      try { await chrome.storage.local.set({ [STORAGE_KEY]: state.entries.slice(-MAX_ENTRIES) }); } catch (_) {}
    }, 700);
  }

  async function append(component, action, data = {}, level = "info") {
    await ensureLoaded();
    state.entries.push({
      at: new Date().toISOString(),
      level,
      component,
      action,
      data: clean(data),
    });
    if (state.entries.length > MAX_ENTRIES) state.entries.splice(0, state.entries.length - MAX_ENTRIES);
    scheduleFlush();
  }

  const baseHandleServerMessage = typeof handleServerMessage === "function" ? handleServerMessage : null;
  if (baseHandleServerMessage) {
    handleServerMessage = async function handleServerMessageWithRuntimeLog(message) {
      await append("background", "server-message", {
        type: message?.type || "",
        request_id: message?.request_id || null,
        model: message?.options?.model || message?.model || null,
        attachment_count: Array.isArray(message?.attachments) ? message.attachments.length : 0,
      });
      try {
        const result = await baseHandleServerMessage(message);
        await append("background", "server-message-dispatched", { type: message?.type || "", request_id: message?.request_id || null });
        return result;
      } catch (error) {
        await append("background", "server-message-error", { type: message?.type || "", request_id: message?.request_id || null, error: String(error?.message || error) }, "error");
        throw error;
      }
    };
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type === "chat2api.log.append") {
      append(
        message.entry?.component || "content",
        message.entry?.action || "event",
        {
          ...(message.entry?.data || {}),
          request_id: message.entry?.request_id || message.entry?.data?.request_id || null,
          sender_tab_id: sender?.tab?.id ?? null,
          sender_url: sender?.tab?.url || "",
        },
        message.entry?.level || "info",
      ).then(() => sendResponse({ ok: true })).catch(() => sendResponse({ ok: false }));
      return true;
    }

    if (message?.type === "chat2api.event") {
      const event = message.event || {};
      append("content-event", event.type || "chat2api.event", {
        request_id: event.request_id || null,
        kind: event.kind || null,
        stage: event.stage || event.diagnostics?.submit_stage || event.diagnostics?.voice_stage || event.diagnostics?.image_stage || null,
        diagnostics: event.diagnostics || null,
        error: event.error || null,
        sender_tab_id: sender?.tab?.id ?? null,
        sender_url: sender?.tab?.url || "",
      }, event.error ? "error" : "info").catch(() => {});
      return false;
    }

    if (message?.type === "popup.logs.export") {
      ensureLoaded().then(async () => {
        const settings = await chrome.storage.local.get(["clientId", "extensionName", "boundTabId", "socketState", "currentModel", "lastRequestedModel"]);
        sendResponse({
          ok: true,
          data: {
            report_type: "chat2api-extension-runtime-log",
            report_version: 1,
            generated_at: new Date().toISOString(),
            extension_version: chrome.runtime.getManifest().version,
            settings: clean(settings),
            entries: state.entries.slice(),
          },
        });
      }).catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }

    if (message?.type === "popup.logs.clear") {
      ensureLoaded().then(async () => {
        state.entries = [];
        await chrome.storage.local.set({ [STORAGE_KEY]: [] });
        sendResponse({ ok: true });
      }).catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }
    return false;
  });

  ensureLoaded().then(() => append("background", "service-worker-ready", { extension_version: chrome.runtime.getManifest().version })).catch(() => {});
})();
