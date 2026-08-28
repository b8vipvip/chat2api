(() => {
  const KEY = "__CHAT2API_TOOL_ISOLATION_V48__";
  if (globalThis[KEY]) return;

  const state = {
    version: 48,
    scans: 0,
    blocked: 0,
    last_category: "",
    last_action: "",
    last_context: "",
    last_at_ms: 0,
  };
  const handled = new WeakSet();
  let scanTimer = 0;

  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  const visible = node => {
    if (!(node instanceof Element)) return false;
    const rect = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) > 0;
  };

  const labelOf = node => normalize(`${node?.getAttribute?.("aria-label") || ""} ${node?.innerText || node?.textContent || ""}`);
  const positiveAction = /^(reconnect|connect|enable|authorize|allow|install|use|continue|重新连接|连接|启用|授权|允许|安装|使用|继续)(?:\s|$)/i;
  const negativeAction = /^(not now|maybe later|later|cancel|close|dismiss|skip|no thanks|don'?t connect|暂不|以后再说|取消|关闭|跳过|不用了|不连接|拒绝)$/i;
  const integrationContext = /(plugin|plugins|connector|connectors|connected app|connected apps|integration|integrations|action|actions|app connection|connection (?:has )?expired|reconnect .* connection|use this connection|插件|连接器|已连接应用|应用连接|集成|连接已过期|重新连接|使用该连接|才能在此次请求中使用该连接)/i;
  const authDanger = /(captcha|verify you are human|security check|two[- ]?factor|2fa|authenticator|passkey|password|payment|billing|subscription|identity verification|人机验证|安全验证|两步验证|双重验证|密码|付款|账单|订阅|身份验证)/i;

  function activeRequestId() {
    return String(globalThis.__CHAT2API_REQUEST_CONTENT_V5__?.active?.requestId || "");
  }

  async function report(category, action, context) {
    const requestId = activeRequestId();
    const diagnostics = {
      tool_isolation: "tool-isolation-v48",
      external_account_tools_disabled: true,
      tool_surface_blocked: true,
      tool_surface_category: category,
      tool_surface_action: action,
      tool_surface_context: normalize(context).slice(0, 240),
    };
    try {
      if (requestId) {
        await chrome.runtime.sendMessage({
          type: "chat2api.event",
          event: { type: "chat.diagnostics", request_id: requestId, diagnostics },
        });
        await chrome.runtime.sendMessage({
          type: "chat2api.tool-blocked",
          request_id: requestId,
          diagnostics,
        });
      }
    } catch (_) {}
  }

  function candidateRoots() {
    const result = [];
    const seen = new Set();
    const selectors = [
      "[role='dialog']",
      "[aria-modal='true']",
      "[data-state='open'][role='dialog']",
      "article",
      "[data-testid^='conversation-turn']",
      "main",
    ];
    for (const selector of selectors) {
      for (const node of document.querySelectorAll(selector)) {
        if (!visible(node) || seen.has(node)) continue;
        seen.add(node);
        result.push(node);
      }
    }
    return result;
  }

  function actionButtons(root) {
    return [...root.querySelectorAll("button,[role='button']")]
      .filter(visible)
      .map(node => ({ node, label: labelOf(node) }))
      .filter(item => item.label && item.label.length <= 140);
  }

  function classify(root) {
    const context = labelOf(root).slice(0, 2200);
    if (!context || authDanger.test(context)) return null;
    const buttons = actionButtons(root);
    const positive = buttons.find(item => positiveAction.test(item.label));
    const negative = buttons.find(item => negativeAction.test(item.label));
    const connectorPhrase = integrationContext.test(context);
    const reconnectPair = Boolean(positive && /reconnect|重新连接/i.test(positive.label) && negative);
    if (!connectorPhrase && !reconnectPair) return null;
    if (!positive && !negative) return null;
    return { context, buttons, positive, negative, category: "external-account-tool" };
  }

  function dismissRoot(root, match) {
    if (handled.has(root)) return false;
    handled.add(root);
    let action = match.negative;
    if (!action) {
      action = match.buttons.find(item => /^(close|dismiss|cancel|关闭|取消)$/i.test(item.label));
    }
    if (!action) {
      try {
        root.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", code: "Escape", bubbles: true}));
        document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", code: "Escape", bubbles: true}));
      } catch (_) {}
      state.blocked += 1;
      state.last_category = match.category;
      state.last_action = "escape";
      state.last_context = match.context.slice(0, 240);
      state.last_at_ms = Date.now();
      report(match.category, "escape", match.context).catch(() => {});
      return true;
    }
    try {
      action.node.click();
      state.blocked += 1;
      state.last_category = match.category;
      state.last_action = action.label;
      state.last_context = match.context.slice(0, 240);
      state.last_at_ms = Date.now();
      report(match.category, action.label, match.context).catch(() => {});
      return true;
    } catch (_) {
      return false;
    }
  }

  function clearComposerIntegrationChips() {
    const composer = document.querySelector("#prompt-textarea")?.closest("form") || document.querySelector("form");
    if (!composer) return 0;
    let cleared = 0;
    for (const button of composer.querySelectorAll("button,[role='button']")) {
      if (!visible(button)) continue;
      const label = labelOf(button);
      if (!/(remove|clear|disconnect|移除|清除|断开).*(plugin|connector|app|integration|插件|连接器|应用|集成)/i.test(label)) continue;
      try { button.click(); cleared += 1; } catch (_) {}
    }
    return cleared;
  }

  function scan() {
    state.scans += 1;
    clearComposerIntegrationChips();
    let blocked = 0;
    for (const root of candidateRoots()) {
      const match = classify(root);
      if (match && dismissRoot(root, match)) blocked += 1;
    }
    return blocked;
  }

  function schedule(delay = 50) {
    clearTimeout(scanTimer);
    scanTimer = setTimeout(scan, Math.max(0, Number(delay || 0)));
  }

  const listener = (message, sender, sendResponse) => {
    if (message?.type === "chat2api.tool-isolation.preflight") {
      const blocked = scan();
      sendResponse({ ok: true, version: 48, blocked, external_account_tools_disabled: true });
      return true;
    }
    if (message?.type === "chat2api.tool-isolation.status") {
      sendResponse({ ok: true, ...state });
      return true;
    }
    return false;
  };
  chrome.runtime.onMessage.addListener(listener);

  const observer = new MutationObserver(() => schedule(30));
  observer.observe(document.documentElement, {childList: true, subtree: true, attributes: true, attributeFilter: ["role", "aria-modal", "data-state", "class", "style"]});
  setInterval(scan, 1000);
  scan();

  globalThis[KEY] = Object.freeze({ version: 48, state, scan });
})();
