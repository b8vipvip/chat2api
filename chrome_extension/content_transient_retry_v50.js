(() => {
  const KEY = "__CHAT2API_TRANSIENT_RETRY_V50__";
  if (globalThis[KEY]) return;

  const REQUEST_KEY = "__CHAT2API_REQUEST_CONTENT_V5__";
  const RECOVERY_KEY = "__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__";
  const MAX_RETRIES = 2;
  const RETRY_COOLDOWN_MS = 900;
  const CHECK_INTERVAL_MS = 100;

  const state = {
    version: 50,
    request_id: "",
    attempts: 0,
    last_click_at: 0,
    timer: null,
    last_reason: "",
  };
  globalThis[KEY] = state;

  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  function visible(node) {
    if (!(node instanceof Element)) return false;
    try {
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
    } catch (_) { return false; }
  }

  const RETRY_LABEL = /^(重试|再试一次|重新尝试|retry|try again)$/i;
  const RETRYABLE_TEXT = /(消息发送超时|发送超时|请求超时|网络错误|网络连接错误|出了点问题|发生错误|message (?:send )?timed out|request timed out|network error|something went wrong|failed to send)/i;
  const NON_RETRYABLE_TEXT = /(上下文.{0,8}(过长|长度|上限)|context.{0,12}(length|limit|too long)|maximum context|conversation too long|登录|重新登录|sign in|log in|authentication|账号|账户|account|套餐|plan|额度|配额|quota|rate limit|usage limit|容量|capacity|plugin|connector|connected app|integration|插件|连接器|已连接应用|授权|authorize|reconnect|重新连接)/i;

  function label(node) {
    return normalize(`${node?.getAttribute?.("aria-label") || ""} ${node?.title || ""} ${node?.innerText || node?.textContent || ""}`);
  }

  function retrySurface() {
    const buttons = [...document.querySelectorAll("button,[role='button']")].filter(visible);
    for (const button of buttons) {
      const buttonLabel = label(button);
      if (!RETRY_LABEL.test(buttonLabel)) continue;
      const container = button.closest("[role='alert'],[data-testid],article,section,div") || button.parentElement;
      const text = normalize(container?.innerText || container?.textContent || buttonLabel).slice(0, 800);
      const pageTail = normalize(document.body?.innerText || "").slice(-2400);
      const evidence = RETRYABLE_TEXT.test(text) ? text : (RETRYABLE_TEXT.test(pageTail) ? pageTail : "");
      if (!evidence || NON_RETRYABLE_TEXT.test(evidence)) continue;
      return { button, reason: evidence.slice(0, 240), label: buttonLabel.slice(0, 80) };
    }
    return null;
  }

  async function emit(requestId, diagnostics) {
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: { type: "chat.diagnostics", request_id: requestId, diagnostics },
      });
    } catch (_) {}
  }

  function resetRecoveryClock(requestId) {
    const recovery = globalThis[RECOVERY_KEY];
    const ctx = recovery?.request;
    if (!ctx || String(ctx.requestId || "") !== requestId) return;
    const now = Date.now();
    ctx.generationSeenAt = now;
    ctx.lastMeaningfulProgressAt = now;
    ctx.lastProbeDiagnosticAt = 0;
    ctx.lastUiChangedAt = now;
    ctx.lastUiFingerprint = "";
    ctx.failed = false;
  }

  async function tick() {
    const active = globalThis[REQUEST_KEY]?.active;
    const requestId = String(active?.requestId || "");
    if (!requestId) {
      state.request_id = "";
      state.attempts = 0;
      state.last_click_at = 0;
      state.last_reason = "";
      return;
    }
    if (state.request_id !== requestId) {
      state.request_id = requestId;
      state.attempts = 0;
      state.last_click_at = 0;
      state.last_reason = "";
    }

    const surface = retrySurface();
    if (!surface) return;
    if (Date.now() - state.last_click_at < RETRY_COOLDOWN_MS) return;
    if (state.attempts >= MAX_RETRIES) {
      if (state.last_reason !== "exhausted") {
        state.last_reason = "exhausted";
        await emit(requestId, {
          transient_retry: "chatgpt-ui-v50",
          transient_retry_exhausted: true,
          transient_retry_attempts: state.attempts,
          transient_retry_reason: surface.reason,
        });
      }
      return;
    }

    state.attempts += 1;
    state.last_click_at = Date.now();
    state.last_reason = surface.reason;
    resetRecoveryClock(requestId);
    await emit(requestId, {
      transient_retry: "chatgpt-ui-v50",
      transient_retry_attempt: state.attempts,
      transient_retry_max_attempts: MAX_RETRIES,
      transient_retry_reason: surface.reason,
      transient_retry_button_label: surface.label,
      transient_retry_same_request: true,
    });
    try {
      surface.button.scrollIntoView?.({ block: "nearest", inline: "nearest" });
      surface.button.click();
    } catch (_) {}
  }

  state.retrySurface = retrySurface;
  state.resetRecoveryClock = resetRecoveryClock;
  state.constants = Object.freeze({ max_retries: MAX_RETRIES, retry_cooldown_ms: RETRY_COOLDOWN_MS });
  state.timer = setInterval(() => tick().catch(() => {}), CHECK_INTERVAL_MS);
})();
