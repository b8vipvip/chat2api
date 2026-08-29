(() => {
  const KEY = "__CHAT2API_RESPONSE_SEMANTIC_RECOVERY_V51__";
  if (globalThis[KEY]) return;

  const REQUEST_KEY = "__CHAT2API_REQUEST_CONTENT_V5__";
  const STALL_KEY = "__CHAT2API_REQUEST_STALL_GUARD_V34__";
  const oldRecovery = globalThis.__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__;
  if (oldRecovery?.timer) {
    try { clearInterval(oldRecovery.timer); } catch (_) {}
    oldRecovery.timer = null;
    oldRecovery.superseded_by = "response-semantic-v51";
  }

  const state = {
    version: 51,
    request: null,
    timer: null,
    snapshots: 0,
    completions: 0,
    filtered_shells: 0,
  };
  globalThis[KEY] = state;

  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  const ROLE_ONLY = /^(?:chatgpt|assistant|ai)\s*(?:said|says|回复|回答|说)\s*[:：]?\s*$/i;
  const ROLE_PREFIX = /^(?:chatgpt|assistant|ai)\s*(?:said|says|回复|回答|说)\s*[:：]\s*/i;
  const TRANSIENT = /^(?:(?:正在)?(?:思考|分析|推理|生成|处理|搜索|浏览)(?:中)?|(?:thinking|analyzing|reasoning|generating|working|searching|browsing)(?: now)?)$/i;

  const visible = node => {
    if (!(node instanceof Element)) return false;
    try {
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
    } catch (_) { return false; }
  };

  function sanitize(value) {
    const text = normalize(value);
    if (!text) return {text: "", filtered: false};
    if (ROLE_ONLY.test(text)) return {text: "", filtered: true};
    const stripped = text.replace(ROLE_PREFIX, "").trim();
    return {text: stripped, filtered: stripped !== text};
  }

  function turns() {
    const nodes = [];
    const seen = new Set();
    for (const selector of [
      "article[data-testid^='conversation-turn']",
      "[data-testid^='conversation-turn']",
      "article[data-message-id]",
    ]) {
      for (const node of document.querySelectorAll(selector)) {
        if (seen.has(node)) continue;
        seen.add(node);
        nodes.push(node);
      }
    }
    return nodes;
  }

  const isUser = turn => turn?.getAttribute?.("data-message-author-role") === "user"
    || Boolean(turn?.querySelector?.("[data-message-author-role='user']"));
  const isAssistant = turn => turn?.getAttribute?.("data-message-author-role") === "assistant"
    || Boolean(turn?.querySelector?.("[data-message-author-role='assistant']"));

  function signature(turn, index) {
    return String(
      turn?.getAttribute?.("data-message-id")
      || turn?.dataset?.messageId
      || turn?.getAttribute?.("data-testid")
      || `turn:${index}`
    );
  }

  function bodyText(turn) {
    if (!turn) return {text: "", filtered: false, source: "none"};
    for (const selector of [
      "[data-message-author-role='assistant'] [data-message-content]",
      "[data-message-author-role='assistant'] .markdown",
      "[data-message-author-role='assistant'] [class*='markdown']",
      "[data-message-author-role='assistant'] [class*='prose']",
      "[data-message-content]",
      ".markdown",
      "[class*='markdown']",
      "[class*='prose']",
    ]) {
      const candidates = [...turn.querySelectorAll(selector)].filter(visible);
      for (let index = candidates.length - 1; index >= 0; index -= 1) {
        const result = sanitize(candidates[index].innerText || candidates[index].textContent || "");
        if (result.filtered && !result.text) state.filtered_shells += 1;
        if (result.text && !TRANSIENT.test(result.text)) return {...result, source: selector};
      }
    }

    const clone = turn.cloneNode(true);
    clone.querySelectorAll("button,svg,nav,footer,[aria-hidden='true'],[data-testid*='copy'],[data-testid*='feedback'],[data-testid*='action']").forEach(node => node.remove());
    for (const node of [...clone.querySelectorAll("*")].reverse()) {
      if (node.children.length) continue;
      if (ROLE_ONLY.test(normalize(node.textContent || ""))) node.remove();
    }
    const result = sanitize(clone.innerText || clone.textContent || "");
    if (result.filtered && !result.text) state.filtered_shells += 1;
    if (!result.text || TRANSIENT.test(result.text)) return {text: "", filtered: result.filtered, source: "turn-fallback"};
    return {...result, source: "turn-fallback"};
  }

  function capture(active) {
    const all = turns();
    const baseline = new Map();
    let assistants = 0;
    let users = 0;
    all.forEach((turn, index) => {
      if (isUser(turn)) users += 1;
      if (!isAssistant(turn)) return;
      assistants += 1;
      baseline.set(signature(turn, index), bodyText(turn).text);
    });
    return {
      requestId: String(active?.requestId || ""),
      startedAt: Date.now(),
      baseline,
      baselineAssistantCount: Math.max(assistants, Number(active?.baselineCount || 0)),
      baselineUserCount: users,
      controllerBaselineIds: active?.baselineIds instanceof Set ? new Set(active.baselineIds) : new Set(),
      generationSeenAt: 0,
      lastText: "",
      lastSignature: "",
      changedAt: 0,
      emittedText: "",
      completed: false,
    };
  }

  function candidate(ctx) {
    const all = turns();
    const userIndexes = [];
    all.forEach((turn, index) => { if (isUser(turn)) userIndexes.push(index); });
    const latestUser = userIndexes.length ? userIndexes[userIndexes.length - 1] : -1;
    const userAdvanced = userIndexes.length > ctx.baselineUserCount;
    const assistants = [];
    all.forEach((turn, index) => {
      if (!isAssistant(turn)) return;
      const body = bodyText(turn);
      if (!body.text) return;
      assistants.push({turn, index, text: body.text, source: body.source, filtered: body.filtered, sig: signature(turn, index)});
    });
    if (!assistants.length) return null;
    if (userAdvanced) {
      const afterUser = assistants.filter(item => item.index > latestUser);
      if (afterUser.length) return afterUser[afterUser.length - 1];
    }
    const newest = assistants[assistants.length - 1];
    const baselineText = ctx.baseline.get(newest.sig);
    const advanced = assistants.length > ctx.baselineAssistantCount
      || !ctx.baseline.has(newest.sig)
      || Boolean(newest.sig && ctx.controllerBaselineIds.size && !ctx.controllerBaselineIds.has(newest.sig))
      || (baselineText !== undefined && newest.text !== baselineText);
    return advanced ? newest : null;
  }

  function generating() {
    return [...document.querySelectorAll("button")].some(button => {
      if (!visible(button) || button.disabled) return false;
      const label = normalize(`${button.getAttribute("aria-label") || ""} ${button.textContent || ""}`);
      return /stop streaming|stop generating|停止生成|停止回答/i.test(label);
    });
  }

  function finalActions(turn) {
    return Boolean(turn && [...turn.querySelectorAll("button")].some(button => {
      if (!visible(button)) return false;
      const label = normalize(`${button.getAttribute("aria-label") || ""} ${button.textContent || ""}`);
      return /good response|bad response|regenerate|copy|赞|踩|重新生成|复制/i.test(label);
    }));
  }

  async function emit(event) {
    try { await chrome.runtime.sendMessage({type: "chat2api.event", event}); return true; }
    catch (_) { return false; }
  }

  async function tick() {
    const active = globalThis[REQUEST_KEY]?.active;
    const activeId = String(active?.requestId || "");
    if (activeId && (!state.request || state.request.requestId !== activeId || state.request.completed)) {
      state.request = capture(active);
    }
    const ctx = state.request;
    if (!ctx || ctx.completed) return;

    const now = Date.now();
    const track = globalThis[STALL_KEY]?.track;
    if (!ctx.generationSeenAt && String(track?.requestId || "") === ctx.requestId && track?.sawGenerating) {
      ctx.generationSeenAt = Number(track.submittedAt || 0) || now;
    }

    // Keep observing for a bounded grace even if request-v5 cleared its local
    // controller after incorrectly seeing only the role shell. The server still
    // owns the request until it receives a meaningful terminal event.
    if (!activeId && now - ctx.startedAt > 180000) {
      state.request = null;
      return;
    }

    const item = candidate(ctx);
    if (!item) return;
    if (item.text !== ctx.lastText || item.sig !== ctx.lastSignature) {
      ctx.lastText = item.text;
      ctx.lastSignature = item.sig;
      ctx.changedAt = now;
    }

    if (item.text !== ctx.emittedText) {
      ctx.emittedText = item.text;
      state.snapshots += 1;
      await emit({
        type: "chat.snapshot",
        request_id: ctx.requestId,
        text: item.text,
        diagnostics: {
          response_stream_recovery: "assistant-body-v51",
          response_semantic_recovery: "assistant-body-v51",
          assistant_body_source: item.source,
          assistant_ui_boilerplate_filtered: Boolean(item.filtered),
        },
      });
    }

    const stableMs = now - ctx.changedAt;
    const hasFinal = finalActions(item.turn);
    if ((generating() || stableMs < (hasFinal ? 700 : 1800)) && stableMs < 9000) return;

    ctx.completed = true;
    state.completions += 1;
    await emit({
      type: "chat.completed",
      request_id: ctx.requestId,
      text: item.text,
      diagnostics: {
        response_stream_recovery: "assistant-body-v51",
        response_semantic_recovery: "assistant-body-v51",
        assistant_body_source: item.source,
        assistant_ui_boilerplate_filtered: Boolean(item.filtered),
        response_stream_stable_ms: stableMs,
      },
    });
  }

  state.sanitize = sanitize;
  state.bodyText = bodyText;
  state.tick = tick;
  state.timer = setInterval(() => tick().catch(() => {}), 100);
})();
