(() => {
  const KEY = "__CHAT2API_CONVERSATION_QUOTA_BACKGROUND_V95__";
  if (globalThis[KEY]) return;

  const MESSAGE_TYPE = "chat2api.conversation-quota-blocked.v95";
  const RATE_LIMIT_STORAGE_KEY = "chatgptRateLimitGuardV52";
  const MAX_ROTATIONS = 2;
  const RETAIN_MS = 120000;
  const LOCAL_QUOTA_PATTERNS = [
    /聊天已暂停.{0,80}(?:额度|使用额度).{0,80}(?:重置|恢复)/i,
    /达到.{0,40}(?:包含|含有).{0,30}(?:文件|图像|图片).{0,40}(?:聊天次数)?上限/i,
    /请(?:发起|开始|新建).{0,30}(?:新的)?纯文本聊天/i,
    /chat (?:is )?paused.{0,100}(?:limit|usage|reset)/i,
    /(?:start|begin|create).{0,40}(?:a )?new (?:text-only|text only|plain text) chat/i,
    /(?:files?|images?).{0,80}(?:chat|conversation).{0,80}(?:limit|maximum)/i,
  ];
  const state = {
    version: 95,
    revision: 95,
    requests: new Map(),
    attempts: new Map(),
    inflight: new Map(),
    replays: 0,
    exhausted: 0,
    last: null,
  };
  globalThis[KEY] = state;

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const normalize = value => String(value || "").replace(/\s+/g, " ").trim();
  const looksConversationLocal = value => {
    const text = normalize(value);
    return Boolean(text && LOCAL_QUOTA_PATTERNS.some(pattern => pattern.test(text)));
  };

  function cloneMessage(message) {
    try { return structuredClone(message); } catch (_) {}
    try { return JSON.parse(JSON.stringify(message)); } catch (_) {}
    return { ...message, routing: { ...(message?.routing || {}) } };
  }

  function routedTextRequest(message) {
    return message?.type === "chat.request" && Boolean(message?.request_id) && Boolean(message?.routing?.api_key_id);
  }

  function remember(message) {
    if (!routedTextRequest(message)) return;
    const requestId = String(message.request_id);
    if (!state.requests.has(requestId) || !message?.__conversation_quota_failover_replay_v95) {
      state.requests.set(requestId, cloneMessage(message));
    }
  }

  function cleanup(requestId) {
    requestId = String(requestId || "");
    state.requests.delete(requestId);
    state.attempts.delete(requestId);
    state.inflight.delete(requestId);
  }

  function scheduleCleanup(requestId) {
    setTimeout(() => cleanup(requestId), RETAIN_MS);
  }

  async function clearConversationLocalGlobalCooldown(text, href) {
    const stored = await chrome.storage.local.get({ [RATE_LIMIT_STORAGE_KEY]: null }).catch(() => ({}));
    const current = stored?.[RATE_LIMIT_STORAGE_KEY];
    if (!current?.active) return false;
    const sameSurface = Boolean(
      looksConversationLocal(current?.text) ||
      (looksConversationLocal(text) && String(current?.url || "") === String(href || ""))
    );
    if (!sameSurface) return false;
    await chrome.storage.local.set({
      [RATE_LIMIT_STORAGE_KEY]: {
        ...current,
        active: false,
        until_ms: 0,
        cleared_at_ms: Date.now(),
        cleared_reason: "conversation-local-quota-failover-v95",
        conversation_local_text: String(text || "").slice(0, 240),
      },
    }).catch(() => {});
    return true;
  }

  async function diagnostic(requestId, fields = {}) {
    await trySendSocket({
      type: "chat.diagnostics",
      request_id: String(requestId || ""),
      diagnostics: {
        conversation_quota_failover_v95: true,
        conversation_quota_failover_max_rotations: MAX_ROTATIONS,
        ...fields,
      },
    }).catch(() => false);
  }

  function resetRecoveryBookkeeping(requestId) {
    const recovery = globalThis.__CHAT2API_BACKGROUND_REQUEST_RECOVERY_V40__;
    recovery?.recycled?.delete?.(requestId);
    recovery?.terminalSeen?.delete?.(requestId);
    const timer = recovery?.pending?.get?.(requestId);
    if (timer) clearTimeout(timer);
    recovery?.pending?.delete?.(requestId);
  }

  async function recycle(requestId, reason) {
    const recovery = globalThis.__CHAT2API_BACKGROUND_REQUEST_RECOVERY_V40__;
    if (typeof recovery?.recycleRequest !== "function") return false;
    const recycled = await recovery.recycleRequest(requestId, reason).catch(() => false);
    return Boolean(recycled);
  }

  async function terminalExhausted(requestId, detail) {
    state.exhausted += 1;
    await clearConversationLocalGlobalCooldown(detail?.text, detail?.href);
    await recycle(requestId, "conversation-quota-failover-exhausted-v95");
    globalThis.__CHAT2API_CONVERSATION_WORKERS_V25__?.releaseRequest?.(requestId);
    globalThis.__CHAT2API_CONVERSATION_DISPATCH_V1__?.requestTabs?.delete?.(requestId);
    await trySendSocket({
      type: "chat.error",
      request_id: requestId,
      error: `ChatGPT kept this conversation blocked after ${MAX_ROTATIONS} fresh-window rotations`,
      diagnostics: {
        conversation_quota_failover_v95: true,
        conversation_quota_failover_exhausted: true,
        conversation_quota_failover_attempts: Number(state.attempts.get(requestId) || 0),
        conversation_quota_failover_max_rotations: MAX_ROTATIONS,
        conversation_quota_block_text: String(detail?.text || "").slice(0, 240),
      },
    }).catch(() => false);
    cleanup(requestId);
  }

  async function rotateAndReplay(requestId, detail = {}) {
    requestId = String(requestId || "");
    const original = state.requests.get(requestId);
    if (!requestId || !original) return { ok: false, error: "original-request-unavailable" };

    const attempt = Number(state.attempts.get(requestId) || 0) + 1;
    state.attempts.set(requestId, attempt);
    if (attempt > MAX_ROTATIONS) {
      await terminalExhausted(requestId, detail);
      return { ok: false, exhausted: true, attempt };
    }

    state.last = {
      request_id: requestId,
      attempt,
      stage: String(detail?.stage || "conversation-blocked"),
      text: String(detail?.text || "").slice(0, 240),
      href: String(detail?.href || ""),
      at_ms: Date.now(),
    };

    await diagnostic(requestId, {
      conversation_quota_block_detected: true,
      conversation_quota_failover_attempt: attempt,
      conversation_quota_failover_action: "close-window-and-replay",
      conversation_quota_affinity_overridden: true,
      conversation_quota_block_stage: state.last.stage,
      conversation_quota_block_text: state.last.text,
    });

    // This surface is local to the current conversation. Clear v52 only when the
    // stored cooldown came from the same local surface; never erase an unrelated
    // account-wide rate limit detected by another Worker window.
    await clearConversationLocalGlobalCooldown(detail?.text, detail?.href);

    const recycled = await recycle(requestId, "conversation-local-quota-blocked-v95");
    if (!recycled) {
      await diagnostic(requestId, {
        conversation_quota_failover_attempt: attempt,
        conversation_quota_failover_action: "recycle-missed-replay-anyway",
      });
    }

    // recycleRequest marks the request as recycled so ordinary terminal cleanup
    // cannot loop. This is intentionally a non-terminal replay of the same API
    // request, so clear only that bookkeeping after the poisoned window is gone.
    await sleep(180);
    resetRecoveryBookkeeping(requestId);

    const replay = cloneMessage(original);
    replay.__conversation_quota_failover_replay_v95 = attempt;
    replay.routing = { ...(original.routing || {}) };
    state.replays += 1;
    await handleServerMessage(replay);

    await diagnostic(requestId, {
      conversation_quota_failover_attempt: attempt,
      conversation_quota_failover_action: "replayed-on-fresh-window",
      conversation_quota_affinity_overridden: true,
    });
    scheduleCleanup(requestId);
    return { ok: true, replayed: true, attempt };
  }

  state.rotateAndReplay = rotateAndReplay;
  state.cleanup = cleanup;
  state.looksConversationLocal = looksConversationLocal;

  const baseHandleServerMessage = handleServerMessage;
  handleServerMessage = async function handleConversationQuotaFailoverV95(message) {
    remember(message);
    return baseHandleServerMessage(message);
  };

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === MESSAGE_TYPE) {
      const requestId = String(message?.request_id || "");
      if (!requestId) {
        sendResponse?.({ ok: false, error: "request-id-required" });
        return false;
      }
      if (state.inflight.has(requestId)) {
        sendResponse?.({ ok: true, deduplicated: true });
        return false;
      }
      const task = rotateAndReplay(requestId, message)
        .catch(async error => {
          await diagnostic(requestId, {
            conversation_quota_failover_failed: true,
            conversation_quota_failover_error: String(error?.message || error),
          });
          await terminalExhausted(requestId, message);
          return { ok: false, error: String(error?.message || error) };
        })
        .finally(() => state.inflight.delete(requestId));
      state.inflight.set(requestId, task);
      task.then(result => sendResponse?.(result)).catch(() => sendResponse?.({ ok: false }));
      return true;
    }

    if (message?.type === "chat2api.event") {
      const event = message.event || {};
      if (["chat.completed", "chat.error", "chat.cancelled"].includes(event.type)) {
        const requestId = String(event.request_id || "");
        if (requestId) cleanup(requestId);
      }
    }
    return false;
  });
})();
