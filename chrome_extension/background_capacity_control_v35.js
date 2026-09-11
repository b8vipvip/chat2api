(() => {
  const KEY = "__CHAT2API_CAPACITY_CONTROL_V35__";
  if (globalThis[KEY]) return;

  const OBSERVER_KEY = "__CHAT2API_WINDOW_OBSERVER_V90__";
  const WINDOW_LIMIT_KEY = "__CHAT2API_ROUTED_WINDOW_LIMIT_V121__";
  const state = { version: 35, revision: 121, lastResult: null };
  globalThis[KEY] = state;

  function targetValue(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 1 || parsed > 32) {
      throw new Error("Worker target must be an integer between 1 and 32");
    }
    return Math.floor(parsed);
  }

  function observer() {
    const value = globalThis[OBSERVER_KEY];
    if (!value || typeof value.snapshot !== "function" || typeof value.report !== "function") {
      throw new Error("Window Observer v90 is not ready");
    }
    return value;
  }

  function windowLimiter() {
    const value = globalThis[WINDOW_LIMIT_KEY];
    if (!value || typeof value.setLimit !== "function" || typeof value.snapshot !== "function") {
      throw new Error("Routed Window Limit v121 is not ready");
    }
    return value;
  }

  async function windowSnapshot() {
    const value = observer();
    await value.report(true).catch(() => {});
    const raw = value.snapshot();
    const activeRows = Array.isArray(raw?.active) ? raw.active : [];
    const inUse = activeRows.filter(row => String(row?.status || "") === "in_use").length;
    const limitState = globalThis[WINDOW_LIMIT_KEY]?.snapshot?.() || {};
    return {
      total: activeRows.length,
      active: inUse,
      idle: Math.max(0, activeRows.length - inUse),
      target: 0,
      own: activeRows.length,
      warm: 0,
      routed: activeRows.length,
      all_chatgpt_windows: activeRows.length,
      routed_window_limit: Number(limitState?.limit || 0) || null,
      routed_window_limit_source: String(limitState?.source || "unset"),
      speculative_windows: false,
      route_window_authority: "conversation-routing-v30",
      observed_at: new Date().toISOString(),
    };
  }

  async function resizeWorkers(requestedTarget) {
    const target = targetValue(requestedTarget);
    // v0.8.30+ deliberately has no browser window pool to resize. This control
    // acknowledges the server's distinct-API concurrency value but never creates
    // or closes ChatGPT windows. Routes are opened on demand by the sole router.
    const snapshot = await windowSnapshot();
    return {
      target,
      target_reached: true,
      pending_reason: "",
      rounds: 0,
      window_policy: "on-demand-single-authority-v30",
      window_snapshot: snapshot,
    };
  }

  async function applyWindowLimit(requestedTarget, source) {
    const target = targetValue(requestedTarget);
    const limiter = windowLimiter();
    const applied = await limiter.setLimit(target, String(source || "explicit"));
    const snapshot = await windowSnapshot();
    return {
      target,
      target_reached: Number(applied?.limit || 0) === target,
      pending_reason: String(applied?.reconcile?.deferred > 0 ? "busy_windows_protected" : ""),
      window_policy: "on-demand-hard-cap-v121",
      window_limit: applied,
      window_snapshot: snapshot,
    };
  }

  async function emitResult(message, ok, data = {}, error = "") {
    const controlId = String(message?.control_id || "");
    const action = String(message?.action || "");
    const snapshot = data?.window_snapshot && typeof data.window_snapshot === "object" ? data.window_snapshot : null;
    const observedAt = snapshot?.observed_at || new Date().toISOString();
    const result = {
      version: 35,
      revision: 121,
      control_id: controlId,
      action,
      ok: Boolean(ok),
      data: data && typeof data === "object" ? data : {},
      error: String(error || ""),
      observed_at: observedAt,
    };
    state.lastResult = result;

    const dispatcher = globalThis.__CHAT2API_CAPACITY_CONTROL_V36__;
    const nativeReady = Boolean(
      Number(globalThis.__CHAT2API_NATIVE_CAPACITY_CONTROL_VERSION__ || 0) >= 36
      && globalThis.__CHAT2API_NATIVE_CAPACITY_DISPATCH_V37__ === true
    );
    const overlayReady = Boolean(
      dispatcher && Number(dispatcher.version || 0) >= 36
      && globalThis.handleServerMessage?.__chat2apiCapacityControlV36 === true
    );
    const controlReady = nativeReady || overlayReady;
    const metadata = {
      extension_control_version: controlReady ? 36 : 35,
      extension_control_ready: controlReady,
      extension_control_transport: nativeReady
        ? "capacity-result-v35-via-native-v37"
        : (overlayReady ? "capacity-result-v35-via-dispatch-v36" : "capacity-controller-v35"),
      extension_control_capability_reporter: nativeReady ? 37 : null,
      extension_control_result: result,
      reserve_window_telemetry_version: 90,
      reserve_window_total: Number(snapshot?.total || 0),
      reserve_window_active: Number(snapshot?.active || 0),
      reserve_window_idle: Number(snapshot?.idle || 0),
      reserve_window_target: 0,
      routed_window_limit: Number(snapshot?.routed_window_limit || 0) || null,
      routed_window_limit_source: String(snapshot?.routed_window_limit_source || "unset"),
      reserve_window_updated_at: observedAt,
      // v121 is an admission/limit coordinator only. The conversation router
      // remains the sole authority for routed-window lifecycle mutation.
      window_decision_authority: "conversation-routing-v30",
      speculative_windows: false,
    };

    if (typeof trySendSocket !== "function") throw new Error("Extension WebSocket sender is unavailable");
    const sent = await trySendSocket({
      type: "extension.control.result",
      control_id: controlId,
      action,
      ok: Boolean(ok),
      data: result.data,
      error: result.error,
      metadata,
    });
    if (!sent) throw new Error("Failed to send Extension control confirmation");
    return result;
  }

  async function handleControl(message) {
    const action = String(message?.action || "");
    try {
      if (action === "windows.snapshot") {
        return emitResult(message, true, { window_snapshot: await windowSnapshot() });
      }
      if (action === "workers.resize") {
        return emitResult(message, true, await resizeWorkers(message?.payload?.target));
      }
      if (action === "windows.limit") {
        return emitResult(
          message,
          true,
          await applyWindowLimit(message?.payload?.target, message?.payload?.source),
        );
      }
      throw new Error(`Unsupported Extension control action: ${action || "(empty)"}`);
    } catch (error) {
      let snapshot = null;
      try { snapshot = await windowSnapshot(); } catch (_) {}
      return emitResult(message, false, snapshot ? { window_snapshot: snapshot } : {}, String(error?.message || error));
    }
  }

  state.handle = handleControl;
  state.snapshot = windowSnapshot;
  state.resize = resizeWorkers;
  state.applyWindowLimit = applyWindowLimit;

  const baseHandler = globalThis.handleServerMessage;
  if (typeof baseHandler === "function") {
    const wrappedHandler = async message => {
      if (String(message?.type || "") === "extension.control") return handleControl(message);
      return baseHandler(message);
    };
    wrappedHandler.__chat2apiCapacityControlV35 = true;
    globalThis.handleServerMessage = wrappedHandler;
  }
})();
