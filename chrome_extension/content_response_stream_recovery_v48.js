(() => {
  const KEY = "__CHAT2API_RESPONSE_STREAM_RECOVERY_V48__";
  if (globalThis[KEY]) return;

  const state = {
    version: 48,
    request: null,
    timer: null,
    deltas: 0,
    snapshots: 0,
    completions: 0,
  };
  globalThis[KEY] = state;

  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  const visible = node => {
    if (!(node instanceof Element)) return false;
    try {
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
    } catch (_) { return false; }
  };

  function turns() {
    const nodes = [];
    const seen = new Set();
    for (const selector of ["article[data-testid^='conversation-turn']", "[data-testid^='conversation-turn']", "article[data-message-id]"]) {
      for (const node of document.querySelectorAll(selector)) {
        if (seen.has(node)) continue;
        seen.add(node);
        nodes.push(node);
      }
    }
    return nodes;
  }

  const isUserTurn = turn => turn?.getAttribute?.("data-message-author-role") === "user" || Boolean(turn?.querySelector?.("[data-message-author-role='user']"));
  const isAssistantTurn = turn => turn?.getAttribute?.("data-message-author-role") === "assistant" || Boolean(turn?.querySelector?.("[data-message-author-role='assistant']"));

  function turnText(turn) {
    if (!turn) return "";
    const candidates = [];
    for (const selector of ["[data-message-author-role='assistant'] [data-message-content]", "[data-message-author-role='assistant'] .markdown", "[data-message-author-role='assistant'] [class*='markdown']", "[data-message-author-role='assistant'] [class*='prose']", "[data-message-content]", ".markdown", "[class*='markdown']", "[class*='prose']"]) {
      for (const node of turn.querySelectorAll(selector)) {
        if (!visible(node)) continue;
        const text = normalize(node.innerText || node.textContent || "");
        if (text) candidates.push(text);
      }
    }
    if (candidates.length) return candidates[candidates.length - 1];
    return normalize(turn.innerText || turn.textContent || "");
  }

  function transient(text) {
    const value = normalize(text).replace(/[.。…·:：]+$/g, "").toLowerCase();
    return /^(正在)?(思考|分析|推理|生成|处理|搜索|浏览)(中)?$/.test(value) || /^(thinking|analyzing|reasoning|generating|working|searching|browsing)( now)?$/.test(value);
  }

  function integrationSurface(turn) {
    const text = normalize(turn?.innerText || turn?.textContent || "");
    if (!text) return false;
    const positive = [...turn.querySelectorAll("button,[role='button']")]
      .filter(visible)
      .some(button => /^(reconnect|connect|enable|authorize|install|重新连接|连接|启用|授权|安装)(?:\s|$)/i.test(normalize(button.innerText || button.textContent || button.getAttribute("aria-label") || "")));
    const context = /(plugin|connector|connected app|integration|connection (?:has )?expired|use this connection|插件|连接器|已连接应用|集成|连接已过期|使用该连接|才能在此次请求中使用该连接)/i.test(text);
    return positive && context;
  }

  function stopVisible() {
    return [...document.querySelectorAll("button")].some(button => {
      if (!visible(button)) return false;
      const label = normalize(`${button.getAttribute("aria-label") || ""} ${button.textContent || ""}`);
      return /stop streaming|stop generating|停止生成|停止回答/i.test(label);
    });
  }

  function finalActions(turn) {
    if (!turn) return false;
    return [...turn.querySelectorAll("button")].some(button => {
      if (!visible(button)) return false;
      const label = normalize(`${button.getAttribute("aria-label") || ""} ${button.textContent || ""}`);
      return /good response|bad response|regenerate|try again|copy|赞|踩|重新生成|重试|复制/i.test(label);
    });
  }

  function signature(turn, index) {
    return String(turn?.getAttribute?.("data-message-id") || turn?.dataset?.messageId || turn?.getAttribute?.("data-testid") || `turn:${index}`);
  }

  function captureBaseline(active) {
    const all = turns();
    const baseline = new Map();
    let assistantCount = 0;
    let userCount = 0;
    all.forEach((turn, index) => {
      if (isUserTurn(turn)) userCount += 1;
      if (!isAssistantTurn(turn)) return;
      assistantCount += 1;
      baseline.set(signature(turn, index), turnText(turn));
    });
    const controllerCount = Number(active?.baselineCount || 0);
    return {
      requestId: String(active.requestId || ""),
      startedAt: Date.now(),
      baselineAssistantCount: Math.max(assistantCount, controllerCount),
      baselineUserCount: userCount,
      baseline,
      controllerBaselineIds: active?.baselineIds instanceof Set ? new Set(active.baselineIds) : new Set(),
      controllerBaselineIdentity: String(active?.baselineIdentity || ""),
      lastText: "",
      lastSignature: "",
      changedAt: 0,
      emittedText: "",
      completed: false,
      diagnosticSent: false,
    };
  }

  function currentCandidate(ctx) {
    const all = turns();
    const userIndexes = [];
    all.forEach((turn, index) => { if (isUserTurn(turn)) userIndexes.push(index); });
    const latestUser = userIndexes.length ? userIndexes[userIndexes.length - 1] : -1;
    const userAdvanced = userIndexes.length > ctx.baselineUserCount;
    const assistant = [];
    all.forEach((turn, index) => {
      if (!isAssistantTurn(turn) || integrationSurface(turn)) return;
      const text = turnText(turn);
      if (!text || transient(text)) return;
      assistant.push({ turn, index, text, sig: signature(turn, index) });
    });
    if (!assistant.length) return null;

    if (userAdvanced) {
      const afterUser = assistant.filter(item => item.index > latestUser);
      if (afterUser.length) return afterUser[afterUser.length - 1];
    }

    const newest = assistant[assistant.length - 1];
    const baselineText = ctx.baseline.get(newest.sig);
    const countAdvanced = assistant.length > ctx.baselineAssistantCount;
    const identityAdvanced = !ctx.baseline.has(newest.sig);
    const controllerIdentityAdvanced = Boolean(
      newest.sig && ctx.controllerBaselineIds.size && !ctx.controllerBaselineIds.has(newest.sig)
    );
    const textChanged = baselineText !== undefined && newest.text !== baselineText;
    if (countAdvanced || identityAdvanced || controllerIdentityAdvanced || textChanged) return newest;
    return null;
  }

  async function emit(event) {
    try {
      await chrome.runtime.sendMessage({ type: "chat2api.event", event });
      return true;
    } catch (_) { return false; }
  }

  async function emitRecoveredText(ctx, requestId, text) {
    const previous = ctx.emittedText;
    if (text === previous) return false;
    if (text.startsWith(previous)) {
      const delta = text.slice(previous.length);
      if (delta) {
        state.deltas += 1;
        await emit({
          type: "chat.delta",
          request_id: requestId,
          delta,
          diagnostics: { response_stream_recovery: "dom-turn-v48" },
        });
      }
    } else {
      state.snapshots += 1;
      await emit({
        type: "chat.snapshot",
        request_id: requestId,
        text,
        diagnostics: { response_stream_recovery: "dom-turn-v48", response_stream_reset: true },
      });
    }
    ctx.emittedText = text;
    return true;
  }

  async function tick() {
    const active = globalThis.__CHAT2API_REQUEST_CONTENT_V5__?.active;
    const requestId = String(active?.requestId || "");
    if (!requestId) {
      state.request = null;
      return;
    }
    if (!state.request || state.request.requestId !== requestId) state.request = captureBaseline(active);
    const ctx = state.request;
    if (ctx.completed) return;

    const candidate = currentCandidate(ctx);
    if (!candidate) return;
    if (candidate.text !== ctx.lastText || candidate.sig !== ctx.lastSignature) {
      ctx.lastText = candidate.text;
      ctx.lastSignature = candidate.sig;
      ctx.changedAt = Date.now();
    }

    if (await emitRecoveredText(ctx, requestId, candidate.text)) {
      if (!ctx.diagnosticSent) {
        ctx.diagnosticSent = true;
        await emit({
          type: "chat.diagnostics",
          request_id: requestId,
          diagnostics: {
            response_stream_recovery: "dom-turn-v48",
            response_stream_first_capture_ms: Date.now() - ctx.startedAt,
            response_stream_baseline_assistants: ctx.baselineAssistantCount,
            response_stream_baseline_users: ctx.baselineUserCount,
          },
        });
      }
    }

    const stableMs = Date.now() - ctx.changedAt;
    const stopped = !stopVisible();
    const hasFinal = finalActions(candidate.turn);
    const completeNow = (stopped && stableMs >= 700) || (hasFinal && stableMs >= 700) || stableMs >= 9000;
    if (!completeNow) return;

    ctx.completed = true;
    state.completions += 1;
    await emit({
      type: "chat.completed",
      request_id: requestId,
      text: candidate.text,
      diagnostics: {
        response_stream_recovery: "dom-turn-v48",
        response_stream_completion_reason: stopped ? "generation-control-gone" : (hasFinal ? "final-actions-visible" : "stale-generation-control"),
        response_stream_stable_ms: stableMs,
      },
    });
  }

  state.timer = setInterval(() => tick().catch(() => {}), 100);
})();
