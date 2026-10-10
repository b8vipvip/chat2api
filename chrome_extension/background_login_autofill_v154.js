(() => {
  "use strict";
  const KEY = "__CHAT2API_WORKER_AUTO_LOGIN_V154__";
  if (globalThis[KEY]) return;
  const state = { active: null, code: null, codeAt: 0 };
  globalThis[KEY] = state;
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const trustedPage = raw => {
    try {
      const url = new URL(raw);
      return url.protocol === "https:" && (
        ["chatgpt.com", "www.chatgpt.com", "chat.openai.com", "auth.openai.com", "login.openai.com"].includes(url.hostname)
      );
    } catch (_) { return false; }
  };

  async function report(attemptId, status, extra = {}) {
    await trySendSocket({
      type: "extension.status",
      metadata: {
        worker_login_attempt_id: attemptId,
        worker_login_recovery_state: status,
        ...extra,
      },
    });
  }

  // Runs in Chrome's isolated extension world. No credentials are saved to
  // local/session storage, logged, or passed to a website other than the
  // validated ChatGPT/OpenAI sign-in page.
  function loginStep(username, password, otp) {
    const visible = node => {
      if (!node || !(node instanceof HTMLElement)) return false;
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const first = selectors => selectors.map(selector => [...document.querySelectorAll(selector)].find(visible)).find(Boolean);
    const setValue = (input, value) => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      if (!setter) return false;
      setter.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    };
    const submit = input => {
      const form = input.closest("form");
      const button = form && [...form.querySelectorAll("button[type=submit],input[type=submit],button:not([type])")].find(visible);
      if (button) button.click();
      else if (form) form.requestSubmit();
    };
    if (first(["iframe[src*='captcha']", "[data-sitekey]", ".cf-turnstile"])) {
      return { phase: "challenge" };
    }
    const codeInput = first([
      "input[autocomplete='one-time-code']", "input[name='code']", "input[name='otp']",
      "input[id*='otp']", "input[aria-label*='verification code' i]",
    ]);
    if (codeInput) {
      if (!otp) return { phase: "need_totp" };
      setValue(codeInput, otp);
      submit(codeInput);
      return { phase: "totp_submitted" };
    }
    const passwordInput = first(["input[type='password'][autocomplete='current-password']", "input[type='password']"]);
    if (passwordInput) {
      const email = first(["input[type='email']", "input[autocomplete='username']", "input[name='username']"]);
      if (email) setValue(email, username);
      setValue(passwordInput, password);
      submit(passwordInput);
      return { phase: "password_submitted" };
    }
    const emailInput = first(["input[type='email']", "input[autocomplete='username']", "input[name='username']"]);
    if (emailInput) {
      setValue(emailInput, username);
      submit(emailInput);
      return { phase: "email_submitted" };
    }
    const loginControl = [...document.querySelectorAll("a,button")].find(node =>
      visible(node) && /^(log in|sign in|登录)$/i.test(String(node.innerText || node.textContent || "").trim())
    );
    if (loginControl && /^(chatgpt\.com|www\.chatgpt\.com|chat\.openai\.com)$/.test(location.hostname)) {
      loginControl.click();
      return { phase: "login_opened" };
    }
    return { phase: "awaiting_page" };
  }

  async function automate(message) {
    const attemptId = String(message.attempt_id || "");
    const login = globalThis.__CHAT2API_LOGIN_READINESS_V27__;
    let lastAction = "";
    let lastActionAt = 0;
    let lastCodeRequest = 0;
    let lastUrl = "";
    try {
      if (!/^[A-Za-z0-9_-]{12,64}$/.test(attemptId) || typeof login?.openAutomaticLoginWindow !== "function") throw Error("unavailable");
      await report(attemptId, "automating");
      const surface = await login.openAutomaticLoginWindow();
      if (!Number.isInteger(surface?.tab_id)) throw Error("login_window_missing");
      const deadline = Date.now() + 150000;
      while (Date.now() < deadline) {
        const snapshot = await login.detect(true).catch(() => null);
        if (snapshot?.state === "ready" && snapshot.composer_ready === true &&
            Number(snapshot.checked_at_ms) >= Number(message.started_at_ms || 0)) {
          await report(attemptId, "logged_in");
          if (typeof sendExtensionStatus === "function") await sendExtensionStatus(false).catch(() => {});
          return;
        }
        const tab = await chrome.tabs.get(surface.tab_id).catch(() => null);
        const url = tab?.url || tab?.pendingUrl || "";
        if (!trustedPage(url)) {
          await delay(1200);
          continue;
        }
        const now = Date.now();
        // Avoid re-submitting credentials to an unchanged form after an
        // authentication error. A URL change or new field stage is allowed.
        if (lastAction && lastUrl === url && now - lastActionAt < 4500) {
          await delay(1200);
          continue;
        }
        const currentCode = state.code && Date.now() - state.codeAt < 22000 ? state.code : "";
        const injected = await chrome.scripting.executeScript({
          target: { tabId: surface.tab_id },
          func: loginStep,
          args: [message.username, message.password, currentCode],
        }).catch(() => []);
        const phase = injected?.[0]?.result?.phase || "awaiting_page";
        if (phase === "challenge") {
          await report(attemptId, "manual_required");
          return;
        }
        if (phase === "need_totp") {
          if (now - lastCodeRequest > 5000) {
            lastCodeRequest = now;
            await report(attemptId, "waiting_otp", { worker_login_totp_request_attempt_id: attemptId });
          }
          await delay(1200);
          continue;
        }
        if (phase === "totp_submitted") {
          state.code = null;
          state.codeAt = 0;
        }
        if (phase !== "awaiting_page") {
          const action = phase + ":" + url;
          if (lastAction === action && now - lastActionAt < 20000) {
            await report(attemptId, "manual_required");
            return;
          }
          lastAction = action;
          lastActionAt = now;
          lastUrl = url;
        }
        await delay(1200);
      }
      await report(attemptId, "manual_required");
    } catch (_) {
      await report(attemptId, "failed");
    } finally {
      state.active = null;
      state.code = null;
      state.codeAt = 0;
    }
  }

  const previous = handleServerMessage;
  handleServerMessage = async message => {
    if (message?.type === "worker.login.totp.v154") {
      if (state.active === message.attempt_id && /^\d{6}$/.test(String(message.code || ""))) {
        state.code = String(message.code);
        state.codeAt = Date.now();
      }
      return;
    }
    if (message?.type !== "worker.login.start.v154") return previous(message);
    if (state.active) return;
    state.active = String(message.attempt_id || "");
    void automate(message);
  };
})();