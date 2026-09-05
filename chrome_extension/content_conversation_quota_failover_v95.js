(() => {
  const KEY = "__CHAT2API_CONVERSATION_QUOTA_FAILOVER_V95__";
  if (globalThis[KEY]) return;

  const REQUEST_V5_KEY = "__CHAT2API_REQUEST_CONTENT_V5__";
  const REQUEST_V6_KEY = "__CHAT2API_REQUEST_CONTENT_V6__";
  const MESSAGE_TYPE = "chat2api.conversation-quota-blocked.v95";
  const CHECK_MS = 120;
  const LOCAL_QUOTA_PATTERNS = [
    /聊天已暂停.{0,80}(?:额度|使用额度).{0,80}(?:重置|恢复)/i,
    /(?:额度|使用额度).{0,80}(?:重置|恢复).{0,80}聊天已暂停/i,
    /达到.{0,40}(?:包含|含有).{0,30}(?:文件|图像|图片).{0,40}(?:聊天次数)?上限/i,
    /请(?:发起|开始|新建).{0,30}(?:新的)?纯文本聊天/i,
    /chat (?:is )?paused.{0,100}(?:limit|usage|reset)/i,
    /(?:usage|message) limit.{0,100}(?:chat (?:is )?paused|reset)/i,
    /(?:start|begin|create).{0,40}(?:a )?new (?:text-only|text only|plain text) chat/i,
    /(?:files?|images?).{0,80}(?:chat|conversation).{0,80}(?:limit|maximum)/i,
  ];

  const state = {
    version: 95,
    revision: 95,
    listener: null,
    observer: null,
    timer: null,
    failoverRequested: new Set(),
    detections: 0,
    last: null,
  };
  globalThis[KEY] = state;

  const normalize = value => String(value || "").replace(/\s+/g, " ").trim();

  function matchesConversationQuota(value) {
    const text = normalize(value);
    return Boolean(text && LOCAL_QUOTA_PATTERNS.some(pattern => pattern.test(text)));
  }

  function visible(element) {
    if (!element) return false;
    try {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) > 0;
    } catch (_) {
      return false;
    }
  }

  function composer() {
    for (const selector of [
      "#prompt-textarea",
      "textarea[placeholder]",
      "div[contenteditable='true'][data-lexical-editor='true']",
      "div[contenteditable='true'].ProseMirror",
      "[contenteditable='true']",
    ]) {
      const node = [...document.querySelectorAll(selector)].find(visible);
      if (node) return node;
    }
    return null;
  }

  function composerText(node = composer()) {
    if (!node) return "";
    if (node instanceof HTMLTextAreaElement || node instanceof HTMLInputElement) return normalize(node.value || "");
    return normalize(node.innerText || node.textContent || "");
  }

  function sendButton() {
    for (const selector of [
      "button[data-testid='send-button']",
      "button[aria-label='Send prompt']",
      "button[aria-label*='发送提示']",
      "button[aria-label*='发送消息']",
      "form button[type='submit']",
    ]) {
      const button = [...document.querySelectorAll(selector)].find(visible);
      if (button) return button;
    }
    return null;
  }

  function sendReady() {
    const button = sendButton();
    return Boolean(button && !button.disabled && button.getAttribute("aria-disabled") !== "true");
  }

  function pageText() {
    const root = document.querySelector("main") || document.querySelector("[role='main']") || document.body;
    const text = normalize(root?.innerText || root?.textContent || "");
    // The quota banner is near the composer and short. Bound the scan so a very
    // long historical chat cannot turn each active-request check into an
    // unbounded full-history regexp pass.
    return text.length > 24000 ? text.slice(-24000) : text;
  }

  function quotaText() {
    const text = pageText();
    if (!matchesConversationQuota(text)) return "";
    for (const pattern of LOCAL_QUOTA_PATTERNS) {
      const match = text.match(pattern);
      if (!match) continue;
      const index = Math.max(0, Number(match.index || 0) - 120);
      return text.slice(index, Math.min(text.length, index + 720));
    }
    return text.slice(-720);
  }

  function activeRequest() {
    return globalThis[REQUEST_V6_KEY]?.active || globalThis[REQUEST_V5_KEY]?.active || null;
  }

  function activePromptStillPresent(active) {
    const current = composerText();
    const target = normalize(active?.promptNormalized || active?.prompt || "");
    if (!target || !current) return false;
    return current === target || (target.length > 6 && current.includes(target));
  }

  async function requestFailover(requestId, text, stage) {
    requestId = String(requestId || "");
    if (!requestId || state.failoverRequested.has(requestId)) return false;
    state.failoverRequested.add(requestId);
    state.detections += 1;
    state.last = {
      request_id: requestId,
      stage: String(stage || "active-request"),
      text: String(text || "").slice(0, 720),
      href: String(location.href || ""),
      at_ms: Date.now(),
    };
    try {
      await chrome.runtime.sendMessage({
        type: MESSAGE_TYPE,
        request_id: requestId,
        reason: "conversation-local-quota-blocked-v95",
        stage: state.last.stage,
        text: state.last.text,
        href: state.last.href,
      });
      return true;
    } catch (_) {
      state.failoverRequested.delete(requestId);
      return false;
    }
  }

  async function inspectActive() {
    const active = activeRequest();
    const requestId = String(active?.requestId || "");
    if (!requestId || state.failoverRequested.has(requestId)) return null;
    if (active?.responseStarted) return null;
    if (!activePromptStillPresent(active)) return null;
    if (sendReady()) return null;
    const text = quotaText();
    if (!text) return null;
    await requestFailover(requestId, text, "active-disabled-composer");
    return text;
  }

  function schedule(delay = 30) {
    clearTimeout(state.timer);
    state.timer = setTimeout(() => {
      state.timer = null;
      inspectActive().catch(() => {});
    }, Math.max(0, delay));
  }

  // Wrap the final request listener after request-lifecycle-v50. If a prior
  // conversation is already blocked before the next API turn starts, do not
  // write the prompt into that poisoned conversation at all; ask the background
  // failover owner to close it and replay the same request on a fresh window.
  const base = globalThis[REQUEST_V5_KEY];
  const priorListener = base?.listener;
  if (typeof priorListener === "function") {
    try { chrome.runtime.onMessage.removeListener(priorListener); } catch (_) {}
    const listener = (message, sender, sendResponse) => {
      if (message?.type === "chat2api.request") {
        const requestId = String(message?.requestId || message?.request_id || "");
        const text = !sendReady() ? quotaText() : "";
        if (requestId && text) {
          requestFailover(requestId, text, "pre-dispatch-disabled-conversation").catch(() => {});
          sendResponse({
            ok: true,
            controller: "conversation-quota-failover-v95",
            reroute_pending: true,
            request_id: requestId,
          });
          return false;
        }
      }
      try {
        return priorListener(message, sender, sendResponse);
      } catch (error) {
        throw error;
      }
    };
    state.listener = listener;
    base.listener = listener;
    chrome.runtime.onMessage.addListener(listener);
  }

  state.matchesConversationQuota = matchesConversationQuota;
  state.quotaText = quotaText;
  state.inspectActive = inspectActive;
  state.requestFailover = requestFailover;

  state.observer = new MutationObserver(() => schedule(30));
  state.observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
    attributeFilter: ["disabled", "aria-disabled", "class"],
  });
  setInterval(() => inspectActive().catch(() => {}), CHECK_MS);
  schedule(0);
})();
