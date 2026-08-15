(() => {
  const KEY = "__CHAT2API_MODEL_AFFINITY_V23__";
  if (globalThis[KEY]) return;

  const STORAGE_KEY = "chat2apiModelAffinityV23";
  const ALARM_NAME = "chat2api-model-affinity-v23";
  const REFRESH_MS = 10 * 60 * 1000;
  const state = { presets: [], refreshedAt: 0, refreshInFlight: null, loaded: false };
  globalThis[KEY] = state;

  function normalizeReasoning(value) {
    const raw = String(value || "").trim().toLowerCase();
    if (["low", "minimal", "none", "fast", "instant", "极速"].includes(raw)) return "instant";
    if (["medium", "中", "中等"].includes(raw)) return "medium";
    if (["high", "xhigh", "高"].includes(raw)) return "high";
    return null;
  }

  function normalizePreset(raw, rank = 1) {
    const model = String(raw?.model || "").trim().toLowerCase();
    if (!["gpt-5.6-sol", "gpt-5.5", "gpt-5.5-mini"].includes(model)) return null;
    const reasoning = model === "gpt-5.5-mini" ? null : normalizeReasoning(raw?.reasoning);
    return {
      rank: Number(raw?.rank || rank),
      model,
      reasoning,
      count: Math.max(0, Number(raw?.count || 0)),
      key: `${model}:${reasoning || "auto"}`,
    };
  }

  function samePresets(left, right) {
    return JSON.stringify((left || []).map(item => [item.key, item.count])) ===
      JSON.stringify((right || []).map(item => [item.key, item.count]));
  }

  async function loadStored() {
    if (state.loaded) return state.presets;
    state.loaded = true;
    try {
      const stored = await chrome.storage.local.get({ [STORAGE_KEY]: null });
      const value = stored[STORAGE_KEY];
      if (value?.presets && Array.isArray(value.presets)) {
        state.presets = value.presets.map(normalizePreset).filter(Boolean).slice(0, 2);
        state.refreshedAt = Number(value.refreshed_at_ms || 0);
      }
    } catch (_) {}
    return state.presets;
  }

  async function notifyWarmPool(previous, current) {
    if (samePresets(previous, current)) return;
    try {
      const warmPool = globalThis.__CHAT2API_CONVERSATION_WARM_POOL_V2__;
      if (typeof warmPool?.onAffinityChanged === "function") {
        await warmPool.onAffinityChanged(current);
      }
    } catch (_) {}
  }

  async function refresh(force = false) {
    await loadStored();
    if (!force && state.refreshedAt && Date.now() - state.refreshedAt < REFRESH_MS - 5000) return state.presets;
    if (state.refreshInFlight) return state.refreshInFlight;

    state.refreshInFlight = (async () => {
      const settings = await config().catch(() => ({}));
      if (!settings.clientId || !settings.clientToken || !settings.serverUrl) return state.presets;
      const url = `${String(settings.serverUrl).replace(/\/$/, "")}/api/extensions/model-affinity`;
      const response = await fetch(url, {
        method: "GET",
        cache: "no-store",
        headers: {
          "X-Extension-Client-ID": settings.clientId,
          "X-Extension-Token": settings.clientToken,
        },
      });
      if (!response.ok) throw new Error(`Model affinity refresh failed: HTTP ${response.status}`);
      const payload = await response.json();
      const next = Array.isArray(payload?.presets)
        ? payload.presets.map(normalizePreset).filter(Boolean).slice(0, 2)
        : [];
      const previous = [...state.presets];
      state.presets = next;
      state.refreshedAt = Date.now();
      await chrome.storage.local.set({
        [STORAGE_KEY]: {
          presets: next,
          refreshed_at_ms: state.refreshedAt,
          interval_seconds: Number(payload?.interval_seconds || 600),
          history_limit: Number(payload?.history_limit || 200),
          sample_size: Number(payload?.sample_size || 0),
        },
      }).catch(() => {});
      await notifyWarmPool(previous, next);
      return next;
    })().catch(async error => {
      await chrome.storage.local.set({
        chat2apiModelAffinityError: String(error?.message || error),
        chat2apiModelAffinityErrorAt: Date.now(),
      }).catch(() => {});
      return state.presets;
    }).finally(() => { state.refreshInFlight = null; });
    return state.refreshInFlight;
  }

  function presetsForAccount(presets, accountType) {
    const type = String(accountType || "unknown").toLowerCase();
    let rows = (presets || []).map(normalizePreset).filter(Boolean);
    if (type === "free") {
      rows = rows.filter(item => item.model === "gpt-5.5-mini");
      if (!rows.length) rows = [normalizePreset({ model: "gpt-5.5-mini", reasoning: null, count: 0 }, 1)];
    }
    return rows.slice(0, 2);
  }

  function requestedCombo(message, accountType = "unknown") {
    const model = String(message?.options?.model || message?.model || "").trim().toLowerCase();
    if (!["gpt-5.6-sol", "gpt-5.5", "gpt-5.5-mini"].includes(model)) return null;
    if (model === "gpt-5.5-mini") {
      return {
        key: "gpt-5.5-mini:auto",
        model,
        reasoning: null,
        effective_model: String(accountType).toLowerCase() === "free" ? model : "gpt-5.5",
        effective_reasoning: String(accountType).toLowerCase() === "free" ? null : "instant",
      };
    }
    const direct = normalizeReasoning(message?.options?.reasoning_level);
    const effort = normalizeReasoning(message?.options?.reasoning_effort);
    const reasoning = direct || effort || null;
    return {
      key: `${model}:${reasoning || "auto"}`,
      model,
      reasoning,
      effective_model: model,
      effective_reasoning: reasoning,
    };
  }

  async function sendController(tabId, message, files = []) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, message);
      if (response) return response;
    } catch (_) {}
    if (files.length) {
      await chrome.scripting.executeScript({ target: { tabId }, files }).catch(() => {});
      await sleep(100);
    }
    return chrome.tabs.sendMessage(tabId, message);
  }

  async function prepareTab(tabId, preset, accountType = "unknown") {
    const normalized = normalizePreset(preset);
    if (!normalized) return { ok: true, generic: true, preset: null };
    const type = String(accountType || "unknown").toLowerCase();
    if (type === "free") {
      if (normalized.model !== "gpt-5.5-mini") return { ok: false, incompatible: true, preset: normalized };
      return {
        ok: true,
        preset: normalized,
        requested_model: normalized.model,
        effective_model: normalized.model,
        effective_reasoning: null,
        verified: true,
        strategy: "free-default-mini-no-ui-selection",
      };
    }

    const effectiveModel = normalized.model === "gpt-5.5-mini" ? "gpt-5.5" : normalized.model;
    const effectiveReasoning = normalized.model === "gpt-5.5-mini" ? "instant" : normalized.reasoning;
    await ensureContent(tabId);

    let probe = await sendController(
      tabId,
      { type: "chat2api.model.probe.v7", model: effectiveModel, reasoning_level: effectiveReasoning || "" },
      ["content_model_v7.js"],
    ).catch(() => null);
    let data = probe?.data || {};

    if (!(probe?.ok && data.family_match && data.family_trusted)) {
      const family = await sendController(
        tabId,
        { type: "chat2api.model.prepare.v5", model: effectiveModel },
        ["content_model_v5.js"],
      ).catch(error => ({ ok: false, error: String(error?.message || error) }));
      if (!family?.ok) return { ok: false, preset: normalized, error: family?.error || "model preset failed" };
    }

    probe = await sendController(
      tabId,
      { type: "chat2api.model.probe.v7", model: effectiveModel, reasoning_level: effectiveReasoning || "" },
      ["content_model_v7.js"],
    ).catch(() => null);
    data = probe?.data || {};

    if (effectiveReasoning && !(probe?.ok && data.reasoning_match && data.reasoning_trusted)) {
      const reasoning = await sendController(
        tabId,
        { type: "chat2api.reasoning.prepare.v7", reasoning_level: effectiveReasoning },
        ["content_reasoning_v7.js"],
      ).catch(error => ({ ok: false, error: String(error?.message || error) }));
      if (!reasoning?.ok) return { ok: false, preset: normalized, error: reasoning?.error || "reasoning preset failed" };
    }

    const finalProbe = await sendController(
      tabId,
      { type: "chat2api.model.probe.v7", model: effectiveModel, reasoning_level: effectiveReasoning || "" },
      ["content_model_v7.js"],
    ).catch(() => null);
    const finalData = finalProbe?.data || {};
    const verified = Boolean(finalProbe?.ok && finalData.family_match && (!effectiveReasoning || finalData.reasoning_match));
    return {
      ok: verified,
      preset: normalized,
      requested_model: normalized.model,
      requested_reasoning: normalized.reasoning,
      effective_model: effectiveModel,
      effective_reasoning: effectiveReasoning,
      verified,
      strategy: verified ? "history-affinity-passive-verified" : "history-affinity-verification-failed",
      diagnostics: finalData,
    };
  }

  globalThis.chat2apiModelAffinityV23 = {
    refresh,
    async getPresets(force = false) {
      await loadStored();
      if (force || !state.refreshedAt || Date.now() - state.refreshedAt >= REFRESH_MS) await refresh(force);
      return [...state.presets];
    },
    presetsForAccount,
    requestedCombo,
    prepareTab,
  };

  chrome.alarms.create(ALARM_NAME, { periodInMinutes: 10 });
  chrome.alarms.onAlarm.addListener(alarm => {
    if (alarm.name === ALARM_NAME) refresh(true).catch(() => {});
  });
  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName === "local" && changes.socketState?.newValue === "connected") {
      setTimeout(() => refresh(true).catch(() => {}), 800);
    }
  });
  setTimeout(() => refresh(false).catch(() => {}), 1200);
})();
