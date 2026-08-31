(() => {
  const KEY = "__CHAT2API_RATE_LIMIT_CONTENT_V52__";
  if (globalThis[KEY]) return;

  const STORAGE_KEY = "chatgptRateLimitGuardV52";
  const COOLDOWN_MS = 5 * 60 * 1000;
  const CHECK_MS = 1500;
  const MATCHERS = [
    /请求过于频繁/i,
    /暂时限制.*访问对话记录/i,
    /请稍等几分钟后再重试/i,
    /too many requests/i,
    /requests? (?:are )?too frequent/i,
    /temporarily limited.*conversation history/i,
    /try again in a few minutes/i,
  ];

  const state = {
    timer: null,
    observer: null,
    lastFingerprint: "",
    terminalRequestId: "",
  };
  globalThis[KEY] = state;

  const normalize = value => String(value || "").replace(/\s+/g, " ").trim();

  function visible(node) {
    if (!node) return false;
    const rect = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) > 0;
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
      if (text && MATCHERS.some(pattern => pattern.test(text))) return text.slice(0, 500);
    }
    return "";
  }

  function activeRequest() {
    // request-v5 is the authoritative text controller in the current bundle.
    // Keep the base-controller fallback for partially upgraded tabs while the
    // extension autoreload mechanism converges them to the current runtime.
    return globalThis.__CHAT2API_REQUEST_CONTENT_V5__?.active || globalThis.__CHAT2API_CONTENT__?.active || null;
  }

  async function terminateActiveRequest(snapshot) {
    const active = activeRequest();
    const requestId = String(active?.requestId || "");
    if (!requestId || state.terminalRequestId === requestId) return false;
    state.terminalRequestId = requestId;
    const seconds = Math.max(1, Math.ceil(Math.max(0, Number(snapshot?.until_ms || 0) - Date.now()) / 1000));
    const error = `ChatGPT is temporarily rate limited; request dispatch paused for ${seconds}s after a visible Too many requests response`;

    // Send the terminal browser error before cancelling the local monitor so
    // the server receives the rate-limit reason first and can enter its shared
    // Worker cooldown instead of recording a generic 300s timeout.
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "chat.error",
          request_id: requestId,
          error,
          diagnostics: {
            chatgpt_rate_limit_detected: true,
            chatgpt_rate_limit_source: String(snapshot?.source || "visible-chatgpt-rate-limit-modal-v52"),
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
      active: true,
      detected_at_ms: now,
      until_ms: until,
      url: location.href,
      title: document.title,
      text,
      source: "visible-chatgpt-rate-limit-modal-v52",
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
            data: { until_ms: until, text: text.slice(0, 240), href: location.href },
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

  function schedule(delay = 120) {
    clearTimeout(state.timer);
    state.timer = setTimeout(() => {
      state.timer = null;
      inspect().catch(() => {});
    }, delay);
  }

  state.detect = inspect;
  state.text = rateLimitText;
  state.terminateActiveRequest = terminateActiveRequest;

  state.observer = new MutationObserver(() => schedule(120));
  state.observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["role", "aria-modal", "class"] });
  setInterval(() => inspect().catch(() => {}), CHECK_MS);
  schedule(0);
})();
