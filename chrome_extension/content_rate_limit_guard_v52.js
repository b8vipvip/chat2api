(() => {
  const KEY = "__CHAT2API_RATE_LIMIT_CONTENT_V52__";
  if (globalThis[KEY]) return;

  const STORAGE_KEY = "chatgptRateLimitGuardV52";
  const COOLDOWN_MS = 5 * 60 * 1000;
  // Keep this faster than the 250ms generic stall/error guard. A real account
  // limit must become a rate-limit terminal first, otherwise it is recorded as
  // a generic ChatGPT UI failure and the shared Worker cooldown never arms.
  const CHECK_MS = 180;
  const MATCHERS = [
    /请求过于频繁/i,
    /暂时限制.*访问对话记录/i,
    /请稍等几分钟后再重试/i,
    /你(?:已|已经)?达到.{0,30}(?:使用|消息|请求)?(?:限制|上限)/i,
    /too many requests/i,
    /requests? (?:are )?too frequent/i,
    /temporarily limited.*conversation history/i,
    /try again in a few minutes/i,
    /you(?:'|’)?ve hit your (?:current )?limit/i,
    /you have hit your (?:current )?limit/i,
    /you(?:'|’)?ve reached.{0,40}(?:usage|message|request)?\s*limit/i,
    /you have reached.{0,40}(?:usage|message|request)?\s*limit/i,
  ];
  // Assistant-turn fallback is intentionally narrower than MATCHERS. ChatGPT
  // sometimes renders the account-limit surface inside a fresh assistant shell
  // instead of a dialog/toast. Only strong account-limit wording is accepted
  // there so normal assistant prose mentioning "rate limit" is not misclassified.
  const HARD_LIMIT_MATCHERS = [
    /^you(?:'|’)?ve hit your (?:current )?limit(?:[.!,:;\s]|$)/i,
    /^you have hit your (?:current )?limit(?:[.!,:;\s]|$)/i,
    /^you(?:'|’)?ve reached.{0,40}(?:usage|message|request)?\s*limit(?:[.!,:;\s]|$)/i,
    /^you have reached.{0,40}(?:usage|message|request)?\s*limit(?:[.!,:;\s]|$)/i,
    /^你(?:已|已经)?达到.{0,30}(?:使用|消息|请求)?(?:限制|上限)/i,
  ];

  const state = {
    version: 52,
    detection_revision: 63,
    timer: null,
    observer: null,
    lastFingerprint: "",
    terminalRequestId: "",
  };
  globalThis[KEY] = state;

  const normalize = value => String(value || "").replace(/\s+/g, " ").trim();
  const matchesRateLimitText = value => {
    const text = normalize(value);
    return Boolean(text && MATCHERS.some(pattern => pattern.test(text)));
  };
  const matchesHardLimitText = value => {
    const text = normalize(value);
    return Boolean(text && HARD_LIMIT_MATCHERS.some(pattern => pattern.test(text)));
  };

  function visible(node) {
    if (!node) return false;
    const rect = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) > 0;
  }

  function activeRequest() {
    // request-v5 is the authoritative text controller in the current bundle.
    // Keep the base-controller fallback for partially upgraded tabs while the
    // extension autoreload mechanism converges them to the current runtime.
    return globalThis.__CHAT2API_REQUEST_CONTENT_V5__?.active || globalThis.__CHAT2API_CONTENT__?.active || null;
  }

  function latestAssistantLimitText() {
    // Do not scan historical assistant messages while idle; only a currently
    // owned request may use the assistant-shell compatibility path.
    if (!String(activeRequest()?.requestId || "")) return "";
    const nodes = [...document.querySelectorAll(
      "[data-message-author-role='assistant'], article[data-testid^='conversation-turn'] [data-message-author-role='assistant']"
    )].filter(visible);
    const latest = nodes[nodes.length - 1];
    if (!latest) return "";
    const text = normalize(latest.innerText || latest.textContent || "");
    return matchesHardLimitText(text) ? text.slice(0, 500) : "";
  }

  function rateLimitText() {
    const selectors = [
      "[role='dialog']",
      "[aria-modal='true']",
      "[role='alert']",
      "[data-sonner-toast]",
      "[data-toast]",
      "[class*='toast']",
    ];
    const nodes = [...document.querySelectorAll(selectors.join(","))].filter(visible).slice(-12);
    for (const node of nodes.reverse()) {
      const text = normalize(node.innerText || node.textContent || "");
      if (matchesRateLimitText(text)) return text.slice(0, 500);
    }
    return latestAssistantLimitText();
  }

  async function terminateActiveRequest(snapshot) {
    const active = activeRequest();
    const requestId = String(active?.requestId || "");
    if (!requestId || state.terminalRequestId === requestId) return false;
    state.terminalRequestId = requestId;
    const seconds = Math.max(1, Math.ceil(Math.max(0, Number(snapshot?.until_ms || 0) - Date.now()) / 1000));
    const error = `ChatGPT is temporarily rate limited; request dispatch paused for ${seconds}s after a visible ChatGPT account-limit response`;

    // Send the terminal browser error before cancelling the local monitor so
    // the server receives the rate-limit reason first and can enter its shared
    // Worker cooldown instead of recording a generic UI error/300s timeout.
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "chat.error",
          request_id: requestId,
          error,
          diagnostics: {
            chatgpt_rate_limit_detected: true,
            chatgpt_rate_limit_detection_revision: 63,
            chatgpt_rate_limit_source: String(snapshot?.source || "visible-chatgpt-rate-limit-surface-v63"),
            chatgpt_rate_limit_retry_after_seconds: seconds,
            chatgpt_rate_limit_text: String(snapshot?.text || "").slice(0, 240),
          },
        },
      });
    } catch (_) {
      // Even if the Bridge socket is momentarily unavailable, stop the local
      // request monitor; transport recovery will surface the disconnect rather
      // than leaving this request stuck until its absolute timeout.
    } finally {
      active.rateLimitTerminalError = error;
      active.cancelled = true;
    }
    return true;
  }

  async function publish(text) {
    const now = Date.now();
    const stored = await chrome.storage.local.get({ [STORAGE_KEY]: null }).catch(() => ({}));
    const current = stored?.[STORAGE_KEY] || {};
    const until = Math.max(Number(current.until_ms || 0), now + COOLDOWN_MS);
    const next = {
      version: 52,
      detection_revision: 63,
      active: true,
      detected_at_ms: now,
      until_ms: until,
      url: location.href,
      title: document.title,
      text,
      source: "visible-chatgpt-rate-limit-surface-v63",
    };
    const fingerprint = `${text}|${location.href}|${until}`;
    if (fingerprint !== state.lastFingerprint) {
      state.lastFingerprint = fingerprint;
      await chrome.storage.local.set({ [STORAGE_KEY]: next }).catch(() => {});
      try {
        await chrome.runtime.sendMessage({
          type: "chat2api.log.append",
          entry: {
            component: "page",
            action: "chatgpt-rate-limit-detected",
            level: "warn",
            request_id: String(activeRequest()?.requestId || "") || null,
            data: { detection_revision: 63, until_ms: until, text: text.slice(0, 240), href: location.href },
          },
        });
      } catch (_) {}
    }
    await terminateActiveRequest(next);
    return next;
  }

  async function inspect() {
    const text = rateLimitText();
    if (!text) return null;
    return publish(text);
  }

  function schedule(delay = 60) {
    clearTimeout(state.timer);
    state.timer = setTimeout(() => {
      state.timer = null;
      inspect().catch(() => {});
    }, delay);
  }

  state.detect = inspect;
  state.text = rateLimitText;
  state.matches = matchesRateLimitText;
  state.matchesHard = matchesHardLimitText;
  state.latestAssistantLimitText = latestAssistantLimitText;
  state.terminateActiveRequest = terminateActiveRequest;

  state.observer = new MutationObserver(() => schedule(60));
  state.observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
    attributeFilter: ["role", "aria-modal", "class", "data-message-author-role"],
  });
  setInterval(() => inspect().catch(() => {}), CHECK_MS);
  schedule(0);
})();
