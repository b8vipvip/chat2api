(() => {
  const KEY = "__CHAT2API_BACKGROUND_LOGGING_V3__";
  if (globalThis[KEY]) return;

  const HISTORY_KEY = "chat2apiRuntimeLogV1";
  const LEGACY_CHUNK_KEY = "chat2apiRuntimeChunkV2";
  const CURRENT_CHUNK_KEY = "chat2apiRuntimeChunkV3";
  const CHUNK_INDEX_KEY = "chat2apiRuntimeChunkIndexV3";
  const CHUNK_PREFIX = "chat2apiRuntimeChunkFileV3:";
  const AUTOMATION_DRAFT_KEY = "chat2apiLastAutomationDraftV2";
  const MAX_ENTRIES = 3000;
  const TARGET_BYTES = 200 * 1024;
  const MAX_ARCHIVED_CHUNKS = 64;
  const encoder = new TextEncoder();
  const state = {
    entries: [],
    loaded: false,
    flushTimer: null,
    queue: Promise.resolve(),
    chunk: null,
    chunkIndex: [],
    storageError: "",
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

  function normalizeIndex(value) {
    if (!Array.isArray(value)) return [];
    const seen = new Set();
    const rows = [];
    for (const item of value) {
      const day = String(item?.day || "");
      const part = Math.max(1, Number(item?.part || 1));
      if (!/^\d{8}$/.test(day)) continue;
      const id = `${day}:${part}`;
      if (seen.has(id)) continue;
      seen.add(id);
      rows.push({
        day,
        part,
        bytes: Math.max(0, Number(item?.bytes || 0)),
        lines: Math.max(0, Number(item?.lines || 0)),
        saved_at: item?.saved_at || null,
      });
    }
    rows.sort((a, b) => a.day.localeCompare(b.day) || a.part - b.part);
    return rows.slice(-MAX_ARCHIVED_CHUNKS);
  }

  function chunkStorageKey(day, part) {
    return `${CHUNK_PREFIX}${day}:${String(part).padStart(6, "0")}`;
  }

  function chunkFilename(chunk = state.chunk) {
    return `chat2api-runtime-${chunk.day}-part-${String(chunk.part).padStart(6, "0")}.log`;
  }

  async function ensureLoaded() {
    if (state.loaded) return;
    try {
      const data = await chrome.storage.local.get([HISTORY_KEY, LEGACY_CHUNK_KEY, CURRENT_CHUNK_KEY, CHUNK_INDEX_KEY]);
      state.entries = Array.isArray(data[HISTORY_KEY]) ? data[HISTORY_KEY].slice(-MAX_ENTRIES) : [];
      state.chunk = normalizeChunk(data[CURRENT_CHUNK_KEY] || data[LEGACY_CHUNK_KEY]);
      state.chunkIndex = normalizeIndex(data[CHUNK_INDEX_KEY]);
      state.loaded = true;
      if (!data[CURRENT_CHUNK_KEY] && data[LEGACY_CHUNK_KEY]) {
        await chrome.storage.local.set({ [CURRENT_CHUNK_KEY]: state.chunk, [CHUNK_INDEX_KEY]: state.chunkIndex });
        await chrome.storage.local.remove(LEGACY_CHUNK_KEY);
      }
    } catch (error) {
      state.entries = [];
      state.chunk = newChunk();
      state.chunkIndex = [];
      state.storageError = String(error?.message || error);
      state.loaded = true;
    }
  }

  async function persistNow() {
    await ensureLoaded();
    try {
      state.chunk.last_saved_at = new Date().toISOString();
      await chrome.storage.local.set({
        [HISTORY_KEY]: state.entries.slice(-MAX_ENTRIES),
        [CURRENT_CHUNK_KEY]: state.chunk,
        [CHUNK_INDEX_KEY]: state.chunkIndex,
      });
      state.storageError = "";
    } catch (error) {
      state.storageError = String(error?.message || error);
    }
  }

  function scheduleFlush() {
    if (state.flushTimer) return;
    state.flushTimer = setTimeout(async () => {
      state.flushTimer = null;
      await persistNow();
    }, 700);
  }

  async function archiveCurrentChunk() {
    await ensureLoaded();
    if (!state.chunk?.lines?.length) return null;
    const savedAt = new Date().toISOString();
    const key = chunkStorageKey(state.chunk.day, state.chunk.part);
    const archived = {
      day: state.chunk.day,
      part: state.chunk.part,
      lines: state.chunk.lines.slice(),
      bytes: state.chunk.bytes,
      saved_at: savedAt,
    };
    try {
      await chrome.storage.local.set({ [key]: archived });
      const id = `${archived.day}:${archived.part}`;
      state.chunkIndex = state.chunkIndex.filter(item => `${item.day}:${item.part}` !== id);
      state.chunkIndex.push({
        day: archived.day,
        part: archived.part,
        bytes: archived.bytes,
        lines: archived.lines.length,
        saved_at: savedAt,
      });
      state.chunkIndex.sort((a, b) => a.day.localeCompare(b.day) || a.part - b.part);
      const overflow = state.chunkIndex.length - MAX_ARCHIVED_CHUNKS;
      if (overflow > 0) {
        const removed = state.chunkIndex.splice(0, overflow);
        await chrome.storage.local.remove(removed.map(item => chunkStorageKey(item.day, item.part)));
      }
      await chrome.storage.local.set({ [CHUNK_INDEX_KEY]: state.chunkIndex });
      state.storageError = "";
      return archived;
    } catch (error) {
      state.storageError = String(error?.message || error);
      return null;
    }
  }

  async function rolloverForDayIfNeeded() {
    const today = localDay();
    if (state.chunk.day === today) return;
    await persistNow();
    await archiveCurrentChunk();
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

    // JSONL keeps every runtime event on exactly one complete line. The whole
    // record is appended before the 200 KiB rollover check, so no event is split.
    const line = JSON.stringify(entry);
    state.chunk.lines.push(line);
    state.chunk.bytes += bytesOf(line + "\n");
    scheduleFlush();

    if (state.chunk.bytes >= TARGET_BYTES) {
      await persistNow();
      await archiveCurrentChunk();
      const nextPart = state.chunk.part + 1;
      state.chunk = newChunk(state.chunk.day, nextPart);
      await persistNow();
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

  async function exportStoredChunks() {
    await ensureLoaded();
    await persistNow();
    const keys = state.chunkIndex.map(item => chunkStorageKey(item.day, item.part));
    const stored = keys.length ? await chrome.storage.local.get(keys) : {};
    const chunks = [];
    for (const meta of state.chunkIndex) {
      const value = stored[chunkStorageKey(meta.day, meta.part)];
      const chunk = normalizeChunk(value);
      if (!chunk.lines.length) continue;
      chunks.push({
        filename: chunkFilename(chunk),
        day: chunk.day,
        part: chunk.part,
        bytes: chunk.bytes,
        lines: chunk.lines.length,
        saved_at: value?.saved_at || meta.saved_at || null,
        text: chunk.lines.join("\n") + "\n",
      });
    }
    if (state.chunk.lines.length) {
      chunks.push({
        filename: chunkFilename(state.chunk),
        day: state.chunk.day,
        part: state.chunk.part,
        bytes: state.chunk.bytes,
        lines: state.chunk.lines.length,
        saved_at: state.chunk.last_saved_at,
        current: true,
        text: state.chunk.lines.join("\n") + "\n",
      });
    }
    return chunks;
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
        const chunks = await exportStoredChunks();
        const settings = await chrome.storage.local.get(["clientId", "extensionName", "boundTabId", "socketState", "currentModel", "lastRequestedModel"]);
        sendResponse({
          ok: true,
          data: {
            report_type: "chat2api-extension-runtime-log",
            report_version: 3,
            generated_at: new Date().toISOString(),
            extension_version: chrome.runtime.getManifest().version,
            settings: clean(settings),
            auto_local_log: {
              enabled: true,
              backend: "chrome.storage.local",
              silent_persistence: true,
              automatic_downloads: false,
              target_bytes: TARGET_BYTES,
              max_archived_chunks: MAX_ARCHIVED_CHUNKS,
              current_chunk: chunkFilename(),
              current_bytes: state.chunk.bytes,
              current_lines: state.chunk.lines.length,
              archived_chunks: state.chunkIndex.length,
              last_saved_at: state.chunk.last_saved_at,
              last_error: state.storageError || null,
              format: "JSONL; one complete JSON event per line; rollover occurs only after a complete line",
            },
            chunks,
            entries: state.entries.slice(),
          },
        });
      }).catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }

    if (message?.type === "popup.logs.clear") {
      state.queue.then(async () => {
        await ensureLoaded();
        const archiveKeys = state.chunkIndex.map(item => chunkStorageKey(item.day, item.part));
        if (archiveKeys.length) await chrome.storage.local.remove(archiveKeys);
        state.entries = [];
        state.chunkIndex = [];
        state.chunk = newChunk(localDay(), 1);
        state.storageError = "";
        await chrome.storage.local.set({
          [HISTORY_KEY]: [],
          [CURRENT_CHUNK_KEY]: state.chunk,
          [CHUNK_INDEX_KEY]: [],
        });
        await chrome.storage.local.remove(LEGACY_CHUNK_KEY);
        sendResponse({ ok: true, note: "Silent locally stored runtime logs were cleared. Manually exported files are not deleted." });
      }).catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }
    return false;
  });

  ensureLoaded().then(async () => {
    await append("background", "service-worker-ready", {
      extension_version: chrome.runtime.getManifest().version,
      auto_local_log: true,
      storage_backend: "chrome.storage.local",
      automatic_downloads: false,
      log_target_bytes: TARGET_BYTES,
      log_format: "jsonl-complete-record-rollover",
    });
  }).catch(() => {});
})();
