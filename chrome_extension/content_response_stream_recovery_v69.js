(() => {
  const KEY = "__CHAT2API_RESPONSE_STREAM_RECOVERY_V69__";
  if (globalThis[KEY]) return;

  const legacy = globalThis.__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__;
  if (legacy?.timer) {
    try { clearInterval(legacy.timer); } catch (_) {}
    legacy.timer = null;
    legacy.superseded_by = "response-stream-v69-epoch-safe";
  }

  const REQUEST_KEY = "__CHAT2API_REQUEST_CONTENT_V6__";
  const FALLBACK_AFTER_MS = 8000;
  const COMPLETE_STABLE_MS = 2200;
  const OBSERVER_GRACE_MS = 180000;
  const state = {
    version: 69,
    owner: "response-stream-v69-epoch-safe",
    request: null,
    timer: null,
    snapshots: 0,
    completions: 0,
  };
  globalThis[KEY] = state;

  async function emit(event) {
    try {
      await chrome.runtime.sendMessage({ type: "chat2api.event", event });
      return true;
    } catch (_) {
      return false;
    }
  }

  function startContext(active) {
    return {
      requestId: String(active?.requestId || ""),
      activeRef: active,
      startedAt: Date.now(),
      lastText: "",
      lastIdentity: "",
      changedAt: 0,
      detachedAt: 0,
      completed: false,
    };
  }

  async function tick() {
    const controller = globalThis[REQUEST_KEY];
    const active = controller?.active || null;
    const activeId = String(active?.requestId || "");
    if (activeId && (!state.request || state.request.requestId !== activeId || state.request.completed)) {
      state.request = startContext(active);
    }

    const ctx = state.request;
    if (!ctx || ctx.completed) return;
    const now = Date.now();
    if (!activeId) {
      if (!ctx.detachedAt) ctx.detachedAt = now;
      if (now - ctx.startedAt > OBSERVER_GRACE_MS) {
        state.request = null;
        return;
      }
    }

    const contract = controller?.contract || globalThis[REQUEST_KEY]?.contract;
    if (!contract?.currentAssistantState) return;
    const activeRef = activeId === ctx.requestId ? active : ctx.activeRef;
    if (!activeRef || activeRef.completed) {
      ctx.completed = true;
      return;
    }

    const candidate = contract.currentAssistantState(activeRef);
    if (!candidate?.isNew || !candidate.latest || !candidate.text) return;
    if (candidate.text !== ctx.lastText || candidate.identity !== ctx.lastIdentity) {
      ctx.lastText = candidate.text;
      ctx.lastIdentity = candidate.identity;
      ctx.changedAt = now;
    }

    const primaryRecent = Number(activeRef.lastCaptureAt || 0) > 0 && now - Number(activeRef.lastCaptureAt || 0) < FALLBACK_AFTER_MS;
    const controllerDetached = !activeId || activeId !== ctx.requestId;
    if (!controllerDetached && primaryRecent) return;
    if (!controllerDetached && now - ctx.startedAt < FALLBACK_AFTER_MS) return;

    if (candidate.text !== activeRef.lastRecoveredText) {
      activeRef.lastRecoveredText = candidate.text;
      state.snapshots += 1;
      await emit({
        type: "chat.snapshot",
        request_id: ctx.requestId,
        text: candidate.text,
        diagnostics: {
          response_stream_recovery: "epoch-safe-v69",
          response_epoch_revision: 69,
          response_epoch_candidate_reason: candidate.reason,
          response_recovery_controller_detached: controllerDetached,
        },
      });
    }

    if (contract.isGenerating?.()) return;
    if (!ctx.changedAt || now - ctx.changedAt < COMPLETE_STABLE_MS) return;

    const final = await contract.finalNodeText(candidate.latest);
    const finalText = String(final?.text || candidate.text || "");
    if (!finalText) return;
    ctx.completed = true;
    state.completions += 1;
    activeRef.completed = true;
    await emit({
      type: "chat.completed",
      request_id: ctx.requestId,
      text: finalText,
      diagnostics: {
        response_stream_recovery: "epoch-safe-v69",
        response_epoch_revision: 69,
        response_epoch_candidate_reason: candidate.reason,
        response_recovery_controller_detached: controllerDetached,
        response_format: "markdown",
        response_image_inlined_count: Number(final?.image_inlined_count || 0),
        response_image_inlined_bytes: Number(final?.image_inlined_bytes || 0),
      },
    });
  }

  state.tick = tick;
  state.constants = Object.freeze({
    fallback_after_ms: FALLBACK_AFTER_MS,
    complete_stable_ms: COMPLETE_STABLE_MS,
    observer_grace_ms: OBSERVER_GRACE_MS,
  });
  state.timer = setInterval(() => tick().catch(() => {}), 250);
})();
