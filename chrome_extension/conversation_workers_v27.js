(() => {
  const KEY = "__CHAT2API_CONVERSATION_WORKERS_V27__";
  if (globalThis[KEY]) return;

  const WORKERS_KEY = "__CHAT2API_CONVERSATION_WORKERS_V25__";
  const ROUTER_KEY = "__CHAT2API_CONVERSATION_ROUTING_V1__";
  const HANDOFF_GRACE_MS = 450;
  const HANDOFF_POLL_MS = 25;
  const AFFINITY_RESTORE_TIMEOUT_MS = 12000;
  const baseResolver = globalThis.resolveTargetTabForRequest;
  const workers = globalThis[WORKERS_KEY];
  const router = globalThis[ROUTER_KEY];
  if (typeof baseResolver !== "function" || !workers || !router) return;

  const state = {
    revision: 27,
    handoffGraceMs: HANDOFF_GRACE_MS,
    affinityRestoreRevision: 117,
    lastWaitMs: 0,
    lastRestore: null,
  };
  globalThis[KEY] = state;
  globalThis.chat2apiConversationWorkersV27 = state;

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  function conversationId(url = "") {
    try {
      const parsed = new URL(url);
      if (!["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(parsed.hostname)) return null;
      const match = parsed.pathname.match(/\/c\/([^/?#]+)/i);
      return match ? decodeURIComponent(match[1]) : null;
    } catch (_) { return null; }
  }

  function logicalKey(message) {
    const routing = message?.routing || {};
    const value = routing.logical_api_key_id || routing.api_key_id;
    return typeof value === "string" && value.trim() ? value.trim() : null;
  }

  function requestId(message) {
    return String(message?.request_id || "");
  }

  function primaryBusy(baseKey, currentRequestId) {
    const routes = router?.routes && typeof router.routes === "object" ? router.routes : {};
    const route = routes[baseKey];
    const reservation = workers.routeReservations instanceof Map ? workers.routeReservations.get(baseKey) : null;
    if (reservation && reservation.requestId !== currentRequestId) {
      const reservationId = String(reservation.requestId || "");
      if (!(workers.terminalRequests instanceof Map && workers.terminalRequests.has(reservationId))) return true;
    }
    const inflight = String(route?.inflight_request_id || "");
    if (!inflight || inflight === currentRequestId) return false;
    if (workers.terminalRequests instanceof Map && workers.terminalRequests.has(inflight)) return false;
    if (router.activeRequests instanceof Map && !router.activeRequests.has(inflight)) return false;
    return true;
  }

  async function waitForPrimaryHandoff(message) {
    const baseKey = logicalKey(message);
    const id = requestId(message);
    if (!baseKey || !id || workers.requestTarget?.(id)) return 0;
    if (!primaryBusy(baseKey, id)) return 0;
    const started = Date.now();
    const deadline = started + HANDOFF_GRACE_MS;
    while (Date.now() < deadline) {
      await sleep(HANDOFF_POLL_MS);
      if (!primaryBusy(baseKey, id)) break;
    }
    return Date.now() - started;
  }

  async function waitForRestoredConversation(tabId, expectedConversation) {
    const deadline = Date.now() + AFFINITY_RESTORE_TIMEOUT_MS;
    let last = null;
    while (Date.now() < deadline) {
      try {
        const tab = await chrome.tabs.get(tabId);
        last = tab;
        const id = conversationId(tab.url || tab.pendingUrl || "");
        if (id === expectedConversation && (!tab.status || tab.status === "complete")) {
          try { if (typeof ensureContent === "function") await ensureContent(tabId); } catch (_) {}
          return tab;
        }
      } catch (_) {}
      await sleep(120);
    }
    return last;
  }

  async function restoreAffinityIfNeeded(message, selected, tab) {
    if (!selected?.routeKey || !tab?.id) return { tab, restored: false, reason: "no-route" };
    const route = router?.routes?.[selected.routeKey];
    const expectedConversation = String(route?.conversation_id || "").trim();
    const expectedUrl = String(route?.conversation_url || "").trim();
    if (!expectedConversation || !expectedUrl) return { tab, restored: false, reason: "no-saved-conversation" };

    const actualConversation = conversationId(tab.url || tab.pendingUrl || "");
    if (actualConversation === expectedConversation) return { tab, restored: false, reason: "already-aligned" };

    try {
      await chrome.tabs.update(tab.id, { url: expectedUrl, active: true });
      const restoredTab = await waitForRestoredConversation(tab.id, expectedConversation);
      const restoredConversation = conversationId(restoredTab?.url || restoredTab?.pendingUrl || "");
      if (restoredConversation === expectedConversation) {
        route.tab_id = restoredTab.id;
        route.window_id = restoredTab.windowId;
        route.conversation_id = expectedConversation;
        route.conversation_url = expectedUrl;
        route.last_active_at = Date.now();
        state.lastRestore = {
          requestId: requestId(message),
          routeKey: selected.routeKey,
          tabId: restoredTab.id,
          windowId: restoredTab.windowId,
          at: Date.now(),
        };
        return { tab: restoredTab, restored: true, reason: "saved-conversation-restored" };
      }
      return { tab: restoredTab || tab, restored: false, reason: "restore-verification-failed" };
    } catch (_) {
      return { tab, restored: false, reason: "restore-navigation-failed" };
    }
  }

  async function emitV27Diagnostics(message, selected, tab, waitMs, affinity) {
    if (!selected) return;
    const eventType = message.type === "chat.request" ? "chat.diagnostics" : "image.diagnostics";
    await trySendSocket({
      type: eventType,
      kind: message.type === "voice.request" || message.type === "voice.live.start" ? "voice" : undefined,
      request_id: requestId(message),
      diagnostics: {
        extension_worker_router: "per-api-key-v27-sequential-handoff",
        extension_worker_router_revision: 27,
        extension_worker_terminal_reuse: true,
        extension_worker_sequential_handoff_grace: true,
        extension_worker_handoff_grace_ms: HANDOFF_GRACE_MS,
        extension_worker_handoff_wait_ms: waitMs,
        extension_worker_index: selected.workerIndex,
        extension_window_number: selected.workerIndex,
        window_number: selected.workerIndex,
        extension_worker_limit: selected.workerLimit,
        extension_worker_route_key: selected.routeKey,
        extension_worker_logical_api_key_id: selected.baseKey,
        conversation_affinity_restore_revision: 117,
        conversation_affinity_restored: Boolean(affinity?.restored),
        conversation_affinity_restore_reason: affinity?.reason || null,
        routed_tab_id: tab?.id ?? selected.tabId ?? null,
        routed_window_id: tab?.windowId ?? selected.windowId ?? null,
      },
    }).catch(() => {});
  }

  globalThis.resolveTargetTabForRequest = async function resolveSequentialWorkerV27(message) {
    const waitMs = await waitForPrimaryHandoff(message);
    state.lastWaitMs = waitMs;
    const tab = await baseResolver(message);
    const selected = workers.requestTarget?.(requestId(message)) || null;
    if (!selected) return tab;

    const affinity = await restoreAffinityIfNeeded(message, selected, tab);
    const finalTab = affinity.tab || tab;
    selected.tabId = finalTab?.id ?? selected.tabId ?? null;
    selected.windowId = finalTab?.windowId ?? selected.windowId ?? null;
    message.routing = {
      ...(message.routing || {}),
      logical_api_key_id: selected.baseKey,
      api_key_id: selected.routeKey,
      worker_index: selected.workerIndex,
      window_number: selected.workerIndex,
      worker_limit: selected.workerLimit,
      routed_tab_id: selected.tabId,
      routed_window_id: selected.windowId,
    };
    await emitV27Diagnostics(message, selected, finalTab, waitMs, affinity);
    return finalTab;
  };
})();
