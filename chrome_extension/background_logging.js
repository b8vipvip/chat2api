(() => {
  const KEY = "__CHAT2API_BACKGROUND_LOGGING_V2__";
  if (globalThis[KEY]) return;

  const HISTORY_KEY = "chat2apiRuntimeLogV1";
  const CHUNK_KEY = "chat2apiRuntimeChunkV2";
  const AUTOMATION_DRAFT_KEY = "chat2apiLastAutomationDraftV2";
  const MAX_ENTRIES = 3000;
  const TARGET_BYTES = 200 * 1024;
  const DISK_FLUSH_MS = 5000;
  const encoder = new TextEncoder();
  const state = {
    entries: [],
    loaded: false,
    flushTimer: null,
    diskTimer: null,
    diskDirty: false,
    diskError: "",
    queue: Promise.resolve(),
    chunk: null,
  };
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

  const bytesOf = value => encoder.encode(String(value || "")).byteLength;
  const pad = value => String(value).padStart(2, "0");

  function localDay(date = new Date()) {
    return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}`;
  }

  function newChunk(day = localDay(), part = 1) {
    return { day, part, lines: [], bytes: 0, last_saved_at: null };
  }

  function normalizeChunk(value) {
    if (!value || typeof value !== "object") return newChunk();
    const lines = Array.isArray(value.lines) ? value.lines.map(item => String(item || "")).filter(Boolean) : [];
    return {
      day: /^\d{8}$/.test(String(value.day || "")) ? String(value.day) : localDay(),
      part: Math.max(1, Number(value.part || 1)),
      lines,
      bytes: lines.reduce((sum, line) => sum + bytesOf(line + "\n"), 0),
      last_saved_at: value.last_saved_at || null,
    };
  }

  async function ensureLoaded() {
    if (state.loaded) return;
    try {
      const data = await chrome.storage.local.get([HISTORY_KEY, CHUNK_KEY]);
      state.entries = Array.isArray(data[HISTORY_KEY]) ? data[HISTORY_KEY].slice(-MAX_ENTRIES) : [];
      state.chunk = normalizeChunk(data[CHUNK_KEY]);
    } catch (_) {
      state.entries = [];
      state.chunk = newChunk();
    }
    state.loaded = true;
  }

  async function persistNow() {
    await ensureLoaded();
    try {
      await chrome.storage.local.set({
        [HISTORY_KEY]: state.entries.slice(-MAX_ENTRIES),
        [CHUNK_KEY]: state.chunk,
      });
    } catch (_) {}
  }

  function scheduleFlush() {
    if (state.flushTimer) return;
    state.flushTimer = setTimeout(async () => {
      state.flushTimer = null;
      await persistNow();
    }, 700);
  }

  function chunkFilename(chunk = state.chunk) {
    return `chat2api-logs/chat2api-runtime-${chunk.day}-part-${String(chunk.part).padStart(6, "0")}.log`;
  }

  async function saveChunkSnapshot() {
    await ensureLoaded();
    if (!state.chunk?.lines?.length || !chrome.downloads?.download) return null;
    const text = state.chunk.lines.join("\n") + "\n";
    const url = `data:text/plain;charset=utf-8,${encodeURIComponent(text)}`;
    try {
      const downloadId = await chrome.downloads.download({
        url,
        filename: chunkFilename(),
        saveAs: false,
        conflictAction: "overwrite",
      });
      state.chunk.last_saved_at = new Date().toISOString();
      state.diskDirty = false;
      state.diskError = "";
      scheduleFlush();
      return downloadId;
    } catch (error) {
      state.diskError = String(error?.message || error);
      return null;
    }
  }

  function scheduleDiskSave() {
    state.diskDirty = true;
    if (state.diskTimer) return;
    state.diskTimer = setTimeout(() => {
      state.diskTimer = null;
      state.queue = state.queue.then(() => saveChunkSnapshot()).catch(() => {});
    }, DISK_FLUSH_MS);
  }

  async function rolloverForDayIfNeeded() {
    const today = localDay();
    if (state.chunk.day === today) return;
    if (state.chunk.lines.length) await saveChunkSnapshot();
    state.chunk = newChunk(today, 1);
    await persistNow();
  }

  async function appendUnlocked(component, action, data = {}, level = "info") {
    await ensureLoaded();
    await rolloverForDayIfNeeded();
    const entry = {
      at: new Date().toISOString(),
      level,
      component,
      action,
      data: clean(data),
    };
    state.entries.push(entry);
    if (state.entries.length > MAX_ENTRIES) state.entries.splice(0, state.entries.length - MAX_ENTRIES);

    // JSONL keeps every runtime event on exactly one complete line. We append the
    // whole line first and only then roll the file, so a record is never cut in half.
    const line = JSON.stringify(entry);
    state.chunk.lines.push(line);
    state.chunk.bytes += bytesOf(line + "\n");
    scheduleFlush();

    if (state.chunk.bytes >= TARGET_BYTES) {
      await persistNow();
      await saveChunkSnapshot();
      const nextPart = state.chunk.part + 1;
      state.chunk = newChunk(state.chunk.day, nextPart);
      await persistNow();
    } else {
      scheduleDiskSave();
    }
    return entry;
  }

  function append(component, action, data = {}, level = "info") {
    const task = state.queue.then(() => appendUnlocked(component, action, data, level));
    state.queue = task.catch(() => {});
    return task;
  }

  async function sha256(text) {
    const digest = await crypto.subtle.digest("SHA-256", encoder.encode(String(text || "")));
    return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join("");
  }

  async function rememberAutomationDraft(message) {
    if (message?.type !== "chat.request") return;
    const prompt = String(message?.prompt || "").trim();
    if (!prompt) return;
    try {
      await chrome.storage.local.set({
        [AUTOMATION_DRAFT_KEY]: {
          sha256: await sha256(prompt.replace(/\s+/g, " ").trim()),
          chars: prompt.replace(/\s+/g, " ").trim().length,
          request_id: message.request_id || null,
          at: new Date().toISOString(),
        },
      });
    } catch (_) {}
  }

  const baseHandleServerMessage = typeof handleServerMessage === "function" ? handleServerMessage : null;
  if (baseHandleServerMessage) {
    handleServerMessage = async function handleServerMessageWithRuntimeLog(message) {
      await rememberAutomationDraft(message);
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
      state.queue.then(async () => {
        await ensureLoaded();
        await saveChunkSnapshot();
        const settings = await chrome.storage.local.get(["clientId", "extensionName", "boundTabId", "socketState", "currentModel", "lastRequestedModel"]);
        sendResponse({
          ok: true,
          data: {
            report_type: "chat2api-extension-runtime-log",
            report_version: 2,
            generated_at: new Date().toISOString(),
            extension_version: chrome.runtime.getManifest().version,
            settings: clean(settings),
            auto_local_log: {
              enabled: Boolean(chrome.downloads?.download),
              target_bytes: TARGET_BYTES,
              current_file: chunkFilename(),
              current_bytes: state.chunk.bytes,
              current_lines: state.chunk.lines.length,
              last_saved_at: state.chunk.last_saved_at,
              last_error: state.diskError || null,
              format: "JSONL; one complete JSON event per line; rollover occurs only after a complete line",
            },
            entries: state.entries.slice(),
          },
        });
      }).catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }

    if (message?.type === "popup.logs.clear") {
      state.queue.then(async () => {
        await ensureLoaded();
        const nextPart = state.chunk.day === localDay() ? state.chunk.part + 1 : 1;
        state.entries = [];
        state.chunk = newChunk(localDay(), nextPart);
        state.diskDirty = false;
        await chrome.storage.local.set({ [HISTORY_KEY]: [], [CHUNK_KEY]: state.chunk });
        sendResponse({ ok: true, note: "Downloaded log files are not deleted; only the extension cache/current chunk was reset." });
      }).catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }
    return false;
  });

  ensureLoaded().then(async () => {
    if (state.chunk.lines.length) scheduleDiskSave();
    await append("background", "service-worker-ready", {
      extension_version: chrome.runtime.getManifest().version,
      auto_local_log: true,
      log_target_bytes: TARGET_BYTES,
      log_format: "jsonl-complete-record-rollover",
    });
  }).catch(() => {});
})();
