(() => {
  const KEY = "__CHAT2API_RESPONSE_CAPTURE_V41__";
  if (globalThis[KEY]) return;

  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  function visible(element) {
    if (!element) return false;
    try {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
    } catch (_) {
      return false;
    }
  }

  function transientText(value) {
    const text = normalize(value).replace(/[.。…·:：]+$/g, "").trim().toLowerCase();
    return /^(正在)?(思考|分析|推理|生成|处理|搜索|浏览)(中)?$/.test(text) ||
      /^(thinking|analyzing|reasoning|generating|working|searching|browsing)( now)?$/.test(text);
  }

  function classifyTurn({ user = false, hasAssistantRole = false, roleVisible = false, hasFinalActions = false, hasTextHost = false } = {}) {
    if (user || !hasTextHost) return "";
    if (hasAssistantRole && !roleVisible) return "role-proxy";
    if (!hasAssistantRole && hasFinalActions) return "final-actions";
    return "";
  }

  const state = {
    version: 41,
    observer: null,
    timer: null,
    diagnosticsFor: new Set(),
    repairs: 0,
    contract: { classifyTurn },
  };
  globalThis[KEY] = state;

  function conversationTurns() {
    const result = [];
    const seen = new Set();
    for (const selector of [
      "article[data-testid^='conversation-turn']",
      "[data-testid^='conversation-turn']",
      "article[data-message-id]",
    ]) {
      for (const node of document.querySelectorAll(selector)) {
        if (!seen.has(node)) {
          seen.add(node);
          result.push(node);
        }
      }
    }
    return result;
  }

  function userTurn(turn) {
    if (!turn) return false;
    if (turn.getAttribute?.("data-message-author-role") === "user") return true;
    return Boolean(turn.querySelector?.("[data-message-author-role='user']"));
  }

  function finalActionCount(turn) {
    if (!turn?.querySelectorAll) return 0;
    const seen = new Set();
    for (const selector of [
      "button[data-testid*='thumb']",
      "button[aria-label*='Good response']",
      "button[aria-label*='Bad response']",
      "button[aria-label*='Regenerate']",
      "button[aria-label*='Try again']",
      "button[aria-label*='重新生成']",
      "button[aria-label*='重试']",
      "button[aria-label*='赞']",
      "button[aria-label*='踩']",
    ]) {
      for (const button of turn.querySelectorAll(selector)) {
        if (visible(button)) seen.add(button);
      }
    }
    return seen.size;
  }

  function textHost(root) {
    if (!root?.querySelectorAll) return null;
    for (const selector of [
      "[data-message-content]",
      ".markdown",
      "[class*='markdown']",
      "[class*='prose']",
    ]) {
      const candidates = [...root.querySelectorAll(selector)];
      for (let index = candidates.length - 1; index >= 0; index -= 1) {
        const candidate = candidates[index];
        const text = normalize(candidate.innerText || candidate.textContent || "");
        if (visible(candidate) && text && !transientText(text)) return candidate;
      }
    }
    const ownText = normalize(root.innerText || root.textContent || "");
    if (visible(root) && ownText && !transientText(ownText)) return root;
    return null;
  }

  function markProxy(host, source) {
    if (!host?.setAttribute) return false;
    if (host.getAttribute("data-message-author-role") === "assistant" && visible(host)) return false;
    host.setAttribute("data-message-author-role", "assistant");
    try {
      host.dataset.chat2apiAssistantProxy = "v41";
      host.dataset.chat2apiAssistantProxySource = source;
    } catch (_) {}
    state.repairs += 1;
    return true;
  }

  function repairInvisibleAssistantRoles() {
    let repaired = 0;
    const roles = [...document.querySelectorAll("[data-message-author-role='assistant']")];
    for (const role of roles) {
      if (visible(role)) continue;
      const host = textHost(role);
      if (!host || host === role) continue;
      if (markProxy(host, "invisible-role")) repaired += 1;
    }
    return { repaired, total: roles.length };
  }

  function repairCompletedAssistantTurns() {
    let repaired = 0;
    const turns = conversationTurns();
    for (const turn of turns) {
      if (userTurn(turn)) continue;
      const roles = [...turn.querySelectorAll("[data-message-author-role='assistant']")];
      if (roles.some(visible)) continue;
      const host = textHost(turn);
      const actions = finalActionCount(turn);
      const source = classifyTurn({
        user: false,
        hasAssistantRole: roles.length > 0,
        roleVisible: roles.some(visible),
        hasFinalActions: actions > 0,
        hasTextHost: Boolean(host),
      });
      if (source === "final-actions" && markProxy(host, source)) repaired += 1;
    }
    return { repaired, total: turns.length };
  }

  async function emitRepairDiagnostics(reason, roleStats, turnStats) {
    const controller = globalThis.__CHAT2API_REQUEST_CONTENT_V5__;
    const active = controller?.active;
    const requestId = String(active?.requestId || "");
    if (!requestId || state.diagnosticsFor.has(requestId)) return;
    if (!roleStats.repaired && !turnStats.repaired) return;
    state.diagnosticsFor.add(requestId);
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "chat.diagnostics",
          request_id: requestId,
          diagnostics: {
            response_capture_version: "v41",
            response_capture_recovery: "dom-assistant-proxy",
            response_capture_trigger: reason,
            assistant_role_nodes_total: roleStats.total,
            conversation_turns_total: turnStats.total,
            response_capture_role_repairs: roleStats.repaired,
            response_capture_turn_repairs: turnStats.repaired,
          },
        },
      });
    } catch (_) {}
  }

  async function scan(reason = "mutation") {
    state.timer = null;
    const roleStats = repairInvisibleAssistantRoles();
    const turnStats = repairCompletedAssistantTurns();
    await emitRepairDiagnostics(reason, roleStats, turnStats);
  }

  function schedule(reason) {
    if (state.timer) return;
    state.timer = setTimeout(() => scan(reason).catch(() => {}), 40);
  }

  try {
    state.observer = new MutationObserver(() => schedule("mutation"));
    state.observer.observe(document.documentElement || document, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["data-message-author-role", "data-testid", "aria-label", "class"],
    });
  } catch (_) {}

  scan("initial").catch(() => {});
})();
