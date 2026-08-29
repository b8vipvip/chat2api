(() => {
  const KEY = "__CHAT2API_GENERATION_LIVENESS_V49__";
  if (globalThis[KEY]) return;

  const REQUEST_KEY = "__CHAT2API_REQUEST_CONTENT_V5__";
  const STALL_KEY = "__CHAT2API_REQUEST_STALL_GUARD_V34__";
  const INTERVAL_MS = 20000;
  const state = { version: 49, requestId: "", heartbeatSequence: 0, lastEmitAt: 0 };
  globalThis[KEY] = state;

  function visible(element) {
    if (!element) return false;
    try {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
    } catch (_) { return false; }
  }

  function generationControlVisible() {
    const selectors = [
      "button[data-testid='stop-button']",
      "button[aria-label='Stop streaming']",
      "button[aria-label='Stop generating']",
      "button[aria-label*='停止回答']",
      "button[aria-label*='停止生成']",
    ];
    return selectors.some(selector => [...document.querySelectorAll(selector)].some(node => visible(node) && !node.disabled));
  }

  async function report(requestId) {
    state.heartbeatSequence += 1;
    state.lastEmitAt = Date.now();
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "chat.diagnostics",
          request_id: requestId,
          diagnostics: {
            generation_liveness: "page-state-heartbeat-v49",
            generation_heartbeat_sequence: state.heartbeatSequence,
            generation_control_visible: generationControlVisible(),
            generating_observed: true,
            generation_liveness_interval_ms: INTERVAL_MS,
          },
        },
      });
    } catch (_) {}
  }

  async function tick() {
    const request = globalThis[REQUEST_KEY]?.active;
    const track = globalThis[STALL_KEY]?.track;
    const requestId = String(request?.requestId || "");
    if (!requestId || request?.cancelled) {
      state.requestId = "";
      state.heartbeatSequence = 0;
      state.lastEmitAt = 0;
      return;
    }
    if (state.requestId !== requestId) {
      state.requestId = requestId;
      state.heartbeatSequence = 0;
      state.lastEmitAt = 0;
    }
    if (String(track?.requestId || "") !== requestId || !track?.sawGenerating || track?.failed) return;
    if (Date.now() - state.lastEmitAt < INTERVAL_MS) return;

    // This heartbeat is deliberately diagnostic-only. In v42 the changing
    // `generation_sequence` field was interpreted by the server as real response
    // progress, so a page that stayed forever on a Stop/Thinking surface could
    // keep extending the 150s activity lease without producing any answer text.
    // v49 uses a different field that the server records but does not count as
    // generation activity. Only delta/snapshot/text growth advances that lease.
    await report(requestId);
  }

  state.tick = tick;
  state.generationControlVisible = generationControlVisible;
  state.constants = { interval_ms: INTERVAL_MS };
  setInterval(() => tick().catch(() => {}), 1000);
})();
