(() => {
  const KEY = "__CHAT2API_CAPACITY_CONTROL_V36__";
  if (globalThis[KEY]) return;

  const CONTROL_VERSION = 36;
  const V35_KEY = "__CHAT2API_CAPACITY_CONTROL_V35__";
  const state = {
    version: CONTROL_VERSION,
    ready: false,
    installed_at: new Date().toISOString(),
    last_dispatch_at: null,
    last_error: "",
  };
  globalThis[KEY] = state;

  function controller() {
    const value = globalThis[V35_KEY];
    if (!value || typeof value.handle !== "function" || typeof value.snapshot !== "function") return null;
    return value;
  }

  function capabilityMetadata(metadata = {}) {
    const ctl = controller();
    state.ready = Boolean(ctl);
    return {
      ...(metadata || {}),
      extension_control_version: CONTROL_VERSION,
      extension_control_ready: state.ready,
      extension_control_transport: "authoritative-global-dispatch-v36",
      extension_control_last_error: state.last_error || null,
    };
  }

  const baseHandler = typeof globalThis.handleServerMessage === "function"
    ? globalThis.handleServerMessage
    : (typeof handleServerMessage === "function" ? handleServerMessage : null);

  const wrappedHandler = async message => {
    if (String(message?.type || "") === "extension.control") {
      state.last_dispatch_at = new Date().toISOString();
      const ctl = controller();
      if (!ctl) {
        state.ready = false;
        state.last_error = "Capacity controller v35 is not ready";
        if (typeof trySendSocket === "function") {
          await trySendSocket({
            type: "extension.control.result",
            control_id: String(message?.control_id || ""),
            action: String(message?.action || ""),
            ok: false,
            data: {},
            error: state.last_error,
            metadata: capabilityMetadata({}),
          }).catch(() => false);
        }
        return;
      }
      state.ready = true;
      state.last_error = "";
      return ctl.handle(message);
    }
    if (typeof baseHandler === "function") return baseHandler(message);
  };
  wrappedHandler.__chat2apiCapacityControlV36 = true;

  // In MV3 service workers imported classic scripts share a global function
  // binding. Assign both the identifier and globalThis property so the socket
  // callback always sees the newest dispatcher, even across historical overlays.
  try { handleServerMessage = wrappedHandler; } catch (_) {}
  globalThis.handleServerMessage = wrappedHandler;

  if (typeof trySendSocket === "function") {
    const baseTrySendSocket = trySendSocket;
    trySendSocket = async payload => {
      if (payload?.type === "extension.status" || payload?.type === "extension.control.result") {
        payload = { ...payload, metadata: capabilityMetadata(payload.metadata || {}) };
      }
      return baseTrySendSocket(payload);
    };
  }

  async function reportCapability() {
    state.ready = Boolean(controller());
    if (typeof trySendSocket !== "function") return false;
    return trySendSocket({
      type: "extension.status",
      metadata: capabilityMetadata({
        extension_version: chrome.runtime.getManifest().version,
      }),
    });
  }

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (changes.socketState?.newValue === "connected") {
      setTimeout(() => reportCapability().catch(() => false), 120);
    }
  });

  setTimeout(() => reportCapability().catch(() => false), 350);
  state.report = reportCapability;
  state.capabilityMetadata = capabilityMetadata;
})();
