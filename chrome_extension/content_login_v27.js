(() => {
  const KEY = "__CHAT2API_LOGIN_DETECTOR_V27__";
  if (globalThis[KEY]) return;

  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  const AUTH_CONTROL_RE = /^(log\s*in|sign\s*in|sign\s*up|登录|登入|注册|註冊)$/i;

  function visible(node) {
    if (!(node instanceof Element)) return false;
    const rect = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) > 0;
  }

  function composer() {
    const selectors = [
      "#prompt-textarea",
      "form[data-type='unified-composer'] textarea",
      "form[data-type='unified-composer'] [contenteditable='true']",
      "textarea[placeholder]",
      "div[contenteditable='true'][data-lexical-editor='true']",
      "div[contenteditable='true'].ProseMirror",
    ];
    for (const selector of selectors) {
      const node = [...document.querySelectorAll(selector)].find(visible);
      if (node) return node;
    }
    return null;
  }

  function authPathEvidence() {
    const path = String(location.pathname || "").toLowerCase();
    if (/(^|\/)(auth|login|signin|sign-in|signup|sign-up)(\/|$)/.test(path)) {
      return { kind: "path", value: path.slice(0, 160) };
    }
    return null;
  }

  function authUiEvidence() {
    const candidates = document.querySelectorAll("a,button,[role='button']");
    for (const node of candidates) {
      if (!visible(node)) continue;
      const values = [
        normalize(node.getAttribute("aria-label") || ""),
        normalize(node.innerText || node.textContent || ""),
      ];
      for (const text of values) {
        if (!text || text.length > 120) continue;
        if (AUTH_CONTROL_RE.test(text)) {
          return { kind: "auth-control", value: text.slice(0, 80) };
        }
      }
    }
    return null;
  }

  function detect() {
    // Logged-out ChatGPT can expose a guest composer. Explicit authentication
    // UI/path evidence therefore has priority over composer presence.
    const authEvidence = authPathEvidence() || authUiEvidence();
    if (authEvidence) {
      return {
        state: "login_required",
        confidence: authEvidence.kind === "path" ? "high" : "medium",
        strategy: authEvidence.kind === "path" ? "auth-path" : "visible-auth-control",
        detector: "login-v27",
        composer_ready: false,
        auth_evidence: authEvidence,
        document_ready: document.readyState !== "loading",
        url: location.href,
        checked_at_ms: Date.now(),
      };
    }

    const readyComposer = composer();
    if (readyComposer) {
      return {
        state: "ready",
        confidence: "high",
        strategy: "visible-composer",
        detector: "login-v27",
        composer_ready: true,
        auth_evidence: null,
        document_ready: document.readyState !== "loading",
        url: location.href,
        checked_at_ms: Date.now(),
      };
    }

    if (document.readyState === "loading") {
      return {
        state: "checking",
        confidence: "low",
        strategy: "document-loading",
        detector: "login-v27",
        composer_ready: false,
        auth_evidence: null,
        document_ready: false,
        url: location.href,
        checked_at_ms: Date.now(),
      };
    }

    return {
      state: "unknown",
      confidence: "low",
      strategy: "no-passive-login-evidence",
      detector: "login-v27",
      composer_ready: false,
      auth_evidence: null,
      document_ready: true,
      url: location.href,
      checked_at_ms: Date.now(),
    };
  }

  globalThis[KEY] = Object.freeze({ detect });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "chat2api.login.detect.v27") return false;
    try {
      sendResponse({ ok: true, data: detect() });
    } catch (error) {
      sendResponse({ ok: false, error: String(error?.message || error) });
    }
    return true;
  });
})();
