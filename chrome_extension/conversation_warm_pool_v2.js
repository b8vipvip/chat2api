(() => {
  const KEY = "__CHAT2API_CONVERSATION_WARM_POOL_V2__";
  if (globalThis[KEY]) return;

  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const STORAGE_KEY = "chat2apiConversationWarmPoolV2";
  const WARM_URL = "https://chatgpt.com/";
  const READY_TIMEOUT_MS = 45000;
  const CLAIM_WAIT_MS = 1800;
  const REQUEST_READY_WAIT_MS = 1400;
  const state = {
    warm: null,
    opening: null,
    replenishTimer: null,
    claimedRequests: new Set(),
    bypassReasons: new Map(),
  };
  globalThis[KEY] = state;

  const sleepWarm = ms => new Promise(resolve => setTimeout(resolve, ms));
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
      route.conversation_id ||
      route.conversation_url ||
      Number(route.turn_count || 0) ||
      Number(route.text_chars || 0) ||
      Number(route.attachment_count || 0) ||
      Number.isInteger(route.tab_id) ||
      Number.isInteger(route.window_id)
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
            "#prompt-textarea",
            "textarea[placeholder]",
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
            "button[data-testid*='model' i]",
            "button[aria-label*='model' i]",
            "button[aria-label*='模型']",
            "button[aria-haspopup='menu']",
            "button[aria-haspopup='listbox']",
          ];
          let modelPicker = false;
          for (const selector of pickerSelectors) {
            const found = [...root.querySelectorAll(selector)].find(element => visible(element) && !element.disabled && !rejected(element));
            if (found) {
              modelPicker = true;
              break;
            }
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
      if (!tab) {
        await sleepWarm(180);
        continue;
      }
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

  async function clearStoredWarm() {
    state.warm = null;
    await chrome.storage.local.remove(STORAGE_KEY).catch(() => {});
  }

  async function warmUsedByRoute(tabId) {
    const router = await routerState();
    if (!router?.routes) return false;
    return Object.values(router.routes).some(route => route?.tab_id === tabId);
  }

  async function recoverStoredWarm() {
    const stored = await chrome.storage.local.get(STORAGE_KEY).catch(() => ({}));
    const value = stored?.[STORAGE_KEY];
    if (!value || !Number.isInteger(value.tab_id)) return null;
    const tab = await tabExists(value.tab_id);
    if (!tab || await warmUsedByRoute(value.tab_id)) {
      await clearStoredWarm();
      return null;
    }
    try {
      const accountType = await cachedAccountType();
      const ready = await waitWarmReady(value.tab_id, 8000, accountType === "paid");
      state.warm = {
        ...value,
        tab_id: ready.tab.id,
        window_id: ready.tab.windowId,
        recovered: true,
        account_type: accountType,
        model_picker_ready: Boolean(ready.readiness?.model_picker),
      };
      return state.warm;
    } catch (_) {
      await clearStoredWarm();
      return null;
    }
  }

  async function createWarmWindow() {
    const createdAt = Date.now();
    const accountType = await cachedAccountType();
    const requireModelPicker = accountType === "paid";
    const created = await chrome.windows.create({ url: WARM_URL, focused: false, type: "normal" });
    if (!created?.id) throw new Error("Chrome did not create the ChatGPT warm-up window");
    let tab = Array.isArray(created.tabs) ? created.tabs.find(item => Number.isInteger(item.id)) : null;
    if (!tab) {
      const tabs = await chrome.tabs.query({ windowId: created.id });
      tab = tabs.find(item => Number.isInteger(item.id)) || null;
    }
    if (!tab?.id) throw new Error("The ChatGPT warm-up window contains no usable tab");

    try {
      const ready = await waitWarmReady(tab.id, READY_TIMEOUT_MS, requireModelPicker);
      const warm = {
        tab_id: ready.tab.id,
        window_id: ready.tab.windowId,
        created_at_ms: createdAt,
        ready_at_ms: Date.now(),
        load_ms: ready.load_ms,
        strategy: requireModelPicker ? "composer+model-controller-ready" : "composer-controller-ready",
        account_type: accountType,
        model_picker_ready: Boolean(ready.readiness?.model_picker),
      };
      state.warm = warm;
      await chrome.storage.local.set({ [STORAGE_KEY]: warm });
      return warm;
    } catch (error) {
      try { await chrome.windows.remove(created.id); } catch (_) {}
      throw error;
    }
  }

  async function ensureWarmWindow() {
    if (state.warm) {
      const tab = await tabExists(state.warm.tab_id);
      if (tab && !await warmUsedByRoute(tab.id)) return state.warm;
      await clearStoredWarm();
    }
    if (state.opening) return state.opening;
    state.opening = (async () => {
      const recovered = await recoverStoredWarm();
      if (recovered) return recovered;
      return createWarmWindow();
    })().finally(() => { state.opening = null; });
    return state.opening;
  }

  function scheduleWarm(delayMs = 1200) {
    clearTimeout(state.replenishTimer);
    state.replenishTimer = setTimeout(async () => {
      const settings = await config().catch(() => ({}));
      if (!settings.clientId || !settings.clientToken || settings.socketState !== "connected") return;
      await ensureWarmWindow().catch(async error => {
        await chrome.storage.local.set({ chat2apiWarmupError: String(error?.message || error), chat2apiWarmupErrorAt: Date.now() }).catch(() => {});
      });
    }, delayMs);
  }

  async function liveRouteTab(route) {
    if (!Number.isInteger(route?.tab_id)) return null;
    return tabExists(route.tab_id);
  }

  async function boundedWarmCandidate(message) {
    const started = Date.now();
    let warm = state.warm;
    if (!warm) {
      const pending = ensureWarmWindow().catch(() => null);
      warm = await Promise.race([
        pending,
        sleepWarm(CLAIM_WAIT_MS).then(() => null),
      ]);
      if (!warm) {
        return { warm: null, reason: "warm-opening-exceeded-claim-budget", wait_ms: Date.now() - started };
      }
    }

    const deadline = Date.now() + REQUEST_READY_WAIT_MS;
    let readiness = null;
    while (Date.now() <= deadline) {
      const tab = await tabExists(warm.tab_id);
      if (!tab) return { warm: null, reason: "warm-tab-missing", wait_ms: Date.now() - started };
      readiness = await warmReadyForRequest(warm.tab_id, message);
      if (readiness.ok) return { warm, readiness, reason: null, wait_ms: Date.now() - started };
      await sleepWarm(140);
    }
    return {
      warm: null,
      readiness,
      reason: readiness?.require_model_picker ? "warm-model-controller-not-ready" : "warm-composer-not-ready",
      wait_ms: Date.now() - started,
    };
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
          account_type: candidate.readiness?.account_type || null,
          composer_ready: Boolean(candidate.readiness?.composer),
          model_picker_ready: Boolean(candidate.readiness?.model_picker),
          require_model_picker: Boolean(candidate.readiness?.require_model_picker),
        });
      }
      return null;
    }

    const tab = await tabExists(warm.tab_id);
    if (!tab) {
      await clearStoredWarm();
      return null;
    }

    route.tab_id = tab.id;
    route.window_id = tab.windowId;
    route.window_owned = true;
    route.last_active_at = Date.now();
    route.close_after = null;
    route.last_open_ms = 0;
    route.prewarm_claimed_at = Date.now();
    route.prewarm_load_ms = Number(warm.load_ms || 0);
    route.last_rotation_reason = freshAfterClosedWindow
      ? "prewarmed-after-closed-window"
      : "prewarmed-first-request";

    state.warm = null;
    await chrome.storage.local.remove(STORAGE_KEY).catch(() => {});
    if (message?.request_id) state.claimedRequests.add(message.request_id);

    // The claimed page is now a routed conversation. Refill the one-slot warm pool
    // immediately in the background instead of waiting for this request to finish.
    scheduleWarm(350);
    return { tab, warm, route, freshAfterClosedWindow, readiness: candidate.readiness, claim_wait_ms: candidate.wait_ms };
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
          conversation_router: "per-key-v1+warm-pool-v2",
          conversation_strategy: "claim-prewarmed-window",
          conversation_prewarm_hit: true,
          conversation_prewarm_load_ms: claimed.warm.load_ms,
          conversation_prewarm_ready_age_ms: stableAge,
          conversation_prewarm_claim_wait_ms: claimed.claim_wait_ms,
          conversation_prewarm_account_type: claimed.readiness?.account_type || claimed.warm.account_type || null,
          conversation_prewarm_model_picker_ready: Boolean(claimed.readiness?.model_picker),
          conversation_fresh_after_closed_window: claimed.freshAfterClosedWindow,
          conversation_warm_replenish_on_claim: true,
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
        kind: message.type === "voice.request" ? "voice" : (message.type === "image.request" ? "image" : undefined),
        request_id: message.request_id,
        diagnostics: {
          conversation_router: "per-key-v1+warm-pool-v2",
          conversation_prewarm_hit: false,
          conversation_prewarm_bypassed: true,
          conversation_prewarm_bypass_reason: bypass?.reason || "unknown",
          conversation_prewarm_claim_wait_ms: Number(bypass?.wait_ms || 0),
          conversation_prewarm_account_type: bypass?.account_type || null,
          conversation_prewarm_composer_ready: Boolean(bypass?.composer_ready),
          conversation_prewarm_model_picker_ready: Boolean(bypass?.model_picker_ready),
          conversation_prewarm_require_model_picker: Boolean(bypass?.require_model_picker),
          routed_tab_id: tab?.id ?? null,
          routed_window_id: tab?.windowId ?? null,
        },
      }).catch(() => {});
    }
    return tab;
  };

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== "chat2api.event") return false;
    const event = message.event || {};
    if (!["chat.completed", "chat.error", "chat.cancelled", "image.completed", "image.error", "image.cancelled"].includes(event.type)) return false;
    if (event.request_id) {
      state.claimedRequests.delete(event.request_id);
      state.bypassReasons.delete(event.request_id);
    }
    // Safety refill in case a previous warm-up attempt failed while the request ran.
    scheduleWarm(1400);
    return false;
  });

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (changes.socketState?.newValue === "connected") scheduleWarm(600);
  });

  chrome.tabs.onRemoved.addListener(tabId => {
    if (state.warm?.tab_id === tabId) clearStoredWarm().catch(() => {});
  });

  setTimeout(async () => {
    const settings = await config().catch(() => ({}));
    if (settings.clientId && settings.clientToken && settings.socketState === "connected") scheduleWarm(300);
  }, 300);
})();
