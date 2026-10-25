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

  function nativeStatus() {
    // Read only compact status surfaces in the current conversation. Never
    // scrape entire <main>, the prompt, or arbitrary assistant markdown.
    const active = globalThis.__CHAT2API_REQUEST_CONTENT_V6__?.active;
    if (Number(active?.submitStartedAt || 0) > 0 && active?.networkStreamSeen === true) {
      // Strictly UI-labelled summary widgets; no raw markdown or invisible CoT.
      for (const selector of [
        "main [data-testid*='reasoning-summary' i]",
        "main [data-testid*='thinking-summary' i]",
      ]) {
        for (const node of [...document.querySelectorAll(selector)].slice(-6)) {
          if (!visible(node)) continue;
          const text = String(node.innerText || node.textContent || "").replace(/\s+/g, " ").trim();
          const promptStart = String(active?.promptNormalized || "").slice(0, 80);
          if (text.length >= 8 && text.length <= 240
              && (!promptStart || !text.includes(promptStart))) {
            return {text, kind: "reasoning", source: "visible-chatgpt-ui-v196"};
          }
        }
      }
    }
    const selectors = [
      "main [data-testid*='thinking' i]",
      "main [data-testid*='reasoning' i]",
      "main [role='status']",
      "main [aria-live='polite']",
    ];
    const statusPattern = /^(?:正在(?:思考|分析|推理|生成|处理|搜索|浏览|读取|整理)(?:中|内容|回复|答案|文件)?|(?:thinking|analyzing|reasoning|generating|working|searching|browsing)(?: now|\.\.\.)?)$/i;
    for (const selector of selectors) {
      for (const node of [...document.querySelectorAll(selector)].slice(-12)) {
        if (!visible(node)) continue;
        const text = String(node.innerText || node.textContent || "").replace(/\s+/g, " ").trim();
        if (text.length > 0 && text.length <= 80 && statusPattern.test(text)) {
          return { text, kind: "working", source: "visible-chatgpt-ui-v196" };
        }
      }
    }
    if (generationControlVisible()) {
      return {text: "正在生成", kind: "working", source: "generation-control-v196"};
    }
    return null;
  }

  async function report(requestId) {
    state.heartbeatSequence += 1;
    state.lastEmitAt = Date.now();
    const native = nativeStatus();
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
            ...(native ? {
              visible_reasoning_status: native.text,
              visible_reasoning_status_kind: native.kind,
              visible_reasoning_source: native.source,
            } : {}),
          },
        },
      });
    } catch (_) {}
  }

  async function tick() {
    const request = globalThis.__CHAT2API_REQUEST_CONTENT_V6__?.active
      || globalThis[REQUEST_KEY]?.active;
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
    // The v34 stall tracker may not see the redesigned page's Stop control.
    // Bound this to the canonical request-v6 submission/network evidence.
    const pageActive = Number(request?.submitStartedAt || 0) > 0
      && (request?.networkStreamSeen === true || generationControlVisible());
    const legacyActive = String(track?.requestId || "") === requestId
      && track?.sawGenerating && !track?.failed;
    if (!pageActive && !legacyActive) return;
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
  state.nativeStatus = nativeStatus;
  state.constants = { interval_ms: INTERVAL_MS };
  setInterval(() => tick().catch(() => {}), 1000);
})();
