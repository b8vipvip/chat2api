(() => {
  const KEY = "__CHAT2API_REQUEST_STALL_GUARD_V34__";
  if (globalThis[KEY]) return;

  const REQUEST_KEY = "__CHAT2API_REQUEST_CONTENT_V5__";
  const GENERATION_STOP_GRACE_MS = 5000;
  const GENERATION_START_TIMEOUT_MS = 60000;
  const POLL_MS = 250;

  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  const ERROR_PATTERN = /(出了点问题|发生错误|生成回复时出错|无法生成|生成失败|网络错误|请重试|稍后再试|请求过多|使用上限|消息上限|免费.{0,20}(?:限制|上限)|已达到.{0,30}(?:限制|上限)|达到.{0,30}(?:限制|上限)|something went wrong|there was an error generating|network error|please try again|try again later|unable to generate|failed to generate|too many requests|rate limit|usage limit|message limit|you(?:'|’)?ve reached|you have reached|free plan limit)/i;

  function visible(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect?.() || { width: 0, height: 0 };
    const style = typeof getComputedStyle === "function" ? getComputedStyle(element) : { display: "", visibility: "" };
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function composerText() {
    for (const selector of [
      "#prompt-textarea",
      "textarea[placeholder]",
      "div[contenteditable='true'][data-lexical-editor='true']",
      "div[contenteditable='true'].ProseMirror",
      "[contenteditable='true']",
    ]) {
      const element = [...document.querySelectorAll(selector)].find(visible);
      if (!element) continue;
      return normalize("value" in element ? element.value : (element.innerText || element.textContent || ""));
    }
    return "";
  }

  function generating() {
    for (const selector of [
      "button[data-testid='stop-button']",
      "button[aria-label='Stop streaming']",
      "button[aria-label='Stop generating']",
      "button[aria-label*='停止生成']",
    ]) {
      if ([...document.querySelectorAll(selector)].some(item => visible(item) && !item.disabled)) return true;
    }
    return false;
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
    const turn = node?.closest?.("[data-message-id], article[id], article[data-testid]");
    return node?.getAttribute?.("data-message-id") || turn?.getAttribute?.("data-message-id") || turn?.id || turn?.getAttribute?.("data-testid") || "";
  }

  function transientText(value) {
    const text = normalize(value).replace(/[.。…·:：]+$/g, "").trim().toLowerCase();
    return /^(正在)?(思考|分析|推理|生成|处理|搜索|浏览)(中)?$/.test(text)
      || /^(thinking|analyzing|reasoning|generating|working|searching|browsing)( now)?$/.test(text);
  }

  function nodeText(node) {
    const text = normalize(node?.innerText || node?.textContent || "");
    return transientText(text) ? "" : text;
  }

  function newAssistantText(active) {
    if (!active) return "";
    const nodes = assistantNodes();
    const latest = nodes[nodes.length - 1];
    if (!latest) return "";
    const identity = nodeIdentity(latest);
    const baselineIds = active.baselineIds instanceof Set ? active.baselineIds : new Set();
    const isNew = nodes.length > Number(active.baselineCount || 0)
      || Boolean(identity && !baselineIds.has(identity))
      || Boolean(identity && active.baselineIdentity && identity !== active.baselineIdentity);
    return isNew ? nodeText(latest) : "";
  }

  function visibleErrorText(active) {
    const nodes = [...document.querySelectorAll("[role='alert'],[data-sonner-toast],[data-toast],[class*='toast']")]
      .filter(visible)
      .slice(-10);
    for (const node of nodes) {
      const text = normalize(node.innerText || node.textContent || "");
      if (ERROR_PATTERN.test(text)) return text.slice(0, 500);
    }
    const assistant = newAssistantText(active);
    return assistant && ERROR_PATTERN.test(assistant) ? assistant.slice(0, 500) : "";
  }

  function newTrack(requestId) {
    return {
      requestId: String(requestId || ""),
      sawComposerText: false,
      submittedAt: 0,
      sawGenerating: false,
      generationStoppedAt: 0,
      sawResponseText: false,
      failed: false,
    };
  }

  function evaluate(track, snapshot, now = Date.now()) {
    if (!track || track.failed) return null;
    const composerHasText = Boolean(snapshot?.composer_has_text);
    const isGenerating = Boolean(snapshot?.generating);
    const responseText = normalize(snapshot?.new_assistant_text || "");
    const errorText = normalize(snapshot?.error_text || "");

    if (composerHasText) track.sawComposerText = true;
    if (track.sawComposerText && !composerHasText && !track.submittedAt) track.submittedAt = now;
    if (isGenerating) {
      track.sawGenerating = true;
      track.submittedAt ||= now;
      track.generationStoppedAt = 0;
    }
    if (responseText) {
      track.sawResponseText = true;
      track.generationStoppedAt = 0;
    }

    const afterSubmission = Boolean(track.submittedAt || track.sawGenerating);
    if (afterSubmission && errorText && ERROR_PATTERN.test(errorText)) {
      return { code: "chatgpt-ui-error", message: `ChatGPT response UI error: ${errorText.slice(0, 500)}` };
    }

    if (track.sawGenerating && !isGenerating && !track.sawResponseText) {
      track.generationStoppedAt ||= now;
      if (now - track.generationStoppedAt >= GENERATION_STOP_GRACE_MS) {
        return {
          code: "generation-stopped-without-response",
          message: "ChatGPT generation stopped before any assistant response text was captured",
        };
      }
    }

    if (
      track.submittedAt
      && !track.sawGenerating
      && !track.sawResponseText
      && now - track.submittedAt >= GENERATION_START_TIMEOUT_MS
    ) {
      return {
        code: "generation-did-not-start",
        message: `ChatGPT accepted the prompt but response generation did not start within ${Math.round(GENERATION_START_TIMEOUT_MS / 1000)}s`,
      };
    }
    return null;
  }

  async function emit(event) {
    try { await chrome.runtime.sendMessage({ type: "chat2api.event", event }); } catch (_) {}
  }

  async function fail(active, track, failure) {
    if (!active || !track || track.failed || active.__chat2apiStallGuardFailed) return;
    track.failed = true;
    active.__chat2apiStallGuardFailed = true;
    await emit({
      type: "chat.diagnostics",
      request_id: active.requestId,
      diagnostics: {
        request_stall_guard: "content-v34",
        request_stall_reason: failure.code,
        generation_seen: Boolean(track.sawGenerating),
        response_text_seen: Boolean(track.sawResponseText),
      },
    });
    await emit({ type: "chat.error", request_id: active.requestId, error: failure.message });
    active.cancelled = true;
  }

  const state = {
    track: null,
    newTrack,
    evaluate,
    matchesError: text => ERROR_PATTERN.test(normalize(text)),
    constants: {
      generation_stop_grace_ms: GENERATION_STOP_GRACE_MS,
      generation_start_timeout_ms: GENERATION_START_TIMEOUT_MS,
    },
  };
  globalThis[KEY] = state;

  async function tick() {
    const requestState = globalThis[REQUEST_KEY];
    const active = requestState?.active;
    if (!active?.requestId) {
      state.track = null;
      return;
    }
    if (!state.track || state.track.requestId !== String(active.requestId)) {
      state.track = newTrack(active.requestId);
    }
    const snapshot = {
      composer_has_text: Boolean(composerText()),
      generating: generating(),
      new_assistant_text: newAssistantText(active),
      error_text: visibleErrorText(active),
    };
    const failure = evaluate(state.track, snapshot, Date.now());
    if (failure) await fail(active, state.track, failure);
  }

  state.tick = tick;
  setInterval(() => tick().catch(() => {}), POLL_MS);
})();
