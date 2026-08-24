(() => {
  const KEY = "__CHAT2API_CAPACITY_CAPABILITY_V37__";
  if (globalThis[KEY]) return;

  const V35_KEY = "__CHAT2API_CAPACITY_CONTROL_V35__";
  const V36_KEY = "__CHAT2API_CAPACITY_CONTROL_V36__";
  const CONTROL_VERSION = 36;
  const state = {
    version: 37,
    ready: false,
    installed_at: new Date().toISOString(),
    last_report_at: null,
    last_report_reason: "",
    last_report_ok: false,
    last_error: "",
    report_count: 0,
    runtime_errors: [],
  };
  globalThis[KEY] = state;

  function errorText(error) {
    const value = String(error?.stack || error?.message || error || "unknown runtime error");
    return value.length > 8000 ? value.slice(0, 8000) + "…" : value;
  }

  function recordRuntimeError(kind, error) {
    const row = {
      at: new Date().toISOString(),
      kind: String(kind || "runtime"),
      error: errorText(error),
    };
    state.runtime_errors.push(row);
    if (state.runtime_errors.length > 20) state.runtime_errors.splice(0, state.runtime_errors.length - 20);
    state.last_error = row.error;
    console.warn("chat2api capacity runtime error", row.kind, row.error);
    setTimeout(() => report(`runtime-error:${row.kind}`).catch(() => false), 0);
  }

  if (typeof self?.addEventListener === "function") {
    self.addEventListener("error", event => {
      const error = event?.error || event?.message || "Service Worker error event";
      recordRuntimeError("error", error);
    });
    self.addEventListener("unhandledrejection", event => {
      recordRuntimeError("unhandledrejection", event?.reason || "Unhandled promise rejection");
    });
  }

  function capability() {
    const v35 = globalThis[V35_KEY];
    const v36 = globalThis[V36_KEY];
    const controllerReady = Boolean(v35 && typeof v35.handle === "function" && typeof v35.snapshot === "function");
    const nativeReady = Boolean(
      controllerReady
      && Number(globalThis.__CHAT2API_NATIVE_CAPACITY_CONTROL_VERSION__ || 0) >= CONTROL_VERSION
      && globalThis.__CHAT2API_NATIVE_CAPACITY_DISPATCH_V37__ === true
    );
    const overlayReady = Boolean(
      controllerReady
      && v36
      && Number(v36.version || 0) >= CONTROL_VERSION
      && globalThis.handleServerMessage?.__chat2apiCapacityControlV36 === true
    );
    state.ready = nativeReady || overlayReady;
    const latestRuntimeError = state.runtime_errors.length ? state.runtime_errors[state.runtime_errors.length - 1] : null;
    return {
      extension_version: chrome.runtime.getManifest().version,
      extension_control_version: state.ready ? CONTROL_VERSION : 0,
      extension_control_ready: state.ready,
      extension_control_transport: nativeReady
        ? "background-native-dispatch-v37"
        : (overlayReady ? "overlay-dispatch-v36-with-reporter-v37" : "control-stack-not-ready-v37"),
      extension_control_last_error: state.last_error || null,
      extension_control_capability_reporter: 37,
      extension_control_capability_reported_at: new Date().toISOString(),
      extension_control_native_version: Number(globalThis.__CHAT2API_NATIVE_CAPACITY_CONTROL_VERSION__ || 0),
      extension_control_native_ready: nativeReady,
      extension_runtime_error_count: state.runtime_errors.length,
      extension_runtime_error_last: latestRuntimeError,
    };
  }

  async function report(reason = "manual") {
    state.report_count += 1;
    state.last_report_reason = String(reason || "manual");
    state.last_report_at = new Date().toISOString();
    const metadata = capability();
    try {
      if (typeof sendSocket !== "function") throw new Error("Base WebSocket sender is unavailable");
      await sendSocket({ type: "extension.status", metadata });
      state.last_report_ok = true;
      if (!state.runtime_errors.length) state.last_error = "";
      return true;
    } catch (error) {
      state.last_report_ok = false;
      state.last_error = errorText(error);
      console.warn("chat2api capacity capability v37 report failed", state.last_error);
      return false;
    }
  }

  function schedule(reason, delay) {
    setTimeout(() => report(reason).catch(() => false), delay);
  }

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (changes.socketState?.newValue === "connected") {
      schedule("socket-connected-120ms", 120);
      schedule("socket-connected-1200ms", 1200);
    }
  });

  chrome.alarms.onAlarm.addListener(alarm => {
    if (alarm?.name === "chat2api-keepalive") report("keepalive-alarm").catch(() => false);
  });

  schedule("startup-500ms", 500);
  schedule("startup-2000ms", 2000);
  schedule("startup-5000ms", 5000);
  state.report = report;
  state.capability = capability;
  state.recordRuntimeError = recordRuntimeError;
})();
