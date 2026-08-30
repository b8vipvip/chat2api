(() => {
  const KEY = "__CHAT2API_RATE_LIMIT_GUARD_V52__";
  if (globalThis[KEY]) return;

  const STORAGE_KEY = "chatgptRateLimitGuardV52";
  const state = {
    lastSnapshot: null,
    baseResolveTargetTab: typeof globalThis.resolveTargetTab === "function" ? globalThis.resolveTargetTab : null,
  };
  globalThis[KEY] = state;

  function normalizedSnapshot(raw) {
    const now = Date.now();
    const until = Number(raw?.until_ms || 0);
    const active = Boolean(raw?.active && until > now);
    return {
      version: 52,
      active,
      detected_at_ms: Number(raw?.detected_at_ms || 0),
      until_ms: active ? until : 0,
      remaining_ms: active ? Math.max(0, until - now) : 0,
      text: String(raw?.text || ""),
      source: String(raw?.source || ""),
      url: String(raw?.url || ""),
    };
  }

  async function snapshot() {
    const stored = await chrome.storage.local.get({ [STORAGE_KEY]: null }).catch(() => ({}));
    const next = normalizedSnapshot(stored?.[STORAGE_KEY]);
    state.lastSnapshot = next;
    if (!next.active && stored?.[STORAGE_KEY]?.active) {
      await chrome.storage.local.set({
        [STORAGE_KEY]: {
          ...stored[STORAGE_KEY],
          active: false,
          until_ms: 0,
          cleared_at_ms: Date.now(),
        },
      }).catch(() => {});
    }
    return next;
  }

  function message(row) {
    const seconds = Math.max(1, Math.ceil(Number(row?.remaining_ms || 0) / 1000));
    return `ChatGPT is temporarily rate limited; Worker window creation and request dispatch are paused for ${seconds}s to avoid a reopen loop`;
  }

  async function assertReady(operation = "request") {
    const row = await snapshot();
    if (!row.active) return row;
    const error = new Error(message(row));
    error.code = "chatgpt_rate_limited";
    error.operation = operation;
    error.retry_after_ms = row.remaining_ms;
    throw error;
  }

  async function beforeWindowCreate(purpose = "worker") {
    return assertReady(`create-window:${purpose}`);
  }

  state.snapshot = snapshot;
  state.assertReady = assertReady;
  state.beforeWindowCreate = beforeWindowCreate;
  state.storageKey = STORAGE_KEY;

  if (state.baseResolveTargetTab) {
    const wrapped = async (...args) => {
      await assertReady("resolve-target-tab");
      return state.baseResolveTargetTab(...args);
    };
    wrapped.__chat2apiRateLimitGuardV52 = true;
    globalThis.resolveTargetTab = wrapped;
  }

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local" || !changes[STORAGE_KEY]) return;
    state.lastSnapshot = normalizedSnapshot(changes[STORAGE_KEY].newValue || null);
  });

  snapshot().catch(() => {});
})();
