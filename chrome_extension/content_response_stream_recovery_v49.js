(() => {
  const KEY = "__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__";
  if (globalThis[KEY]) return;

  const REQUEST_KEY = "__CHAT2API_REQUEST_CONTENT_V5__";
  const STALL_KEY = "__CHAT2API_REQUEST_STALL_GUARD_V34__";
  const DIAGNOSTIC_AFTER_MS = 15000;
  const IDLE_STUCK_MS = 25000;
  const NON_IDLE_STUCK_MS = 45000;
  const VISIBLE_GENERATION_STUCK_MS = 120000;
  const OBSERVER_GRACE_MS = 180000;

  const state = {
    version: 49,
    owner_revision: 53,
    owner: "response-stream-v49-single-owner",
    request: null,
    timer: null,
    snapshots: 0,
    completions: 0,
    failures: 0,
    semantic_shells_filtered: 0,
  };
  globalThis[KEY] = state;

  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  const ROLE_ONLY = /^(?:chatgpt|assistant|ai)\s*(?:said|says|回复|回答|说)\s*[:：]?\s*$/i;
  const ROLE_PREFIX = /^(?:chatgpt|assistant|ai)\s*(?:said|says|回复|回答|说)\s*[:：]\s*/i;

  function sanitizeAssistantText(value) {
    const text = normalize(value);
    if (!text) return { text: "", filtered: false };
    if (ROLE_ONLY.test(text)) {
      state.semantic_shells_filtered += 1;
      return { text: "", filtered: true };
    }
    const stripped = text.replace(ROLE_PREFIX, "").trim();
    if (stripped !== text) state.semantic_shells_filtered += 1;
    return { text: stripped, filtered: stripped !== text };
  }

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

  const isUserTurn = turn => turn?.getAttribute?.("data-message-author-role") === "user"
    || Boolean(turn?.querySelector?.("[data-message-author-role='user']"));
  const isAssistantTurn = turn => turn?.getAttribute?.("data-message-author-role") === "assistant"
    || Boolean(turn?.querySelector?.("[data-message-author-role='assistant']"));

  function turnText(turn) {
    if (!turn) return "";
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
        const result = sanitizeAssistantText(candidates[index].innerText || candidates[index].textContent || "");
        if (result.text) return result.text;
      }
    }

    // Current ChatGPT DOM can expose the accessibility role heading before it
    // mounts a stable markdown body. Keep the broad v49 fallback that worked in
    // production, but strip action chrome and role-only leaf nodes first. This
    // preserves DOM-shape tolerance without ever returning "ChatGPT said:" as
    // assistant content.
    let clone = null;
    try { clone = turn.cloneNode(true); } catch (_) {}
    if (clone?.querySelectorAll) {
      clone.querySelectorAll("button,svg,nav,footer,[aria-hidden='true'],[data-testid*='copy'],[data-testid*='feedback'],[data-testid*='action']")
        .forEach(node => node.remove());
      for (const node of [...clone.querySelectorAll("*")].reverse()) {
        if (node.children?.length) continue;
        if (ROLE_ONLY.test(normalize(node.textContent || ""))) node.remove();
      }
      const result = sanitizeAssistantText(clone.innerText || clone.textContent || "");
      if (result.text) return result.text;
    }

    return sanitizeAssistantText(turn.innerText || turn.textContent || "").text;
  }

  function transient(text) {
    const value = normalize(text).replace(/[.。…·:：]+$/g, "").toLowerCase();
    return /^(正在)?(思考|分析|推理|生成|处理|搜索|浏览)(中)?$/.test(value)
      || /^(thinking|analyzing|reasoning|generating|working|searching|browsing)( now)?$/.test(value);
  }

  function integrationSurface(turn) {
    const text = normalize(turn?.innerText || turn?.textContent || "");
    if (!text) return false;
    const positive = [...turn.querySelectorAll("button,[role='button']")]
      .filter(visible)
      .some(button => /^(reconnect|connect|enable|authorize|install|重新连接|连接|启用|授权|安装)(?:\s|$)/i.test(
        normalize(button.innerText || button.textContent || button.getAttribute("aria-label") || "")
      ));
    const context = /(plugin|connector|connected app|integration|connection (?:has )?expired|use this connection|插件|连接器|已连接应用|集成|连接已过期|使用该连接|才能在此次请求中使用该连接)/i.test(text);
    return positive && context;
  }

  function stopVisible() {
    return [...document.querySelectorAll("button")].some(button => {
      if (!visible(button)) return false;
      const label = normalize(`${button.getAttribute("aria-label") || ""} ${button.textContent || ""}`);
      return /stop streaming|stop generating|停止生成|停止回答/i.test(label) && !button.disabled;
    });
  }

  function sendReady() {
    const selectors = [
      "button[data-testid='send-button']",
      "button[aria-label='Send prompt']",
      "button[aria-label*='发送提示']",
      "button[aria-label*='发送消息']",
    ];
    for (const selector of selectors) {
      for (const button of document.querySelectorAll(selector)) {
        if (!visible(button)) continue;
        return !button.disabled && button.getAttribute?.("aria-disabled") !== "true";
      }
    }
    return false;
  }

  const ACTIVE_STATUS_PATTERN = /(^|\s)(thinking|analyzing|reasoning|generating|searching|browsing|working)(\s|$)|正在(思考|分析|推理|生成|搜索|浏览|处理)/i;

  function activeStatusText() {
    const nodes = [...document.querySelectorAll("[role='status'],[aria-live='polite'],[aria-live='assertive']")].filter(visible);
    for (const node of nodes) {
      const text = normalize(node.innerText || node.textContent || "");
      if (text && ACTIVE_STATUS_PATTERN.test(text)) return text.slice(0, 160);
    }
    return "";
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
    return String(
      turn?.getAttribute?.("data-message-id")
      || turn?.dataset?.messageId
      || turn?.getAttribute?.("data-testid")
      || `turn:${index}`
    );
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
      requestId: String(active?.requestId || ""),
      startedAt: Date.now(),
      generationSeenAt: 0,
      lastMeaningfulProgressAt: 0,
      baselineAssistantCount: Math.max(assistantCount, controllerCount),
      baselineUserCount: userCount,
      baseline,
      controllerBaselineIds: active?.baselineIds instanceof Set ? new Set(active.baselineIds) : new Set(),
      lastText: "",
      lastSignature: "",
      changedAt: 0,
      emittedText: "",
      completed: false,
      failed: false,
      diagnosticSent: false,
      lastProbeDiagnosticAt: 0,
      probeSequence: 0,
      lastUiFingerprint: "",
      lastUiChangedAt: 0,
      controllerDetachedAt: 0,
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

  async function emitRecoveredSnapshot(ctx, requestId, text) {
    if (text === ctx.emittedText) return false;
    ctx.emittedText = text;
    ctx.lastMeaningfulProgressAt = Date.now();
    state.snapshots += 1;
    await emit({
      type: "chat.snapshot",
      request_id: requestId,
      text,
      diagnostics: {
        response_stream_recovery: "dom-turn-v49-single-owner-v53",
        response_semantic_recovery: "role-shell-filter-integrated-v53",
        assistant_ui_boilerplate_filtered: true,
        page_progress_probe: "page-progress-v49",
      },
    });
    return true;
  }

  function pageState(candidate) {
    const stopped = !stopVisible();
    const ready = sendReady();
    const status = activeStatusText();
    const text = String(candidate?.text || "");
    const sig = String(candidate?.sig || "");
    const tail = text.slice(-80);
    return {
      stop_visible: !stopped,
      send_ready: ready,
      status_active: Boolean(status),
      status_text: status,
      assistant_candidate_chars: text.length,
      assistant_candidate_identity: sig.slice(0, 160),
      fingerprint: `${stopped ? 0 : 1}|${ready ? 1 : 0}|${status ? 1 : 0}|${sig}|${text.length}|${tail}`,
    };
  }

  function stuckThreshold(snapshot) {
    if (snapshot.stop_visible) return VISIBLE_GENERATION_STUCK_MS;
    if (snapshot.send_ready && !snapshot.status_active) return IDLE_STUCK_MS;
    return NON_IDLE_STUCK_MS;
  }

  async function emitProbe(ctx, requestId, snapshot, noProgressMs, action = "observe") {
    ctx.probeSequence += 1;
    ctx.lastProbeDiagnosticAt = Date.now();
    await emit({
      type: "chat.diagnostics",
      request_id: requestId,
      diagnostics: {
        response_stream_recovery: "dom-turn-v49-single-owner-v53",
        response_semantic_recovery: "role-shell-filter-integrated-v53",
        response_observer_controller_detached: Boolean(ctx.controllerDetachedAt),
        page_progress_probe: "page-progress-v49",
        page_probe_sequence: ctx.probeSequence,
        page_probe_no_response_ms: noProgressMs,
        page_probe_stop_visible: snapshot.stop_visible,
        page_probe_send_ready: snapshot.send_ready,
        page_probe_status_active: snapshot.status_active,
        page_probe_status_text: snapshot.status_text,
        page_probe_assistant_candidate_chars: snapshot.assistant_candidate_chars,
        page_probe_assistant_candidate_identity: snapshot.assistant_candidate_identity,
        page_probe_ui_changed_ms: ctx.lastUiChangedAt ? Date.now() - ctx.lastUiChangedAt : null,
        page_probe_action: action,
      },
    });
  }

  async function failStuck(ctx, active, snapshot, noProgressMs, thresholdMs) {
    if (ctx.failed || ctx.completed) return;
    ctx.failed = true;
    state.failures += 1;
    await emitProbe(ctx, ctx.requestId, snapshot, noProgressMs, "fail-chatgpt-ui-stuck");
    const mode = snapshot.stop_visible
      ? "generation control remained visible"
      : (snapshot.send_ready && !snapshot.status_active ? "page returned idle without a response" : "page remained in a non-idle intermediate state");
    await emit({
      type: "chat.error",
      request_id: ctx.requestId,
      error: `ChatGPT UI made no observable response progress for ${Math.round(thresholdMs / 1000)}s after generation started (${mode})`,
      diagnostics: {
        response_stream_recovery: "dom-turn-v49-single-owner-v53",
        response_semantic_recovery: "role-shell-filter-integrated-v53",
        page_progress_probe: "page-progress-v49",
        page_probe_failure: "chatgpt-ui-stuck",
      },
    });
    if (active) active.cancelled = true;
  }

  async function tick() {
    const active = globalThis[REQUEST_KEY]?.active;
    const activeId = String(active?.requestId || "");
    if (activeId && (!state.request || state.request.requestId !== activeId || state.request.completed || state.request.failed)) {
      state.request = captureBaseline(active);
    }

    const ctx = state.request;
    if (!ctx || ctx.completed || ctx.failed) return;
    const now = Date.now();

    // request-v5 is allowed to settle its local controller independently. The
    // response observer must not disappear with it: the server still owns this
    // request until a meaningful snapshot/terminal event is observed. This was
    // the v0.8.9 regression that turned a DOM miss into a blind 150s timeout.
    if (!activeId) {
      if (!ctx.controllerDetachedAt) ctx.controllerDetachedAt = now;
      if (now - ctx.startedAt > OBSERVER_GRACE_MS) {
        state.request = null;
        return;
      }
    }

    const track = globalThis[STALL_KEY]?.track;
    if (
      String(track?.requestId || "") === ctx.requestId
      && track?.sawGenerating
      && !ctx.generationSeenAt
    ) {
      ctx.generationSeenAt = Number(track.submittedAt || 0) || now;
      ctx.lastMeaningfulProgressAt = ctx.generationSeenAt;
    }

    const candidate = currentCandidate(ctx);
    if (candidate && (candidate.text !== ctx.lastText || candidate.sig !== ctx.lastSignature)) {
      ctx.lastText = candidate.text;
      ctx.lastSignature = candidate.sig;
      ctx.changedAt = now;
      ctx.lastMeaningfulProgressAt = now;
    }

    const snapshot = pageState(candidate);
    if (snapshot.fingerprint !== ctx.lastUiFingerprint) {
      ctx.lastUiFingerprint = snapshot.fingerprint;
      ctx.lastUiChangedAt = now;
    }

    if (candidate) {
      if (await emitRecoveredSnapshot(ctx, ctx.requestId, candidate.text)) {
        if (!ctx.diagnosticSent) {
          ctx.diagnosticSent = true;
          await emit({
            type: "chat.diagnostics",
            request_id: ctx.requestId,
            diagnostics: {
              response_stream_recovery: "dom-turn-v49-single-owner-v53",
              response_semantic_recovery: "role-shell-filter-integrated-v53",
              response_stream_first_capture_ms: now - ctx.startedAt,
              response_stream_baseline_assistants: ctx.baselineAssistantCount,
              response_stream_baseline_users: ctx.baselineUserCount,
              response_observer_controller_detached: Boolean(ctx.controllerDetachedAt),
              page_progress_probe: "page-progress-v49",
            },
          });
        }
      }

      const stableMs = now - ctx.changedAt;
      const hasFinal = finalActions(candidate.turn);
      const completeNow = (hasFinal && stableMs >= 700)
        || (!snapshot.stop_visible && stableMs >= 1800)
        || stableMs >= 9000;
      if (!completeNow) return;

      ctx.completed = true;
      state.completions += 1;
      await emit({
        type: "chat.completed",
        request_id: ctx.requestId,
        text: candidate.text,
        diagnostics: {
          response_stream_recovery: "dom-turn-v49-single-owner-v53",
          response_semantic_recovery: "role-shell-filter-integrated-v53",
          response_stream_completion_reason: hasFinal
            ? "final-actions-visible"
            : (!snapshot.stop_visible ? "generation-control-gone" : "stale-generation-control"),
          response_stream_stable_ms: stableMs,
          response_observer_controller_detached: Boolean(ctx.controllerDetachedAt),
          page_progress_probe: "page-progress-v49",
        },
      });
      return;
    }

    if (!ctx.generationSeenAt) return;
    const noProgressMs = Math.max(0, now - (ctx.lastMeaningfulProgressAt || ctx.generationSeenAt));
    if (
      noProgressMs >= DIAGNOSTIC_AFTER_MS
      && now - ctx.lastProbeDiagnosticAt >= DIAGNOSTIC_AFTER_MS
    ) {
      await emitProbe(ctx, ctx.requestId, snapshot, noProgressMs);
    }

    const thresholdMs = stuckThreshold(snapshot);
    if (noProgressMs >= thresholdMs) {
      await failStuck(ctx, activeId === ctx.requestId ? active : null, snapshot, noProgressMs, thresholdMs);
    }
  }

  state.constants = {
    diagnostic_after_ms: DIAGNOSTIC_AFTER_MS,
    idle_stuck_ms: IDLE_STUCK_MS,
    non_idle_stuck_ms: NON_IDLE_STUCK_MS,
    visible_generation_stuck_ms: VISIBLE_GENERATION_STUCK_MS,
    observer_grace_ms: OBSERVER_GRACE_MS,
  };
  state.sanitizeAssistantText = sanitizeAssistantText;
  state.extractTurnText = turnText;
  state.pageState = pageState;
  state.stuckThreshold = stuckThreshold;
  state.tick = tick;
  state.timer = setInterval(() => tick().catch(() => {}), 100);
})();
