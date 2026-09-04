(() => {
  const KEY = "__CHAT2API_WORKER_DISABLED_WINDOW_GUARD_V86__";
  if (globalThis[KEY]) return;

  const DISABLED_KEY = "chat2apiWorkerMasterDisabledV61";
  const state = {
    version: 86,
    blocked: 0,
    closed_after_create: 0,
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

  globalThis.chat2apiCreateWindowStaggered = async function createWindowUnlessWorkerDisabled(options, meta = {}) {
    if (await disabled()) {
      state.blocked += 1;
      state.last = { action: "blocked-before-create", source: String(meta?.source || "unknown"), at_ms: Date.now() };
      throw new Error("Worker is disabled; managed ChatGPT window creation is blocked by v86");
    }

    const created = await baseCreate(options, meta);
    if (await disabled()) {
      if (Number.isInteger(created?.id)) {
        try { await chrome.windows.remove(created.id); } catch (_) {}
      }
      state.closed_after_create += 1;
      state.last = { action: "closed-after-create", source: String(meta?.source || "unknown"), window_id: created?.id ?? null, at_ms: Date.now() };
      throw new Error("Worker was disabled while a managed ChatGPT window was being created; v86 closed it");
    }
    return created;
  };
})();
