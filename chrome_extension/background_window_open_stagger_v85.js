(() => {
  const KEY = "__CHAT2API_WINDOW_OPEN_STAGGER_V85__";
  if (globalThis[KEY]) return;

  const REVISION = 85;
  const MIN_INTERVAL_MS = 15 * 1000;
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const state = {
    revision: REVISION,
    min_interval_ms: MIN_INTERVAL_MS,
    last_started_at: 0,
    sequence: 0,
    tail: Promise.resolve(),
    last: null,
  };

  async function create(options = {}, meta = {}) {
    const run = async () => {
      const now = Date.now();
      const waitMs = Math.max(0, Number(state.last_started_at || 0) + MIN_INTERVAL_MS - now);
      if (waitMs > 0) await delay(waitMs);
      const startedAt = Date.now();
      state.last_started_at = startedAt;
      state.sequence += 1;
      const reason = String(meta?.reason || "managed-chatgpt-window");
      state.last = {
        sequence: state.sequence,
        reason,
        waited_ms: waitMs,
        started_at_ms: startedAt,
        min_interval_ms: MIN_INTERVAL_MS,
      };
      await chrome.storage.local.set({ chat2apiWindowOpenStaggerV85: state.last }).catch(() => {});
      return chrome.windows.create(options);
    };
    const task = state.tail.then(run, run);
    state.tail = task.then(() => undefined, () => undefined);
    return task;
  }

  globalThis[KEY] = state;
  globalThis.chat2apiCreateWindowStaggered = create;
})();
