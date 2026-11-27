(() => {
  const KEY = "__CHAT2API_CONVERSATION_DISPATCH_V1__";
  if (globalThis[KEY]) return;

  const baseHandleServerMessage = handleServerMessage;
  const baseResolveTargetTab = resolveTargetTab;
  const state = { currentTab: null, chain: Promise.resolve(), requestTabs: new Map(), nativeRequestTabs: new Map(), nativeRequestTabsHydrated: false };
  globalThis[KEY] = state;
  const monotonicNow = () => globalThis.performance?.now?.() ?? Date.now();
  const elapsedMs = started => Math.max(0, Math.round((monotonicNow() - started) * 10) / 10);
  const timingValue = value => typeof value === "number" && Number.isFinite(value) && value >= 0
    ? Math.round(value * 10) / 10 : null;
  function recordRouteReallocation(message, started) {
    const previous = timingValue(message.__chat2api_worker_route_reallocation_ms) ?? 0;
    message.__chat2api_worker_route_reallocation_ms = Math.round((previous + elapsedMs(started)) * 10) / 10;
  }

  resolveTargetTab = async function resolveCurrentConversationTarget() {
    if (state.currentTab?.id) {
      const routedId = state.currentTab.id;
      try { return await chrome.tabs.get(routedId); }
      catch (_) {
        // Routed requests must never fall back to an unrelated browser tab.
        // The dispatcher will ask the single router to reallocate pre-ACK.
        throw new Error(`No tab with id: ${routedId}`);
      }
    }
    return baseResolveTargetTab();
  };

  async function withCurrentTab(tab, callback) {
    state.currentTab = tab;
    try { return await callback(); }
    finally { state.currentTab = null; }
  }

  async function resolveRoutedTab(message, { recordReallocation = true } = {}) {
    const resolver = globalThis.resolveTargetTabForRequest;
    const requestId = String(message?.request_id || "");
    // The single conversation router remains the sole allocation owner.
    // Repair a tab that disappears BEFORE content dispatch exactly once;
    // never replay a request that could already have reached the composer.
    let reallocationStarted = null;
    try {
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const tab = typeof resolver === "function"
          ? await resolver(message) : await baseResolveTargetTab();
        if (!tab?.id) throw new Error("Per-key conversation router returned no usable ChatGPT tab");
        const live = await chrome.tabs.get(tab.id).catch(() => null);
        if (live?.id && live.windowId === tab.windowId) {
          if (requestId) {
            state.requestTabs.set(requestId, { tabId: live.id, windowId: live.windowId });
            if (message?.type === "chat.request") {
              state.nativeRequestTabs.set(requestId, {
                tabId: live.id, windowId: live.windowId, expiresAt: Date.now() + 1800000,
              });
              while (state.nativeRequestTabs.size > 64) {
                state.nativeRequestTabs.delete(state.nativeRequestTabs.keys().next().value);
              }
            }
          }
          await chrome.storage.local.set({
            boundTabId: live.id, autoBind: false, modelsUpdatedAt: 0,
            nativeRequestTabsV1: [...state.nativeRequestTabs.entries()],
          });
          return live;
        }
        if (attempt === 0 && requestId) {
          reallocationStarted = monotonicNow();
          const router = globalThis.__CHAT2API_CONVERSATION_ROUTING_V1__;
          await router?.failRequest?.(requestId, "tab-closed-before-dispatch-v153");
          continue;
        }
        const error = new Error("Routed ChatGPT tab disappeared before request dispatch");
        error.code = "routed_tab_closed_before_dispatch";
        error.stage = "route-readiness";
        throw error;
      }
      throw new Error("Routed ChatGPT tab was not available");
    } finally {
      if (reallocationStarted !== null && recordReallocation) recordRouteReallocation(message, reallocationStarted);
    }
  }

  function enqueueDispatch(taskFactory) {
    const task = state.chain.then(taskFactory);
    state.chain = task.catch(() => {});
    return task;
  }

  async function hydrateNativeRequestTabs() {
    if (state.nativeRequestTabsHydrated) return;
    state.nativeRequestTabsHydrated = true;
    try {
      const stored = await chrome.storage.local.get("nativeRequestTabsV1");
      const rows = Array.isArray(stored?.nativeRequestTabsV1) ? stored.nativeRequestTabsV1 : [];
      for (const row of rows) {
        if (!Array.isArray(row) || row.length !== 2) continue;
        const [requestId,target] = row;
        if (!/^req_[A-Za-z0-9_-]{8,120}$/.test(String(requestId || ""))
            || !target || !Number.isInteger(target.tabId) || !Number.isInteger(target.windowId)
            || !Number.isFinite(target.expiresAt) || target.expiresAt <= Date.now()) continue;
        state.nativeRequestTabs.set(String(requestId), {
          tabId:target.tabId, windowId:target.windowId, expiresAt:target.expiresAt,
        });
      }
      while (state.nativeRequestTabs.size > 64) {
        state.nativeRequestTabs.delete(state.nativeRequestTabs.keys().next().value);
      }
    } catch (_) {}
  }

  async function persistNativeRequestTabs() {
    try {
      await chrome.storage.local.set({
        nativeRequestTabsV1:[...state.nativeRequestTabs.entries()],
      });
    } catch (_) {}
  }

  function terminalTypeFor(message) {
    if (message?.type === "image.request") return "image.error";
    if (message?.type === "voice.request" || message?.type === "voice.live.start") return "voice.error";
    return "chat.error";
  }

  async function publishRoutedDispatchFailure(message, error) {
    const requestId = String(message?.request_id || "");
    if (!requestId) throw error;
    const text = String(error?.message || error || "Worker route dispatch failed");
    const retryAfterMs = Math.max(0, Number(error?.retry_after_ms || 0));
    const rateLimited = error?.code === "chatgpt_rate_limited" || /temporarily rate limited|too many requests/i.test(text);

    // Dispatch owns transport only. It never clears route fields or closes a
    // window. Any allocation/preflight failure is handed to the single router
    // authority, which performs exactly one lifecycle transition.
    const router = globalThis.__CHAT2API_CONVERSATION_ROUTING_V1__;
    const routeRetired = typeof router?.failRequest === "function"
      ? await router.failRequest(requestId, `dispatch-failure:${String(error?.code || "unknown")}`).catch(() => false)
      : false;

    const event = {
      type: terminalTypeFor(message),
      request_id: requestId,
      error: text,
      diagnostics: {
        ...(error?.routingDiagnostics || {}),
        routed_dispatch_terminal_v58: true,
        route_failure_code: String(error?.code || "route_dispatch_failed"),
        route_failure_stage: String(error?.stage || "dispatch"),
        worker_dispatch_queue_wait_ms: timingValue(message.__chat2api_worker_dispatch_queue_wait_ms),
        worker_route_resolve_ms: timingValue(message.__chat2api_worker_route_resolve_ms),
        worker_route_reallocation_ms: timingValue(message.__chat2api_worker_route_reallocation_ms),
        route_retired_by_authority_v30: routeRetired,
        rate_limit_terminal: rateLimited,
        retry_after_ms: retryAfterMs,
      },
    };
    if (retryAfterMs > 0) event.retry_after_ms = retryAfterMs;
    const sent = await trySendSocket(event);
    state.requestTabs.delete(requestId);
    state.nativeRequestTabs.delete(requestId);
    if (!sent) throw error;
    return null;
  }

  globalThis.chat2apiResolveRoutedWorkerTabV24 = function resolveExternalRoutedWorkerTab(message) {
    return enqueueDispatch(() => resolveRoutedTab(message));
  };

  async function routedDispatch(message) {
    const routeStarted = monotonicNow();
    let tab;
    try {
      tab = await resolveRoutedTab(message);
    } finally {
      message.__chat2api_worker_route_resolve_ms = elapsedMs(routeStarted);
    }
    try {
      return await withCurrentTab(tab, () => baseHandleServerMessage(message));
    } catch (error) {
      if (error?.code !== "routed_tab_disappeared_before_ack") throw error;
      const requestId = String(message?.request_id || "");
      const router = globalThis.__CHAT2API_CONVERSATION_ROUTING_V1__;
      if (!requestId || typeof router?.failRequest !== "function") throw error;
      // An explicit Chrome "No tab" means no content-script ACK and no
      // generation started. Reallocate once through the SAME router only.
      // Include the pre-ACK retry in the route timing instead of reporting
      // only the first allocation attempt. Keep the retry separately visible
      // so a real slow trace can distinguish initial routing from tab recovery.
      const reallocationStarted = monotonicNow();
      let replacement;
      try {
        await router.failRequest(requestId, "lost-tab-before-content-ack-v153");
        // The outer timer covers this entire recovery, including any internal
        // readiness retry. Suppress nested recording to avoid double counting.
        replacement = await resolveRoutedTab(message, { recordReallocation: false });
      } finally {
        recordRouteReallocation(message, reallocationStarted);
        message.__chat2api_worker_route_resolve_ms = elapsedMs(routeStarted);
      }
      await trySendSocket({
        type: "chat.diagnostics",
        request_id: requestId,
        diagnostics: {
          routed_tab_missing_before_ack: true,
          routed_tab_reallocated_once: true,
          routed_tab_id: replacement.id,
          routed_window_id: replacement.windowId,
        },
      }).catch(() => false);
      return withCurrentTab(replacement, () => baseHandleServerMessage(message));
    }
  }

  async function dispatchKnownRequest(message) {
    const requestId = String(message?.request_id || "");
    const target = state.requestTabs.get(requestId);
    const router = globalThis.__CHAT2API_CONVERSATION_ROUTING_V1__;
    let tab = null;
    try {
      if (target?.tabId) tab = await chrome.tabs.get(target.tabId).catch(() => null);
      // Never send an old request's cancel to the latest bound tab. If its
      // original tab is already gone, exact-owner route retirement is enough.
      // This whole operation runs inside the dispatch chain, so a replacement
      // request cannot lease the old route before cancellation settles.
      if (tab?.id) return await withCurrentTab(tab, () => baseHandleServerMessage(message));
      return null;
    } finally {
      let retired = false;
      if (requestId && typeof router?.failRequest === "function") {
        retired = await router.failRequest(requestId, "server-cancel-control-v136").catch(() => false);
      }
      state.requestTabs.delete(requestId);
      state.lastCancelHandoff = {
        request_id: requestId,
        target_tab_id: target?.tabId ?? null,
        target_found: Boolean(tab?.id),
        retired_by_router: Boolean(retired),
        at_ms: Date.now(),
      };
    }
  }

  async function dispatchNativeInteraction(message) {
    await hydrateNativeRequestTabs();
    const requestId = String(message?.request_id || "");
    const actionId = String(message?.action_id || "");
    const revision = Number(message?.revision);
    const interactionId = String(message?.interaction_id || "");
    let result = {ok:false,error:"native-interaction-route-unavailable"};
    const target = state.nativeRequestTabs.get(requestId);
    if (target && target.expiresAt <= Date.now()) state.nativeRequestTabs.delete(requestId);
    if (
      target && target.expiresAt > Date.now()
      && /^[a-f0-9]{32}$/.test(actionId)
      && Number.isInteger(revision) && revision >= 1
      && /^nc_[1-9][0-9]{0,6}_[1-9][0-9]{0,3}$/.test(interactionId)
    ) {
      try {
        const tab = await chrome.tabs.get(target.tabId);
        if (tab?.id === target.tabId && tab.windowId === target.windowId
            && isChatGptUrl(tab.url || tab.pendingUrl || "")) {
          result = await chrome.tabs.sendMessage(tab.id, {
            type:"chat2api.native.interaction",
            request_id:requestId,
            action_id:actionId,
            revision,
            interaction_id:interactionId,
            action:String(message?.action || ""),
            value:message?.value,
          });
        } else {
          state.nativeRequestTabs.delete(requestId);
          await persistNativeRequestTabs();
          result = {ok:false,error:"original-Worker-tab-no-longer-available"};
        }
      } catch (_) {
        state.nativeRequestTabs.delete(requestId);
        await persistNativeRequestTabs();
        result = {ok:false,error:"original-Worker-tab-no-longer-available"};
      }
    }
    await trySendSocket({
      type:"chat.native.interaction.ack",
      request_id:requestId,
      action_id:actionId,
      revision,
      interaction_id:interactionId,
      ok:result?.ok===true,
      error:String(result?.error || "").slice(0,120),
    });
    return result;
  }

  handleServerMessage = async function handleConversationAwareServerMessage(message) {
    if (message?.type === "chat.native.interaction") {
      return enqueueDispatch(() => dispatchNativeInteraction(message));
    }
    const isRoutedRequest = ["chat.request", "image.request", "voice.request", "voice.live.start"].includes(message?.type)
      && Boolean(message?.routing?.api_key_id);
    // Queue every cancellation even when its tab mapping vanished. Falling
    // through to the generic handler may cancel an unrelated/newer window.
    const isTargetedControl = ["chat.cancel", "image.cancel", "voice.cancel"].includes(message?.type);
    if (!isRoutedRequest && !isTargetedControl) return baseHandleServerMessage(message);

    // This chain serializes only brief route allocation/page hand-off operations.
    // Same-logical-API request admission is exclusively server scheduler v58.
    const dispatchEnqueuedAt = isRoutedRequest ? monotonicNow() : null;
    return enqueueDispatch(async () => {
      if (dispatchEnqueuedAt !== null) {
        message.__chat2api_worker_dispatch_queue_wait_ms = elapsedMs(dispatchEnqueuedAt);
      }
      try {
        return isRoutedRequest ? await routedDispatch(message) : await dispatchKnownRequest(message);
      } catch (error) {
        if (isRoutedRequest) return publishRoutedDispatchFailure(message, error);
        throw error;
      }
    });
  };

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== "chat2api.event") return false;
    const event = message.event || {};
    if (!["chat.completed", "chat.error", "chat.cancelled", "image.completed", "image.error", "image.cancelled", "voice.error", "voice.cancelled"].includes(event.type)) return false;
    const requestId = String(event.request_id || "");
    if (requestId) state.requestTabs.delete(requestId);
    if (requestId && event.type !== "chat.completed") {
      state.nativeRequestTabs.delete(requestId);
      persistNativeRequestTabs();
    }
    return false;
  });
  chrome.tabs.onRemoved?.addListener?.(tabId => {
    for (const [requestId, target] of state.nativeRequestTabs) {
      if (target?.tabId === tabId) state.nativeRequestTabs.delete(requestId);
    }
    persistNativeRequestTabs();
  });
})();
