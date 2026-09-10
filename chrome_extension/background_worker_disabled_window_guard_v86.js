(() => {
  const KEY = "__CHAT2API_WORKER_DISABLED_WINDOW_GUARD_V86__";
  if (globalThis[KEY]) return;

  const DISABLED_KEY = "chat2apiWorkerMasterDisabledV61";
  const state = {
    version: 86,
    revision: 90,
    blocked: 0,
    last: null,
  };
  globalThis[KEY] = state;

  async function disabled() {
    try {
      const stored = await chrome.storage.local.get({ [DISABLED_KEY]: false });
      return stored?.[DISABLED_KEY] === true;
    } catch (_) {
      return false;
    }
  }

  const baseCreate = globalThis.chat2apiCreateWindowStaggered;
  if (typeof baseCreate !== "function") return;

  // v0.8.30 is admission-only here. This guard may reject a new router-owned
  // creation before it starts, but it never closes a window after creation.
  // Any lifecycle transition belongs to conversation_routing.js.
  globalThis.chat2apiCreateWindowStaggered = async function createWindowUnlessWorkerDisabled(options, meta = {}) {
    if (await disabled()) {
      state.blocked += 1;
      state.last = { action: "blocked-before-create", source: String(meta?.source || meta?.reason || "unknown"), at_ms: Date.now() };
      throw new Error("Worker is disabled; managed ChatGPT window creation is blocked by v86");
    }
    return baseCreate(options, meta);
  };
})();
