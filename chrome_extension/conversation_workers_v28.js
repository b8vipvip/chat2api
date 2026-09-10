(() => {
  const KEY = "__CHAT2API_CONVERSATION_WORKERS_V28__";
  if (globalThis[KEY]) return;

  const baseResolver = globalThis.resolveTargetTabForRequest;
  if (typeof baseResolver !== "function") return;

  const state = {
    revision: 28,
    strictSerial: true,
    workerLimit: 1,
  };
  globalThis[KEY] = state;
  globalThis.chat2apiConversationWorkersV28 = state;

  function logicalKey(message) {
    const routing = message?.routing || {};
    const raw = String(routing.logical_api_key_id || routing.api_key_id || "").trim();
    if (!raw) return null;
    return raw.replace(/::worker\d+$/i, "");
  }

  globalThis.resolveTargetTabForRequest = async function resolveStrictSingleWorker(message) {
    const key = logicalKey(message);
    if (!key) return baseResolver(message);

    message.routing = {
      ...(message.routing || {}),
      logical_api_key_id: key,
      api_key_id: key,
      worker_index: 1,
      worker_limit: 1,
      strict_api_fifo: true,
    };

    const tab = await baseResolver(message);
    const requestId = String(message?.request_id || "");
    if (requestId) {
      const eventType = message.type === "chat.request" ? "chat.diagnostics" : "image.diagnostics";
      await trySendSocket({
        type: eventType,
        kind: message.type === "voice.request" || message.type === "voice.live.start" ? "voice" : undefined,
        request_id: requestId,
        diagnostics: {
          extension_worker_router: "per-logical-api-strict-worker1-v28",
          extension_worker_router_revision: 28,
          extension_worker_strict_serial: true,
          extension_worker_index: 1,
          extension_worker_limit: 1,
          extension_worker_route_key: key,
          extension_worker_logical_api_key_id: key,
          routed_tab_id: tab?.id ?? null,
          routed_window_id: tab?.windowId ?? null,
        },
      }).catch(() => {});
    }
    return tab;
  };
})();