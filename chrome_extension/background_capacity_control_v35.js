(() => {
  const KEY = "__CHAT2API_CAPACITY_CONTROL_V35__";
  if (globalThis[KEY]) return;

  const RESERVE_KEY = "__CHAT2API_RESERVE_POOL_V29__";
  const SUPERVISOR_KEY = "__CHAT2API_TAB_SUPERVISOR_V32__";
  const MAX_ROUNDS = 8;
  const RESIZE_DEADLINE_MS = 55000;

  const state = { lastResult: null };
  globalThis[KEY] = state;

  const sleepControl = ms => new Promise(resolve => setTimeout(resolve, ms));

  function targetValue(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 1 || parsed > 32) {
      throw new Error("Worker target must be an integer between 1 and 32");
    }
    return Math.floor(parsed);
  }

  function reservePool() {
    const reserve = globalThis[RESERVE_KEY];
    if (!reserve || typeof reserve.snapshot !== "function" || typeof reserve.reconcile !== "function") {
      throw new Error("Reserve Pool v29 is not ready");
    }
    return reserve;
  }

  async function windowSnapshot() {
    const reserve = reservePool();
    const raw = await reserve.snapshot();
    const total = Math.max(0, Math.floor(Number(raw?.total || 0)));
    const active = Math.max(0, Math.min(total, Math.floor(Number(raw?.active || 0))));
    const target = Math.max(1, Math.min(32, Math.floor(Number(raw?.target || reserve.target || 1))));
    return {
      total,
      active,
      idle: Math.max(0, total - active),
      target,
      own: Math.max(0, Math.floor(Number(raw?.own || 0))),
      warm: Math.max(0, Math.floor(Number(raw?.warm || 0))),
      routed: Math.max(0, Math.floor(Number(raw?.routed || 0))),
      all_chatgpt_windows: raw?.live instanceof Set ? raw.live.size : total,
      observed_at: new Date().toISOString(),
    };
  }

  async function reportFreshStatus() {
    const reserve = reservePool();
    if (typeof reserve.report === "function") await reserve.report(true).catch(() => {});
  }

  async function resizeWorkers(requestedTarget) {
    const target = targetValue(requestedTarget);
    const reserve = reservePool();
    const supervisor = globalThis[SUPERVISOR_KEY];

    if (typeof reserve.refreshConfig === "function") {
      const authoritativeTarget = targetValue(await reserve.refreshConfig(true));
      if (authoritativeTarget !== target) {
        throw new Error(`Server runtime target mismatch (expected ${target}, received ${authoritativeTarget})`);
      }
    } else {
      throw new Error("Reserve Pool runtime-config refresh is unavailable");
    }

    let snapshot = await windowSnapshot();
    let previousTotal = -1;
    let stagnantRounds = 0;
    let rounds = 0;
    let pendingReason = "";
    const deadline = Date.now() + RESIZE_DEADLINE_MS;

    while (Date.now() < deadline && rounds < MAX_ROUNDS && snapshot.total !== target) {
      if (snapshot.total > target && snapshot.active > target) {
        pendingReason = "active-windows-protected";
        break;
      }

      previousTotal = snapshot.total;
      rounds += 1;
      await reserve.reconcile();
      if (supervisor && typeof supervisor.reconcile === "function") {
        await supervisor.reconcile().catch(() => null);
      }
      snapshot = await windowSnapshot();

      if (snapshot.total === target) break;
      if (snapshot.total === previousTotal) stagnantRounds += 1;
      else stagnantRounds = 0;

      if (stagnantRounds >= 2) {
        pendingReason = snapshot.total < target
          ? "prewarm-not-ready-or-ineligible"
          : "managed-windows-still-protected";
        break;
      }
      await sleepControl(250);
    }

    await reportFreshStatus();
    snapshot = await windowSnapshot();
    const targetReached = snapshot.total === target;
    if (!targetReached && !pendingReason) {
      pendingReason = snapshot.total > target && snapshot.active > target
        ? "active-windows-protected"
        : "target-not-reached-before-control-deadline";
    }

    return {
      target,
      target_reached: targetReached,
      pending_reason: pendingReason,
      rounds,
      window_snapshot: snapshot,
    };
  }

  async function emitResult(message, ok, data = {}, error = "") {
    const controlId = String(message?.control_id || "");
    const action = String(message?.action || "");
    const snapshot = data?.window_snapshot && typeof data.window_snapshot === "object"
      ? data.window_snapshot
      : null;
    const observedAt = snapshot?.observed_at || new Date().toISOString();
    const result = {
      version: 35,
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
      dispatcher
      && Number(dispatcher.version || 0) >= 36
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
    };
    if (snapshot) {
      metadata.reserve_window_telemetry_version = 29;
      metadata.reserve_window_total = Number(snapshot.total || 0);
      metadata.reserve_window_active = Number(snapshot.active || 0);
      metadata.reserve_window_idle = Number(snapshot.idle || 0);
      metadata.reserve_window_target = Number(snapshot.target || 0);
      metadata.reserve_window_updated_at = observedAt;
      metadata.reserve_window_all_chatgpt_windows = Number(snapshot.all_chatgpt_windows || 0);
    }

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
        const snapshot = await windowSnapshot();
        await reportFreshStatus();
        return emitResult(message, true, { window_snapshot: snapshot });
      }
      if (action === "workers.resize") {
        const result = await resizeWorkers(message?.payload?.target);
        return emitResult(message, true, result);
      }
      throw new Error(`Unsupported Extension control action: ${action || "(empty)"}`);
    } catch (error) {
      let snapshot = null;
      try { snapshot = await windowSnapshot(); } catch (_) {}
      return emitResult(
        message,
        false,
        snapshot ? { window_snapshot: snapshot } : {},
        String(error?.message || error),
      );
    }
  }

  // Native background.js dispatches extension.control directly into this API.
  // Publish it before installing any legacy wrapper so MV3 global binding quirks
  // cannot leave the controller permanently at control=v0.
  state.handle = handleControl;
  state.snapshot = windowSnapshot;
  state.resize = resizeWorkers;

  // Retain the historical overlay path for older Bridge entrypoints. It is now
  // optional: absence of a global base handler must not disable the controller.
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
