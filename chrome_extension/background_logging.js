(() => {
  const KEY = "__CHAT2API_BACKGROUND_LOGGING_V4__";
  if (globalThis[KEY]) return;

  const HISTORY_KEY = "chat2apiRuntimeLogV1";
  const ACTIVE_RUNS_KEY = "chat2apiRuntimeActiveRunsV4";
  const RUN_INDEX_KEY = "chat2apiRuntimeRunIndexV4";
  const RUN_PART_PREFIX = "chat2apiRuntimeRunPartV4:";
  const LEGACY_CHUNK_KEY = "chat2apiRuntimeChunkV2";
  const LEGACY_CURRENT_CHUNK_KEY = "chat2apiRuntimeChunkV3";
  const LEGACY_CHUNK_INDEX_KEY = "chat2apiRuntimeChunkIndexV3";
  const LEGACY_CHUNK_PREFIX = "chat2apiRuntimeChunkFileV3:";
  const AUTOMATION_DRAFT_KEY = "chat2apiLastAutomationDraftV2";
  const RUN_IDLE_MS = 120000;
  const TARGET_BYTES = 200 * 1024;
  const MAX_ENTRIES = 3000;
  const MAX_FINALIZED_RUNS = 64;
  const ALARM_PREFIX = "chat2api-log-finalize:";
  const encoder = new TextEncoder();

  const state = {
    loaded: false,
    entries: [],
    activeRuns: new Map(),
    requestToRun: new Map(),
    runIndex: [],
    queue: Promise.resolve(),
    flushTimer: null,
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
        const safeRoutingIdentity = key === "api_key_id" || key === "api_key_kind" || key === "conversation_api_key_id";
        if (!safeRoutingIdentity && /token|secret|pairing|authorization|api[_-]?key|b64|base64|data_base64|audio_data|image_data/i.test(key)) {
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
  const nowIso = () => new Date().toISOString();
  const requestTypes = new Set(["chat.request", "image.request", "voice.request"]);
  const terminalTypes = new Set(["chat.completed", "chat.error", "chat.cancelled", "image.completed", "image.error", "image.cancelled"]);

  function runPartKey(runId, partIndex) {
    return `${RUN_PART_PREFIX}${runId}:${String(partIndex).padStart(6, "0")}`;
  }

  function alarmName(runId) {
    return `${ALARM_PREFIX}${runId}`;
  }

  function newRunId(keyId) {
    const compact = String(keyId || "unrouted").replace(/[^a-zA-Z0-9._-]+/g, "-").slice(0, 28) || "unrouted";
    const random = typeof crypto.randomUUID === "function" ? crypto.randomUUID().slice(0, 8) : Math.random().toString(36).slice(2, 10);
    return `run-${Date.now().toString(36)}-${compact}-${random}`;
  }

  function createRun(keyId, keyKind) {
    const at = nowIso();
    return {
      run_id: newRunId(keyId),
      api_key_id: keyId || "unrouted",
      api_key_kind: keyKind || "unknown",
      started_at: at,
      last_activity_at: at,
      ended_at: null,
      idle_deadline: null,
      request_count: 0,
      active_request_ids: [],
      request_ids: [],
      current_part: 1,
      current_lines: [],
      current_bytes: 0,
      archived_parts: 0,
      total_lines: 0,
      total_bytes: 0,
      finalized: false,
    };
  }

  function normalizeRun(value) {
    if (!value || typeof value !== "object" || !value.run_id) return null;
    const currentLines = Array.isArray(value.current_lines) ? value.current_lines.map(item => String(item || "")).filter(Boolean) : [];
    const activeIds = Array.isArray(value.active_request_ids) ? value.active_request_ids.map(String).filter(Boolean) : [];
    const requestIds = Array.isArray(value.request_ids) ? value.request_ids.map(String).filter(Boolean) : [];
    return {
      run_id: String(value.run_id),
      api_key_id: String(value.api_key_id || "unrouted"),
      api_key_kind: String(value.api_key_kind || "unknown"),
      started_at: value.started_at || nowIso(),
      last_activity_at: value.last_activity_at || value.started_at || nowIso(),
      ended_at: value.ended_at || null,
      idle_deadline: Number(value.idle_deadline || 0) || null,
      request_count: Math.max(0, Number(value.request_count || requestIds.length || 0)),
      active_request_ids: [...new Set(activeIds)],
      request_ids: [...new Set(requestIds)],
      current_part: Math.max(1, Number(value.current_part || 1)),
      current_lines: currentLines,
      current_bytes: currentLines.reduce((sum, line) => sum + bytesOf(line + "\n"), 0),
      archived_parts: Math.max(0, Number(value.archived_parts || 0)),
      total_lines: Math.max(currentLines.length, Number(value.total_lines || currentLines.length)),
      total_bytes: Math.max(0, Number(value.total_bytes || 0)),
      finalized: false,
    };
  }

  function normalizeRunIndex(value) {
    if (!Array.isArray(value)) return [];
    const seen = new Set();
    const rows = [];
    for (const item of value) {
      const runId = String(item?.run_id || "");
      if (!runId || seen.has(runId)) continue;
      seen.add(runId);
      rows.push({
        run_id: runId,
        api_key_id: String(item?.api_key_id || "unrouted"),
        api_key_kind: String(item?.api_key_kind || "unknown"),
        started_at: item?.started_at || null,
        ended_at: item?.ended_at || null,
        request_count: Math.max(0, Number(item?.request_count || 0)),
        part_count: Math.max(0, Number(item?.part_count || 0)),
        total_lines: Math.max(0, Number(item?.total_lines || 0)),
        total_bytes: Math.max(0, Number(item?.total_bytes || 0)),
      });
    }
    rows.sort((a, b) => String(a.ended_at || a.started_at || "").localeCompare(String(b.ended_at || b.started_at || "")));
    return rows.slice(-MAX_FINALIZED_RUNS);
  }

  function serializeActiveRuns() {
    return [...state.activeRuns.values()].map(run => ({
      ...run,
      active_request_ids: [...run.active_request_ids],
      request_ids: [...run.request_ids],
      current_lines: [...run.current_lines],
    }));
  }

  async function persistNow() {
    if (!state.loaded) return;
    try {
      await chrome.storage.local.set({
        [HISTORY_KEY]: state.entries.slice(-MAX_ENTRIES),
        [ACTIVE_RUNS_KEY]: serializeActiveRuns(),
        [RUN_INDEX_KEY]: state.runIndex,
      });
      state.storageError = "";
    } catch (error) {
      state.storageError = String(error?.message || error);
    }
  }

  function scheduleFlush() {
    if (state.flushTimer) return;
    state.flushTimer = setTimeout(() => {
      state.flushTimer = null;
      state.queue = state.queue.then(() => persistNow()).catch(() => {});
    }, 700);
  }

  async function ensureLoaded() {
    if (state.loaded) return;
    try {
      const data = await chrome.storage.local.get([HISTORY_KEY, ACTIVE_RUNS_KEY, RUN_INDEX_KEY]);
      state.entries = Array.isArray(data[HISTORY_KEY]) ? data[HISTORY_KEY].slice(-MAX_ENTRIES) : [];
      state.runIndex = normalizeRunIndex(data[RUN_INDEX_KEY]);
      for (const item of Array.isArray(data[ACTIVE_RUNS_KEY]) ? data[ACTIVE_RUNS_KEY] : []) {
        const run = normalizeRun(item);
        if (!run) continue;
        state.activeRuns.set(run.api_key_id, run);
        for (const requestId of run.request_ids) state.requestToRun.set(requestId, run.run_id);
      }
      state.loaded = true;
      const now = Date.now();
      for (const run of state.activeRuns.values()) {
        if (run.active_request_ids.length) continue;
        if (!run.idle_deadline) run.idle_deadline = now + RUN_IDLE_MS;
        if (run.idle_deadline <= now) {
          setTimeout(() => { state.queue = state.queue.then(() => finalizeRun(run.run_id, "startup-expired")).catch(() => {}); }, 0);
        } else {
          await chrome.alarms.create(alarmName(run.run_id), { when: run.idle_deadline });
        }
      }
    } catch (error) {
      state.loaded = true;
      state.entries = [];
      state.activeRuns.clear();
      state.requestToRun.clear();
      state.runIndex = [];
      state.storageError = String(error?.message || error);
    }
  }

  function runById(runId) {
    for (const run of state.activeRuns.values()) if (run.run_id === runId) return run;
    return null;
  }

  function runForRequest(requestId) {
    if (!requestId) return null;
    const runId = state.requestToRun.get(String(requestId));
    return runId ? runById(runId) : null;
  }

  async function archiveCurrentPart(run) {
    if (!run?.current_lines?.length) return null;
    const part = {
      run_id: run.run_id,
      api_key_id: run.api_key_id,
      api_key_kind: run.api_key_kind,
      part: run.current_part,
      lines: run.current_lines.slice(),
      bytes: run.current_bytes,
      saved_at: nowIso(),
    };
    await chrome.storage.local.set({ [runPartKey(run.run_id, run.current_part)]: part });
    run.archived_parts += 1;
    run.current_part += 1;
    run.current_lines = [];
    run.current_bytes = 0;
    return part;
  }

  async function appendToRun(run, component, action, data = {}, level = "info", requestId = null) {
    if (!run || run.finalized) return null;
    const entry = {
      at: nowIso(),
      level,
      run_id: run.run_id,
      api_key_id: run.api_key_id,
      api_key_kind: run.api_key_kind,
      request_id: requestId || data?.request_id || null,
      component,
      action,
      data: clean(data),
    };
    const line = JSON.stringify(entry);
    const lineBytes = bytesOf(line + "\n");

    // Roll before appending the next complete JSONL line. A single event is
    // therefore never split across two ~200 KiB parts.
    if (run.current_lines.length && run.current_bytes + lineBytes > TARGET_BYTES) {
      await archiveCurrentPart(run);
    }

    run.current_lines.push(line);
    run.current_bytes += lineBytes;
    run.total_lines += 1;
    run.total_bytes += lineBytes;
    run.last_activity_at = entry.at;

    state.entries.push(entry);
    if (state.entries.length > MAX_ENTRIES) state.entries.splice(0, state.entries.length - MAX_ENTRIES);
    scheduleFlush();
    return entry;
  }

  async function clearFinalizeAlarm(run) {
    if (!run) return;
    try { await chrome.alarms.clear(alarmName(run.run_id)); } catch (_) {}
    run.idle_deadline = null;
  }

  async function scheduleRunFinalize(run) {
    if (!run || run.active_request_ids.length) return;
    run.idle_deadline = Date.now() + RUN_IDLE_MS;
    await chrome.alarms.create(alarmName(run.run_id), { when: run.idle_deadline });
    await persistNow();
  }

  async function startRequest(message) {
    await ensureLoaded();
    const requestId = String(message?.request_id || "");
    if (!requestId) return null;
    const keyId = String(message?.routing?.api_key_id || "unrouted");
    const keyKind = String(message?.routing?.api_key_kind || "unknown");
    let run = state.activeRuns.get(keyId);
    if (!run || run.finalized) {
      run = createRun(keyId, keyKind);
      state.activeRuns.set(keyId, run);
    }
    await clearFinalizeAlarm(run);
    if (!run.active_request_ids.includes(requestId)) run.active_request_ids.push(requestId);
    if (!run.request_ids.includes(requestId)) run.request_ids.push(requestId);
    run.request_count = run.request_ids.length;
    run.last_activity_at = nowIso();
    state.requestToRun.set(requestId, run.run_id);
    await appendToRun(run, "background", "request_start", {
      request_id: requestId,
      type: message.type,
      model: message?.options?.model || message?.model || null,
      attachment_count: Array.isArray(message?.attachments) ? message.attachments.length : (message?.audio ? 1 : 0),
      routing: {
        api_key_id: keyId,
        api_key_kind: keyKind,
      },
    }, "info", requestId);
    await persistNow();
    return run;
  }

  async function finishRequest(requestId, status, detail = {}) {
    await ensureLoaded();
    const id = String(requestId || "");
    const run = runForRequest(id);
    if (!run || !run.active_request_ids.includes(id)) return;
    await appendToRun(run, "background", "request_end", { request_id: id, status, ...detail }, status === "error" ? "error" : "info", id);
    run.active_request_ids = run.active_request_ids.filter(value => value !== id);
    run.last_activity_at = nowIso();
    if (!run.active_request_ids.length) await scheduleRunFinalize(run);
    else await persistNow();
  }

  async function finalizeRun(runId, reason = "idle") {
    await ensureLoaded();
    const run = runById(runId);
    if (!run || run.finalized || run.active_request_ids.length) return false;
    if (run.idle_deadline && reason !== "manual" && run.idle_deadline > Date.now()) return false;

    await appendToRun(run, "background", "run_finalized", {
      reason,
      idle_ms: RUN_IDLE_MS,
      request_count: run.request_count,
    });
    await archiveCurrentPart(run);
    run.finalized = true;
    run.ended_at = nowIso();
    run.idle_deadline = null;
    try { await chrome.alarms.clear(alarmName(run.run_id)); } catch (_) {}

    const summary = {
      run_id: run.run_id,
      api_key_id: run.api_key_id,
      api_key_kind: run.api_key_kind,
      started_at: run.started_at,
      ended_at: run.ended_at,
      request_count: run.request_count,
      part_count: run.archived_parts,
      total_lines: run.total_lines,
      total_bytes: run.total_bytes,
    };
    state.runIndex = state.runIndex.filter(item => item.run_id !== run.run_id);
    state.runIndex.push(summary);
    state.runIndex.sort((a, b) => String(a.ended_at || "").localeCompare(String(b.ended_at || "")));

    for (const requestId of run.request_ids) state.requestToRun.delete(requestId);
    state.activeRuns.delete(run.api_key_id);

    while (state.runIndex.length > MAX_FINALIZED_RUNS) {
      const removed = state.runIndex.shift();
      if (!removed) break;
      const keys = [];
      for (let part = 1; part <= removed.part_count; part += 1) keys.push(runPartKey(removed.run_id, part));
      if (keys.length) await chrome.storage.local.remove(keys);
    }
    await persistNow();
    return true;
  }

  async function appendForRequest(requestId, component, action, data = {}, level = "info") {
    await ensureLoaded();
    const run = runForRequest(requestId);
    if (!run) return null;
    return appendToRun(run, component, action, data, level, requestId);
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
          at: nowIso(),
        },
      });
    } catch (_) {}
  }

  async function readRunParts(meta) {
    const count = Math.max(0, Number(meta?.part_count || 0));
    const keys = [];
    for (let part = 1; part <= count; part += 1) keys.push(runPartKey(meta.run_id, part));
    const stored = keys.length ? await chrome.storage.local.get(keys) : {};
    const parts = [];
    for (let part = 1; part <= count; part += 1) {
      const value = stored[runPartKey(meta.run_id, part)];
      if (!value?.lines?.length) continue;
      parts.push({
        filename: `chat2api-runtime-${meta.run_id}-part-${String(part).padStart(6, "0")}.log`,
        run_id: meta.run_id,
        api_key_id: meta.api_key_id,
        part,
        bytes: Number(value.bytes || 0),
        lines: value.lines.length,
        saved_at: value.saved_at || null,
        text: value.lines.join("\n") + "\n",
      });
    }
    return parts;
  }

  async function exportLegacyChunks() {
    const data = await chrome.storage.local.get([LEGACY_CHUNK_KEY, LEGACY_CURRENT_CHUNK_KEY, LEGACY_CHUNK_INDEX_KEY]);
    const index = Array.isArray(data[LEGACY_CHUNK_INDEX_KEY]) ? data[LEGACY_CHUNK_INDEX_KEY] : [];
    const keys = index.map(item => `${LEGACY_CHUNK_PREFIX}${item.day}:${String(item.part || 1).padStart(6, "0")}`);
    const stored = keys.length ? await chrome.storage.local.get(keys) : {};
    const chunks = [];
    for (const item of index) {
      const key = `${LEGACY_CHUNK_PREFIX}${item.day}:${String(item.part || 1).padStart(6, "0")}`;
      const value = stored[key];
      if (!Array.isArray(value?.lines) || !value.lines.length) continue;
      chunks.push({ legacy: true, day: item.day, part: item.part, lines: value.lines.length, bytes: value.bytes || 0, text: value.lines.join("\n") + "\n" });
    }
    const current = data[LEGACY_CURRENT_CHUNK_KEY] || data[LEGACY_CHUNK_KEY];
    if (Array.isArray(current?.lines) && current.lines.length) {
      chunks.push({ legacy: true, current: true, day: current.day, part: current.part, lines: current.lines.length, bytes: current.bytes || 0, text: current.lines.join("\n") + "\n" });
    }
    return chunks;
  }

  async function exportStoredRuns() {
    await ensureLoaded();
    await persistNow();
    const runs = [];
    const chunks = [];

    for (const meta of state.runIndex) {
      const parts = await readRunParts(meta);
      chunks.push(...parts);
      runs.push({ ...meta, finalized: true, parts });
    }

    for (const run of state.activeRuns.values()) {
      const archivedMeta = { run_id: run.run_id, api_key_id: run.api_key_id, api_key_kind: run.api_key_kind, part_count: run.archived_parts };
      const parts = await readRunParts(archivedMeta);
      if (run.current_lines.length) {
        const current = {
          filename: `chat2api-runtime-${run.run_id}-part-${String(run.current_part).padStart(6, "0")}.log`,
          run_id: run.run_id,
          api_key_id: run.api_key_id,
          part: run.current_part,
          current: true,
          bytes: run.current_bytes,
          lines: run.current_lines.length,
          saved_at: run.last_activity_at,
          text: run.current_lines.join("\n") + "\n",
        };
        parts.push(current);
      }
      chunks.push(...parts);
      runs.push({
        run_id: run.run_id,
        api_key_id: run.api_key_id,
        api_key_kind: run.api_key_kind,
        started_at: run.started_at,
        ended_at: null,
        request_count: run.request_count,
        active_request_count: run.active_request_ids.length,
        idle_deadline: run.idle_deadline,
        finalized: false,
        parts,
      });
    }

    return { runs, chunks, legacy_chunks: await exportLegacyChunks() };
  }

  const baseHandleServerMessage = typeof handleServerMessage === "function" ? handleServerMessage : null;
  if (baseHandleServerMessage) {
    handleServerMessage = async function handleServerMessageWithRunLog(message) {
      await rememberAutomationDraft(message);
      const requestId = message?.request_id || null;
      let startedRun = null;
      if (requestTypes.has(message?.type)) startedRun = await startRequest(message);
      else if (requestId) await appendForRequest(requestId, "background", "server-message", { type: message?.type || "", request_id: requestId });

      if (startedRun) {
        await appendToRun(startedRun, "background", "server-message", {
          type: message?.type || "",
          request_id: requestId,
          model: message?.options?.model || message?.model || null,
          attachment_count: Array.isArray(message?.attachments) ? message.attachments.length : 0,
        }, "info", requestId);
      }

      try {
        const result = await baseHandleServerMessage(message);
        if (requestId) await appendForRequest(requestId, "background", "server-message-dispatched", { type: message?.type || "", request_id: requestId });
        return result;
      } catch (error) {
        if (requestId) {
          await appendForRequest(requestId, "background", "server-message-error", { type: message?.type || "", request_id: requestId, error: String(error?.message || error) }, "error");
          if (requestTypes.has(message?.type)) await finishRequest(requestId, "error", { synthetic: true, reason: "dispatch-threw" });
        }
        throw error;
      }
    };
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type === "chat2api.log.append") {
      const requestId = message.entry?.request_id || message.entry?.data?.request_id || null;
      if (!requestId) {
        sendResponse({ ok: true, ignored: true, reason: "no-active-request" });
        return false;
      }
      appendForRequest(
        requestId,
        message.entry?.component || "content",
        message.entry?.action || "event",
        {
          ...(message.entry?.data || {}),
          request_id: requestId,
          sender_tab_id: sender?.tab?.id ?? null,
          sender_url: sender?.tab?.url || "",
        },
        message.entry?.level || "info",
      ).then(entry => sendResponse({ ok: true, ignored: !entry })).catch(() => sendResponse({ ok: false }));
      return true;
    }

    if (message?.type === "chat2api.event") {
      const event = message.event || {};
      const requestId = event.request_id || null;
      if (requestId) {
        state.queue = state.queue.then(async () => {
          await appendForRequest(requestId, "content-event", event.type || "chat2api.event", {
            request_id: requestId,
            kind: event.kind || null,
            stage: event.stage || event.diagnostics?.submit_stage || event.diagnostics?.voice_stage || event.diagnostics?.image_stage || null,
            diagnostics: event.diagnostics || null,
            error: event.error || null,
            sender_tab_id: sender?.tab?.id ?? null,
            sender_url: sender?.tab?.url || "",
          }, event.error ? "error" : "info");
          if (terminalTypes.has(event.type)) {
            const status = /error$/.test(event.type) ? "error" : (/cancelled$/.test(event.type) ? "cancelled" : "completed");
            await finishRequest(requestId, status, { terminal_event: event.type, kind: event.kind || null });
          }
        }).catch(() => {});
      }
      return false;
    }

    if (message?.type === "popup.logs.export") {
      state.queue.then(async () => {
        const exported = await exportStoredRuns();
        const settings = await chrome.storage.local.get(["clientId", "extensionName", "boundTabId", "socketState", "currentModel", "lastRequestedModel"]);
        sendResponse({
          ok: true,
          data: {
            report_type: "chat2api-extension-runtime-log",
            report_version: 4,
            generated_at: nowIso(),
            extension_version: chrome.runtime.getManifest().version,
            settings: clean(settings),
            auto_local_log: {
              enabled: true,
              backend: "chrome.storage.local",
              silent_persistence: true,
              automatic_downloads: false,
              sessionized_by_api_key: true,
              run_idle_finalize_ms: RUN_IDLE_MS,
              target_bytes: TARGET_BYTES,
              max_finalized_runs: MAX_FINALIZED_RUNS,
              active_runs: state.activeRuns.size,
              finalized_runs: state.runIndex.length,
              last_error: state.storageError || null,
              format: "JSONL; one complete JSON event per line; each API-key run is independent and finalizes after 120 seconds idle",
            },
            runs: exported.runs,
            chunks: exported.chunks,
            legacy_chunks: exported.legacy_chunks,
            entries: state.entries.slice(),
          },
        });
      }).catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }

    if (message?.type === "popup.logs.clear") {
      state.queue.then(async () => {
        await ensureLoaded();
        const keys = [];
        for (const meta of state.runIndex) for (let part = 1; part <= meta.part_count; part += 1) keys.push(runPartKey(meta.run_id, part));
        for (const run of state.activeRuns.values()) {
          for (let part = 1; part <= run.archived_parts; part += 1) keys.push(runPartKey(run.run_id, part));
          try { await chrome.alarms.clear(alarmName(run.run_id)); } catch (_) {}
        }
        const legacy = await chrome.storage.local.get(LEGACY_CHUNK_INDEX_KEY);
        for (const item of Array.isArray(legacy?.[LEGACY_CHUNK_INDEX_KEY]) ? legacy[LEGACY_CHUNK_INDEX_KEY] : []) {
          keys.push(`${LEGACY_CHUNK_PREFIX}${item.day}:${String(item.part || 1).padStart(6, "0")}`);
        }
        if (keys.length) await chrome.storage.local.remove([...new Set(keys)]);
        state.entries = [];
        state.activeRuns.clear();
        state.requestToRun.clear();
        state.runIndex = [];
        state.storageError = "";
        await chrome.storage.local.set({ [HISTORY_KEY]: [], [ACTIVE_RUNS_KEY]: [], [RUN_INDEX_KEY]: [] });
        await chrome.storage.local.remove([LEGACY_CHUNK_KEY, LEGACY_CURRENT_CHUNK_KEY, LEGACY_CHUNK_INDEX_KEY]);
        sendResponse({ ok: true, note: "Sessionized runtime logs stored by the extension were cleared. Manually exported files are not deleted." });
      }).catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }
    return false;
  });

  chrome.alarms.onAlarm.addListener(alarm => {
    if (!alarm?.name?.startsWith(ALARM_PREFIX)) return;
    const runId = alarm.name.slice(ALARM_PREFIX.length);
    state.queue = state.queue.then(() => finalizeRun(runId, "idle-120s")).catch(() => {});
  });

  ensureLoaded().then(() => persistNow()).catch(() => {});
})();
