(() => {
  const KEY = "__CHAT2API_CONVERSATION_WARM_POOL_V2__";
  if (globalThis[KEY]) return;

  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const NETWORK_GATE_KEY = "__CHAT2API_NETWORK_GATE_V26__";
  const STORAGE_KEY = "chat2apiConversationWarmPoolV2";
  const WARM_URL = "https://chatgpt.com/";
  const READY_TIMEOUT_MS = 45000;
  const CLAIM_WAIT_MS = 1800;
  const REQUEST_READY_WAIT_MS = 1400;
  const MAX_WARM_SLOTS = 2;
  // A visible composer only proves that the cached DOM is present. Long-lived
  // ChatGPT SPA pages can retain that DOM while their generation transport is no
  // longer healthy. Never give an API request a spare page that has sat idle for
  // hours; rotate it before routing so playground and external requests share the
  // same freshness guarantee.
  const MAX_WARM_READY_AGE_MS = 30 * 60 * 1000;
  const state = {
    warm: null,
    opening: null,
    replenishTimer: null,
    warmSlots: new Map(),
    openingSlots: new Map(),
    replenishTimers: new Map(),
    claimedRequests: new Set(),
    bypassReasons: new Map(),
    storedLoaded: false,
    onAffinityChanged: null,
  };
  globalThis[KEY] = state;

  const sleepWarm = ms => new Promise(resolve => setTimeout(resolve, ms));
  const createManagedWindow = (options, reason) => typeof globalThis.chat2apiCreateWindowStaggered === "function"
    ? globalThis.chat2apiCreateWindowStaggered(options, { reason })
    : chrome.windows.create(options);
  const baseResolver = globalThis.resolveTargetTabForRequest;
  if (typeof baseResolver !== "function") return;

  function routeKey(message) {
    const value = message?.routing?.api_key_id;
    return typeof value === "string" && value.trim() ? value.trim() : null;
  }

  function requestedModel(message) {
    return String(message?.options?.model || message?.model || "").trim().toLowerCase();
  }

  async function cachedAccountType() {
    try {
      const stored = await chrome.storage.local.get({ accountType: "unknown" });
      const value = String(stored.accountType || "unknown").toLowerCase();
      return ["free", "paid"].includes(value) ? value : "unknown";
    } catch (_) {
      return "unknown";
    }
  }

  async function proactivePrewarmAllowed() {
    const gate = globalThis[NETWORK_GATE_KEY];
    if (typeof gate?.allowPrewarm === "function") {
      try { return await gate.allowPrewarm(); } catch (_) { return false; }
    }
    try {
      const stored = await chrome.storage.local.get({ networkExternalReady: false });
      return stored.networkExternalReady === true;
    } catch (_) {
      return false;
    }
  }

  async function routerState(timeoutMs = 4000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const router = globalThis[ROUTER_KEY];
      if (router?.loaded && router.routes && typeof router.routes === "object") return router;
      await sleepWarm(80);
    }
    return globalThis[ROUTER_KEY] || null;
  }

  function freshRoute(key) {
    return {
      api_key_id: key,
      conversation_id: null,
      conversation_url: null,
      generation: 1,
      turn_count: 0,
      text_chars: 0,
      attachment_count: 0,
      slow_load_strikes: 0,
      last_open_ms: null,
      last_rotation_reason: null,
      tab_id: null,
      window_id: null,
      window_owned: true,
      inflight_request_id: null,
      last_active_at: 0,
      close_after: null,
      prewarm_claimed_at: null,
      prewarm_load_ms: null,
    };
  }

  function resetForWarmClaim(route) {
    if (!route) return false;
    const hadClosedSession = Boolean(
      route.conversation_id || route.conversation_url || Number(route.turn_count || 0) ||
      Number(route.text_chars || 0) || Number(route.attachment_count || 0) ||
      Number.isInteger(route.tab_id) || Number.isInteger(route.window_id)
    );
    route.conversation_id = null;
    route.conversation_url = null;
    route.turn_count = 0;
    route.text_chars = 0;
    route.attachment_count = 0;
    route.slow_load_strikes = 0;
    route.last_open_ms = null;
    route.inflight_request_id = null;
    route.close_after = null;
    if (hadClosedSession) route.generation = Number(route.generation || 1) + 1;
    return hadClosedSession;
  }

  async function tabExists(tabId) {
    if (!Number.isInteger(tabId)) return null;
    try {
      const tab = await chrome.tabs.get(tabId);
      if (!isChatGptUrl(tab.url || tab.pendingUrl || "")) return null;
      return tab;
    } catch (_) {
      return null;
    }
  }

  async function pageReadiness(tabId) {
    try {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: () => {
          const visible = element => {
            if (!element) return false;
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) > 0;
          };
          const composerSelectors = [
            "#prompt-textarea", "textarea[placeholder]",
            "div[contenteditable='true'][data-lexical-editor='true']",
            "div[contenteditable='true'].ProseMirror",
          ];
          const composer = composerSelectors.some(selector => [...document.querySelectorAll(selector)].some(visible));
          const root = [...document.querySelectorAll("form[data-type='unified-composer'], form")]
            .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || document;
          const rejected = element => /send|submit|voice|microphone|mic|audio|attach|upload|file|tool|添加|附件|上传|语音|麦克风|发送/i.test(
            `${element?.getAttribute?.("aria-label") || ""} ${element?.getAttribute?.("data-testid") || ""} ${element?.innerText || element?.textContent || ""}`,
          );
          const pickerSelectors = [
            "button[class*='composer-pill'][aria-haspopup='menu']",
            "button[class*='composer-pill'][aria-haspopup='listbox']",
            "button[data-testid*='model' i]", "button[aria-label*='model' i]",
            "button[aria-label*='模型']", "button[aria-haspopup='menu']", "button[aria-haspopup='listbox']",
          ];
          let modelPicker = false;
          for (const selector of pickerSelectors) {
            const found = [...root.querySelectorAll(selector)].find(element => visible(element) && !element.disabled && !rejected(element));
            if (found) { modelPicker = true; break; }
          }
          return { composer, model_picker: modelPicker, document_ready: document.readyState !== "loading" };
        },
      });
      return results?.[0]?.result || { composer: false, model_picker: false, document_ready: false };
    } catch (_) {
      return { composer: false, model_picker: false, document_ready: false };
    }
  }

  function requestRequiresModelPicker(message, accountType) {
    if (message?.type !== "chat.request") return false;
    const model = requestedModel(message);
    if (model === "gpt-5.5-mini" && accountType === "free") return false;
    return true;
  }

  async function warmReadyForRequest(tabId, message) {
    const accountType = await cachedAccountType();
    const readiness = await pageReadiness(tabId);
    const requirePicker = requestRequiresModelPicker(message, accountType);
    return {
      ok: Boolean(readiness.composer && (!requirePicker || readiness.model_picker)),
      account_type: accountType,
      require_model_picker: requirePicker,
      ...readiness,
    };
  }

  async function waitWarmReady(tabId, timeoutMs = READY_TIMEOUT_MS, requireModelPicker = false) {
    const started = Date.now();
    const deadline = started + timeoutMs;
    let lastError = null;
    let lastReadiness = null;
    while (Date.now() < deadline) {
      const tab = await tabExists(tabId);
      if (!tab) { await sleepWarm(180); continue; }
      try {
        lastReadiness = await pageReadiness(tabId);
        if (lastReadiness.composer && (!requireModelPicker || lastReadiness.model_picker)) {
          await ensureContent(tabId);
          return { tab: await chrome.tabs.get(tabId), load_ms: Date.now() - started, readiness: lastReadiness };
        }
      } catch (error) {
        lastError = error;
      }
      await sleepWarm(220);
    }
    const suffix = lastReadiness
      ? ` (composer=${Boolean(lastReadiness.composer)}, model_picker=${Boolean(lastReadiness.model_picker)}, require_model_picker=${Boolean(requireModelPicker)})`
      : "";
    throw lastError || new Error(`Timed out prewarming the ChatGPT composer${suffix}`);
  }

  async function warmUsedByRoute(tabId) {
    const router = await routerState();
    if (!router?.routes) return false;
    return Object.values(router.routes).some(route => route?.tab_id === tabId);
  }

  function syncLegacyAliases() {
    state.warm = [...state.warmSlots.values()][0] || null;
    state.opening = [...state.openingSlots.values()][0] || null;
    state.replenishTimer = [...state.replenishTimers.values()][0] || null;
  }

  async function persistWarmSlots() {
    syncLegacyAliases();
    await chrome.storage.local.set({
      [STORAGE_KEY]: {
        version: 23,
        slots: [...state.warmSlots.values()].map(item => ({ ...item })),
      },
    }).catch(() => {});
  }

  async function closeWarm(warm) {
    if (!warm) return;
    try { if (Number.isInteger(warm.window_id)) await chrome.windows.remove(warm.window_id); } catch (_) {}
  }

  function warmReadyAge(warm, now = Date.now()) {
    const readyAt = Number(warm?.ready_at_ms || warm?.created_at_ms || 0);
    if (!Number.isFinite(readyAt) || readyAt <= 0) return null;
    return Math.max(0, Number(now || Date.now()) - readyAt);
  }

  function warmSlotFresh(warm, now = Date.now()) {
    const age = warmReadyAge(warm, now);
    return age !== null && age <= MAX_WARM_READY_AGE_MS;
  }

  async function pruneExpiredWarmSlots(now = Date.now(), scheduleReplacement = true) {
    const expired = [];
    let maxAgeMs = 0;
    let closedWindows = 0;
    let detachedRoutedWindows = 0;
    for (const [slotKey, warm] of [...state.warmSlots.entries()]) {
      if (warmSlotFresh(warm, now)) continue;
      const age = warmReadyAge(warm, now);
      if (age !== null) maxAgeMs = Math.max(maxAgeMs, age);
      state.warmSlots.delete(slotKey);
      const routed = await warmUsedByRoute(warm?.tab_id);
      if (routed) {
        // Persisted pool ownership can lag a claim during a service-worker restart.
        // Detach that slot, but never close a page already owned by a live route.
        detachedRoutedWindows += 1;
      } else {
        await closeWarm(warm);
        closedWindows += 1;
      }
      expired.push({ slot_key: slotKey, ready_age_ms: age });
    }
    if (expired.length) {
      await persistWarmSlots();
      if (scheduleReplacement) scheduleWarm(120);
    }
    state.lastFreshnessPrune = {
      version: 39,
      checked_at_ms: Number(now || Date.now()),
      expired_count: expired.length,
      max_ready_age_ms: maxAgeMs,
      closed_windows: closedWindows,
      detached_routed_windows: detachedRoutedWindows,
    };
    return state.lastFreshnessPrune;
  }

  state.maxReadyAgeMs = MAX_WARM_READY_AGE_MS;
  state.readyAge = warmReadyAge;
  state.isFresh = warmSlotFresh;
  state.pruneExpired = pruneExpiredWarmSlots;

  async function loadStoredWarmSlots() {
    if (state.storedLoaded) return;
    state.storedLoaded = true;
    const stored = await chrome.storage.local.get(STORAGE_KEY).catch(() => ({}));
    const value = stored?.[STORAGE_KEY];
    const rows = Array.isArray(value?.slots) ? value.slots : (value?.tab_id ? [{ ...value, slot_key: "generic" }] : []);
    for (const raw of rows.slice(0, MAX_WARM_SLOTS)) {
      if (!Number.isInteger(raw?.tab_id) || !raw?.slot_key) continue;
      const tab = await tabExists(raw.tab_id);
      if (!tab || await warmUsedByRoute(raw.tab_id)) continue;
      if (!warmSlotFresh(raw)) {
        await closeWarm({ ...raw, window_id: tab.windowId });
        continue;
      }
      state.warmSlots.set(String(raw.slot_key), { ...raw, tab_id: tab.id, window_id: tab.windowId, recovered: true });
    }
    await persistWarmSlots();
  }

  async function desiredSlotDefinitions() {
    const accountType = await cachedAccountType();
    const affinity = globalThis.chat2apiModelAffinityV23;
    let presets = [];
    if (affinity?.getPresets) presets = await affinity.getPresets(false).catch(() => []);
    if (affinity?.presetsForAccount) presets = affinity.presetsForAccount(presets, accountType);
    const unique = [];
    const seen = new Set();
    for (const preset of presets || []) {
      if (!preset?.key || seen.has(preset.key)) continue;
      seen.add(preset.key);
      unique.push({ slot_key: `affinity:${preset.key}`, preset, account_type: accountType });
      if (unique.length >= MAX_WARM_SLOTS) break;
    }
    if (!unique.length) unique.push({ slot_key: "generic", preset: null, account_type: accountType });
    return unique;
  }

  async function preparePreset(tabId, definition) {
    const preset = definition?.preset || null;
    const affinity = globalThis.chat2apiModelAffinityV23;
    if (!preset || typeof affinity?.prepareTab !== "function") {
      return { ok: true, generic: true, verified: false, strategy: "generic-ready" };
    }
    return affinity.prepareTab(tabId, preset, definition.account_type);
  }

  async function createWarmWindow(definition) {
    const createdAt = Date.now();
    const accountType = definition.account_type || await cachedAccountType();
    const requireModelPicker = accountType === "paid" || Boolean(definition.preset && definition.preset.model !== "gpt-5.5-mini");
    const created = await createManagedWindow({ url: WARM_URL, focused: false, type: "normal" }, "warm-pool");
    if (!created?.id) throw new Error("Chrome did not create the ChatGPT warm-up window");
    let tab = Array.isArray(created.tabs) ? created.tabs.find(item => Number.isInteger(item.id)) : null;
    if (!tab) {
      const tabs = await chrome.tabs.query({ windowId: created.id });
      tab = tabs.find(item => Number.isInteger(item.id)) || null;
    }
    if (!tab?.id) throw new Error("The ChatGPT warm-up window contains no usable tab");

    try {
      const ready = await waitWarmReady(tab.id, READY_TIMEOUT_MS, requireModelPicker);
      const presetStarted = Date.now();
      const prepared = await preparePreset(tab.id, definition);
      if (!prepared?.ok) throw new Error(prepared?.error || "Unable to preselect the warm-window model preset");
      const warm = {
        slot_key: definition.slot_key,
        tab_id: ready.tab.id,
        window_id: ready.tab.windowId,
        created_at_ms: createdAt,
        ready_at_ms: Date.now(),
        load_ms: ready.load_ms,
        preset_prepare_ms: Date.now() - presetStarted,
        strategy: definition.preset ? "history-model-affinity-preselected" : (requireModelPicker ? "composer+model-controller-ready" : "composer-controller-ready"),
        account_type: accountType,
        model_picker_ready: Boolean(ready.readiness?.model_picker),
        preset_key: definition.preset?.key || null,
        preset_model: definition.preset?.model || null,
        preset_reasoning: definition.preset?.reasoning || null,
        preset_count: Number(definition.preset?.count || 0),
        preset_verified: Boolean(prepared?.verified),
        effective_model: prepared?.effective_model || definition.preset?.model || null,
        effective_reasoning: prepared?.effective_reasoning || definition.preset?.reasoning || null,
      };
      state.warmSlots.set(definition.slot_key, warm);
      await persistWarmSlots();
      return warm;
    } catch (error) {
      try { await chrome.windows.remove(created.id); } catch (_) {}
      throw error;
    }
  }

  async function ensureWarmSlot(definition) {
    await loadStoredWarmSlots();
    const existing = state.warmSlots.get(definition.slot_key);
    if (existing) {
      const tab = await tabExists(existing.tab_id);
      const samePreset = String(existing.preset_key || "") === String(definition.preset?.key || "");
      const routed = tab ? await warmUsedByRoute(tab.id) : false;
      if (tab && samePreset && !routed && warmSlotFresh(existing)) return existing;
      state.warmSlots.delete(definition.slot_key);
      if (!routed) await closeWarm(existing);
      await persistWarmSlots();
    }
    if (state.openingSlots.has(definition.slot_key)) return state.openingSlots.get(definition.slot_key);
    const opening = createWarmWindow(definition).finally(() => {
      state.openingSlots.delete(definition.slot_key);
      syncLegacyAliases();
    });
    state.openingSlots.set(definition.slot_key, opening);
    syncLegacyAliases();
    return opening;
  }

  async function reconcileWarmSlots() {
    await loadStoredWarmSlots();
    await pruneExpiredWarmSlots(Date.now(), false);
    const definitions = await desiredSlotDefinitions();
    const desiredKeys = new Set(definitions.map(item => item.slot_key));
    for (const [slotKey, warm] of [...state.warmSlots.entries()]) {
      if (desiredKeys.has(slotKey)) continue;
      state.warmSlots.delete(slotKey);
      await closeWarm(warm);
    }
    await persistWarmSlots();
    await Promise.all(definitions.map(definition => ensureWarmSlot(definition).catch(async error => {
      await chrome.storage.local.set({ chat2apiWarmupError: String(error?.message || error), chat2apiWarmupErrorAt: Date.now() }).catch(() => {});
      return null;
    })));
    syncLegacyAliases();
    return definitions;
  }

  function scheduleWarm(delayMs = 1200, preferredSlotKey = null) {
    const timerKey = preferredSlotKey || "__all__";
    clearTimeout(state.replenishTimers.get(timerKey));
    const timer = setTimeout(async () => {
      state.replenishTimers.delete(timerKey);
      syncLegacyAliases();
      const settings = await config().catch(() => ({}));
      if (!settings.clientId || !settings.clientToken || settings.socketState !== "connected") return;
      if (!await proactivePrewarmAllowed()) return;
      const definitions = await desiredSlotDefinitions();
      const selected = preferredSlotKey ? definitions.find(item => item.slot_key === preferredSlotKey) : null;
      if (selected) await ensureWarmSlot(selected).catch(() => {});
      else await reconcileWarmSlots().catch(() => {});
    }, delayMs);
    state.replenishTimers.set(timerKey, timer);
    syncLegacyAliases();
  }

  async function liveRouteTab(route) {
    if (!Number.isInteger(route?.tab_id)) return null;
    return tabExists(route.tab_id);
  }

  async function candidateOrder(message) {
    await loadStoredWarmSlots();
    await pruneExpiredWarmSlots();
    const accountType = await cachedAccountType();
    const affinity = globalThis.chat2apiModelAffinityV23;
    const combo = affinity?.requestedCombo ? affinity.requestedCombo(message, accountType) : null;
    const warms = [...state.warmSlots.values()];
    warms.sort((left, right) => {
      const leftExact = combo && left.preset_key === combo.key ? 1 : 0;
      const rightExact = combo && right.preset_key === combo.key ? 1 : 0;
      if (leftExact !== rightExact) return rightExact - leftExact;
      const leftGeneric = left.preset_key ? 0 : 1;
      const rightGeneric = right.preset_key ? 0 : 1;
      if (leftGeneric !== rightGeneric) return rightGeneric - leftGeneric;
      return Number(right.preset_count || 0) - Number(left.preset_count || 0);
    });
    return { warms, combo, accountType };
  }

  async function boundedWarmCandidate(message) {
    const started = Date.now();
    await loadStoredWarmSlots();
    let ordered = await candidateOrder(message);
    if (!ordered.warms.length) {
      const pending = reconcileWarmSlots().catch(() => null);
      await Promise.race([pending, sleepWarm(CLAIM_WAIT_MS)]);
      ordered = await candidateOrder(message);
    }
    if (!ordered.warms.length) {
      return { warm: null, reason: "warm-opening-exceeded-claim-budget", wait_ms: Date.now() - started, combo: ordered.combo };
    }

    for (const warm of ordered.warms) {
      const deadline = Date.now() + REQUEST_READY_WAIT_MS;
      let readiness = null;
      while (Date.now() <= deadline) {
        const tab = await tabExists(warm.tab_id);
        if (!tab) break;
        readiness = await warmReadyForRequest(warm.tab_id, message);
        if (readiness.ok) {
          return {
            warm, readiness, reason: null, wait_ms: Date.now() - started, combo: ordered.combo,
            preset_match: Boolean(ordered.combo && warm.preset_key === ordered.combo.key),
          };
        }
        await sleepWarm(140);
      }
    }
    return { warm: null, reason: "warm-model-controller-not-ready", wait_ms: Date.now() - started, combo: ordered.combo };
  }

  async function claimWarmWindow(key, message) {
    const router = await routerState();
    if (!router?.routes) return null;
    let route = router.routes[key];
    if (route && await liveRouteTab(route)) return null;

    route = route || freshRoute(key);
    const freshAfterClosedWindow = resetForWarmClaim(route);
    router.routes[key] = route;

    const candidate = await boundedWarmCandidate(message);
    const warm = candidate.warm;
    if (!warm) {
      if (message?.request_id) {
        state.bypassReasons.set(message.request_id, {
          reason: candidate.reason || "warm-not-claimable",
          wait_ms: Number(candidate.wait_ms || 0),
          requested_preset_key: candidate.combo?.key || null,
        });
      }
      return null;
    }

    const tab = await tabExists(warm.tab_id);
    if (!tab) return null;
    route.tab_id = tab.id;
    route.window_id = tab.windowId;
    route.window_owned = true;
    route.last_active_at = Date.now();
    route.close_after = null;
    route.last_open_ms = 0;
    route.prewarm_claimed_at = Date.now();
    route.prewarm_load_ms = Number(warm.load_ms || 0);
    route.last_rotation_reason = freshAfterClosedWindow ? "prewarmed-after-closed-window" : "prewarmed-first-request";

    state.warmSlots.delete(warm.slot_key);
    await persistWarmSlots();
    if (message?.request_id) state.claimedRequests.add(message.request_id);

    // Refill the claimed affinity slot immediately for burst traffic. Do not wait for request completion.
    // Legacy invariant equivalent: scheduleWarm(350)
    scheduleWarm(350, warm.slot_key);
    return {
      tab, warm, route, freshAfterClosedWindow, readiness: candidate.readiness,
      claim_wait_ms: candidate.wait_ms, preset_match: candidate.preset_match, requested_combo: candidate.combo,
    };
  }

  globalThis.resolveTargetTabForRequest = async function resolvePrewarmedConversation(message) {
    const key = routeKey(message);
    let claimed = null;
    if (key) claimed = await claimWarmWindow(key, message);
    const tab = await baseResolver(message);
    if (claimed && message?.request_id) {
      const stableAge = Math.max(0, Date.now() - Number(claimed.warm.ready_at_ms || Date.now()));
      const eventType = message.type === "chat.request" ? "chat.diagnostics" : "image.diagnostics";
      await trySendSocket({
        type: eventType,
        kind: message.type === "voice.request" ? "voice" : (message.type === "image.request" ? "image" : undefined),
        request_id: message.request_id,
        diagnostics: {
          conversation_router: "per-key-v1+warm-pool-v23-affinity",
          conversation_strategy: claimed.preset_match ? "claim-model-affinity-window" : "claim-prewarmed-window",
          conversation_prewarm_hit: true,
          conversation_prewarm_load_ms: claimed.warm.load_ms,
          conversation_prewarm_ready_age_ms: stableAge,
          conversation_prewarm_max_ready_age_ms: MAX_WARM_READY_AGE_MS,
          conversation_prewarm_freshness_gate: "spare-max-ready-age-v39",
          conversation_prewarm_claim_wait_ms: claimed.claim_wait_ms,
          conversation_prewarm_account_type: claimed.readiness?.account_type || claimed.warm.account_type || null,
          conversation_prewarm_model_picker_ready: Boolean(claimed.readiness?.model_picker),
          conversation_prewarm_preset_match: Boolean(claimed.preset_match),
          conversation_prewarm_preset_key: claimed.warm.preset_key || null,
          conversation_prewarm_preset_model: claimed.warm.preset_model || null,
          conversation_prewarm_preset_reasoning: claimed.warm.preset_reasoning || null,
          conversation_prewarm_preset_verified: Boolean(claimed.warm.preset_verified),
          conversation_prewarm_preset_prepare_ms: Number(claimed.warm.preset_prepare_ms || 0),
          conversation_requested_preset_key: claimed.requested_combo?.key || null,
          conversation_fresh_after_closed_window: claimed.freshAfterClosedWindow,
          conversation_warm_replenish_on_claim: true,
          conversation_warm_pool_slots: MAX_WARM_SLOTS,
          routed_tab_id: tab?.id ?? null,
          routed_window_id: tab?.windowId ?? null,
        },
      }).catch(() => {});
    } else if (message?.request_id && state.bypassReasons.has(message.request_id)) {
      const bypass = state.bypassReasons.get(message.request_id);
      state.bypassReasons.delete(message.request_id);
      const eventType = message.type === "chat.request" ? "chat.diagnostics" : "image.diagnostics";
      await trySendSocket({
        type: eventType,
        request_id: message.request_id,
        diagnostics: {
          conversation_router: "per-key-v1+warm-pool-v23-affinity",
          conversation_prewarm_hit: false,
          conversation_prewarm_bypassed: true,
          conversation_prewarm_bypass_reason: bypass?.reason || "unknown",
          conversation_prewarm_claim_wait_ms: Number(bypass?.wait_ms || 0),
          conversation_prewarm_max_ready_age_ms: MAX_WARM_READY_AGE_MS,
          conversation_prewarm_freshness_gate: "spare-max-ready-age-v39",
          conversation_requested_preset_key: bypass?.requested_preset_key || null,
          conversation_warm_pool_slots: MAX_WARM_SLOTS,
          routed_tab_id: tab?.id ?? null,
          routed_window_id: tab?.windowId ?? null,
        },
      }).catch(() => {});
    }
    return tab;
  };

  state.onAffinityChanged = async function onAffinityChanged() {
    await reconcileWarmSlots();
  };

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== "chat2api.event") return false;
    const event = message.event || {};
    if (!["chat.completed", "chat.error", "chat.cancelled", "image.completed", "image.error", "image.cancelled"].includes(event.type)) return false;
    if (event.request_id) {
      state.claimedRequests.delete(event.request_id);
      state.bypassReasons.delete(event.request_id);
    }
    scheduleWarm(1400);
    return false;
  });

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (changes.socketState?.newValue === "connected") scheduleWarm(600);
    if (changes.networkExternalReady?.newValue === true) scheduleWarm(120);
  });

  chrome.tabs.onRemoved.addListener(tabId => {
    for (const [slotKey, warm] of [...state.warmSlots.entries()]) {
      if (warm?.tab_id !== tabId) continue;
      state.warmSlots.delete(slotKey);
      persistWarmSlots().catch(() => {});
      scheduleWarm(600, slotKey);
    }
  });

  setTimeout(async () => {
    const settings = await config().catch(() => ({}));
    if (settings.clientId && settings.clientToken && settings.socketState === "connected") scheduleWarm(300);
  }, 300);
})();
