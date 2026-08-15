(() => {
  const KEY = "__CHAT2API_COMPLETION_FAST_V21__";
  if (globalThis[KEY]) return;

  const state = {
    requestId: null,
    identity: "",
    text: "",
    stableSince: 0,
    suppressedButton: null,
    restoreTimer: null,
  };
  globalThis[KEY] = state;

  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  function visible(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function stopButton() {
    for (const selector of [
      "button[data-testid='stop-button']",
      "button[aria-label='Stop streaming']",
      "button[aria-label='Stop generating']",
      "button[aria-label*='停止回答']",
      "button[aria-label*='停止生成']",
    ]) {
      const found = [...document.querySelectorAll(selector)].find(item => visible(item) && !item.disabled);
      if (found) return found;
    }
    return null;
  }

  function assistantNodes() {
    const result = [];
    const seen = new Set();
    for (const selector of [
      "[data-message-author-role='assistant']",
      "article[data-testid^='conversation-turn'] [data-message-author-role='assistant']",
    ]) {
      for (const node of document.querySelectorAll(selector)) {
        if (!seen.has(node) && visible(node)) {
          seen.add(node);
          result.push(node);
        }
      }
    }
    return result;
  }

  function nodeIdentity(node) {
    const turn = node?.closest("[data-message-id], article[id], article[data-testid]");
    return node?.getAttribute("data-message-id") || turn?.getAttribute("data-message-id") || turn?.id || turn?.getAttribute("data-testid") || "";
  }

  function nodeText(node) {
    if (!node) return "";
    for (const selector of ["[data-message-content]", ".markdown", "[class*='markdown']"]) {
      const candidates = [...node.querySelectorAll(selector)].filter(visible);
      for (let index = candidates.length - 1; index >= 0; index -= 1) {
        const text = normalize(candidates[index].innerText || candidates[index].textContent || "");
        if (text) return text;
      }
    }
    const clone = node.cloneNode(true);
    clone.querySelectorAll("button,svg,nav,footer,[aria-hidden='true']").forEach(item => item.remove());
    return normalize(clone.innerText || clone.textContent || "");
  }

  function isNewAssistant(active, node, identity, nodes) {
    if (!node) return false;
    if (nodes.length > Number(active?.baselineCount || 0)) return true;
    if (identity && active?.baselineIds && !active.baselineIds.has(identity)) return true;
    return Boolean(identity && active?.baselineIdentity && identity !== active.baselineIdentity);
  }

  function finalActionControls(node) {
    const root = node?.closest("article[data-testid^='conversation-turn'], article, [data-message-id]") || node;
    if (!root) return [];
    const selectors = [
      "button[data-testid*='copy']",
      "button[aria-label*='Copy']",
      "button[aria-label*='复制']",
      "button[data-testid*='thumb']",
      "button[aria-label*='Good response']",
      "button[aria-label*='Bad response']",
      "button[aria-label*='赞']",
      "button[aria-label*='踩']",
    ];
    const found = [];
    for (const selector of selectors) {
      for (const button of root.querySelectorAll(selector)) {
        if (visible(button) && !found.includes(button)) found.push(button);
      }
    }
    return found;
  }

  function transientStatusVisible() {
    const candidates = [...document.querySelectorAll("[role='status'], [aria-live='polite'], [aria-live='assertive']")].filter(visible);
    return candidates.some(node => {
      const text = normalize(node.innerText || node.textContent || "").toLowerCase();
      return /(^|\s)(thinking|analyzing|reasoning|searching|browsing|working|generating)(\s|$)|正在(思考|分析|推理|搜索|浏览|处理|生成)/i.test(text);
    });
  }

  async function emitDiagnostics(requestId, stableMs, actionCount, button) {
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "chat.diagnostics",
          request_id: requestId,
          diagnostics: {
            completion_fast_overlay: "v21",
            completion_recovery: "strong-final-actions-fast",
            completion_stable_ms: stableMs,
            completion_final_action_count: actionCount,
            completion_fast_stale_stop_label: normalize(`${button?.dataset?.testid || ""} ${button?.getAttribute?.("aria-label") || ""}`).slice(0, 120),
          },
        },
      });
    } catch (_) {}
  }

  function restoreSuppressedButton() {
    clearTimeout(state.restoreTimer);
    state.restoreTimer = null;
    const button = state.suppressedButton;
    state.suppressedButton = null;
    if (!button?.isConnected) return;
    const previous = button.dataset.chat2apiPerfPreviousVisibility;
    if (previous === "__empty__") button.style.removeProperty("visibility");
    else if (previous != null) button.style.visibility = previous;
    delete button.dataset.chat2apiPerfPreviousVisibility;
    delete button.dataset.chat2apiPerfStaleStopSuppressed;
  }

  async function suppress(active, button, stableMs, actionCount) {
    if (!button || button.dataset.chat2apiPerfStaleStopSuppressed === "v21") return;
    // Respect the conservative v6 overlay if it already owns the button.
    if (button.dataset.chat2apiStaleStopSuppressed === "v6") return;
    button.dataset.chat2apiPerfPreviousVisibility = button.style.visibility || "__empty__";
    button.dataset.chat2apiPerfStaleStopSuppressed = "v21";
    button.style.visibility = "hidden";
    state.suppressedButton = button;
    await emitDiagnostics(active.requestId, stableMs, actionCount, button);
    state.restoreTimer = setTimeout(restoreSuppressedButton, 2200);
  }

  async function tick() {
    const controller = globalThis.__CHAT2API_REQUEST_CONTENT_V5__;
    const active = controller?.active;
    if (!active?.requestId || active.cancelled) {
      if (state.suppressedButton) restoreSuppressedButton();
      state.requestId = null;
      state.identity = "";
      state.text = "";
      state.stableSince = 0;
      return;
    }

    if (state.requestId !== active.requestId) {
      restoreSuppressedButton();
      state.requestId = active.requestId;
      state.identity = "";
      state.text = "";
      state.stableSince = 0;
    }

    const nodes = assistantNodes();
    const latest = nodes[nodes.length - 1];
    const identity = nodeIdentity(latest);
    if (!isNewAssistant(active, latest, identity, nodes)) return;
    const text = nodeText(latest);
    if (!text) return;

    if (text !== state.text || identity !== state.identity) {
      state.text = text;
      state.identity = identity;
      state.stableSince = Date.now();
      return;
    }

    const button = stopButton();
    if (!button || !state.stableSince || transientStatusVisible()) return;
    const stableMs = Date.now() - state.stableSince;
    const actions = finalActionControls(latest);

    // A final copy/feedback action row plus stable assistant text and no live status is
    // a substantially stronger completion signal than a stale Stop button alone.
    // v6 remains the fallback for all weaker UI variants (2.5 s / 9 s paths).
    if (actions.length && stableMs >= 900) {
      await suppress(active, button, stableMs, actions.length);
    }
  }

  setInterval(() => tick().catch(() => {}), 180);
})();
