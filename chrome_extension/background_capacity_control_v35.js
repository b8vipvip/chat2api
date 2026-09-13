(() => {
  const KEY = "__CHAT2API_CAPACITY_CONTROL_V35__";
  if (globalThis[KEY]) return;

  const OBSERVER_KEY = "__CHAT2API_WINDOW_OBSERVER_V90__";
  const WINDOW_LIMIT_KEY = "__CHAT2API_ROUTED_WINDOW_LIMIT_V121__";
  const PERSISTENT_POOL_KEY = "__CHAT2API_PERSISTENT_WINDOW_POOL_V132__";
  const state = { version: 35, revision: 132, lastResult: null };
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

  function persistentPool() {
    const value = globalThis[PERSISTENT_POOL_KEY];
    if (!value || typeof value.setTarget !== "function" || typeof value.snapshot !== "function") {
      throw new Error("Persistent Window Pool v132 is not ready");
    }
    return value;
  }

  async function windowSnapshot() {
    const limitState = globalThis[WINDOW_LIMIT_KEY]?.snapshot?.() || {};
    const pool = globalThis[PERSISTENT_POOL_KEY];
    if (pool && typeof pool.snapshot === "function") {
      const raw = await pool.snapshot();
      return {
        ...raw,
        target: Number(raw?.target || 0),
        total: Number(raw?.total || 0),
        active: Number(raw?.active || 0),
        idle: Number(raw?.idle || 0),
        warm: Number(raw?.warm || raw?.standby || 0),
        routed_window_limit: Number(limitState?.limit || 0) || null,
        routed_window_limit_source: String(limitState?.source || raw?.source || "unset"),
        speculative_windows: false,
        prewarmed_windows: true,
        route_window_authority: "conversation-routing-v30+persistent-pool-v132",
        window_decision_authority: "persistent-window-pool-v132",
        observed_at: raw?.observed_at || new Date().toISOString(),
      };
    }

    // Compatibility fallback for a partially upgraded Worker. v132-capable builds
    // normally never use this branch because the persistent pool is imported
    // before the capacity-control stack.
    const value = observer();
    await value.report(true).catch(() => {});
    const raw = value.snapshot();
    const activeRows = Array.isArray(raw?.active) ? raw.active : [];
    const inUse = activeRows.filter(row => String(row?.status || "") === "in_use").length;
    return {
      total: activeRows.length,
      active: inUse,
      idle: Math.max(0, activeRows.length - inUse),
      target: Number(limitState?.limit || 0),
      own: activeRows.length,
      warm: 0,
      routed: activeRows.length,
      all_chatgpt_windows: activeRows.length,
      routed_window_limit: Number(limitState?.limit || 0) || null,
      routed_window_limit_source: String(limitState?.source || "unset"),
      speculative_windows: false,
      prewarmed_windows: false,
      route_window_authority: "conversation-routing-v30",
      window_decision_authority: "conversation-routing-v30",
      observed_at: new Date().toISOString(),
    };
  }

  async function resizeWorkers(requestedTarget) {
    const target = targetValue(requestedTarget);
    // Concurrency remains an independent server scheduler setting. The physical
    // persistent-window target is changed only by windows.limit; the server keeps
    // the invariant window_target >= concurrency before sending either control.
    const snapshot = await windowSnapshot();
    return {
      target,
      target_reached: true,
      pending_reason: "",
      rounds: 0,
      window_policy: "persistent-window-target-independent-v132",
      window_snapshot: snapshot,
    };
  }

  async function applyWindowLimit(requestedTarget, source) {
    const target = targetValue(requestedTarget);
    const cleanSource = String(source || "explicit");
    // Keep v121's stored limit as a backwards-compatible admission guard, while
    // v132 becomes the physical lifecycle authority and prewarms to the target.
    const limiter = windowLimiter();
    const pool = persistentPool();
    const [limitApplied, poolApplied] = await Promise.all([
      limiter.setLimit(target, cleanSource),
      pool.setTarget(target, cleanSource),
    ]);
    const snapshot = await windowSnapshot();
    const reached = snapshot.login_ready === true && snapshot.worker_disabled !== true && Number(snapshot.total || 0) === target;
    let pendingReason = "";
    if (snapshot.worker_disabled === true) pendingReason = "worker_disabled";
    else if (snapshot.login_ready !== true) pendingReason = "login_not_ready";
    else if (Number(snapshot.total || 0) < target) pendingReason = "warming";
    else if (Number(snapshot.total || 0) > target) pendingReason = "busy_windows_protected";
    return {
      target,
      target_reached: reached,
      pending_reason: pendingReason,
      window_policy: "persistent-prewarmed-total-window-pool-v132",
      window_limit: limitApplied,
      persistent_pool: poolApplied,
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
      revision: 132,
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
      reserve_window_telemetry_version: 132,
      reserve_window_total: Number(snapshot?.total || 0),
      reserve_window_active: Number(snapshot?.active || 0),
      reserve_window_idle: Number(snapshot?.idle || 0),
      reserve_window_target: Number(snapshot?.target || 0),
      persistent_window_pool_revision: 132,
      persistent_window_pool_policy: "persistent-prewarmed-total-window-pool-v132",
      persistent_window_pool_warm: Number(snapshot?.warm || 0),
      persistent_window_pool_login_ready: snapshot?.login_ready === true,
      routed_window_limit: Number(snapshot?.routed_window_limit || 0) || null,
      routed_window_limit_source: String(snapshot?.routed_window_limit_source || "unset"),
      reserve_window_updated_at: observedAt,
      window_decision_authority: "persistent-window-pool-v132",
      route_window_authority: "conversation-routing-v30+persistent-pool-v132",
      speculative_windows: false,
      prewarmed_windows: true,
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
