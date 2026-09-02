(() => {
  const KEY = "__CHAT2API_INTERRUPTION_GUARD_V72__";
  if (globalThis[KEY]) return;

  const state = {
    revision: 72,
    observer: null,
    timer: null,
    lastActionAt: 0,
    lastFingerprint: "",
    resolving: null,
  };

  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  const lower = value => normalize(value).toLowerCase();

  function visible(element) {
    if (!element) return false;
    try {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0";
    } catch (_) {
      return false;
    }
  }

  function labelOf(element) {
    return normalize(`${element?.dataset?.testid || ""} ${element?.getAttribute?.("aria-label") || ""} ${element?.title || ""} ${element?.innerText || element?.textContent || ""}`);
  }

  function activeRequest() {
    return globalThis.__CHAT2API_REQUEST_CONTENT_V6__?.active
      || globalThis.__CHAT2API_REQUEST_CONTENT_V5__?.active
      || null;
  }

  async function log(action, data = {}, level = "info") {
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.log.append",
        entry: {
          component: "page",
          action,
          level,
          request_id: activeRequest()?.requestId || null,
          data: { interruption_guard_revision: 72, ...data },
        },
      });
    } catch (_) {}
  }

  function clickSafe(element) {
    if (!element || !visible(element) || element.disabled || element.getAttribute("aria-disabled") === "true") return false;
    try {
      element.scrollIntoView?.({ block: "nearest", inline: "nearest" });
      element.click();
      return true;
    } catch (_) {
      return false;
    }
  }

  function boundedContext(element) {
    let current = element;
    for (let depth = 0; current && depth < 7; depth += 1, current = current.parentElement) {
      const text = normalize(current.innerText || current.textContent || "");
      if (text.length >= 20 && text.length <= 2600) {
        if (current.matches?.("[role='dialog'],[role='alertdialog'],[aria-modal='true'],article,section")) return { root: current, text };
        if (/reconnect|connect|connection|expired|which response|prefer|feedback|consent|github|not now|no thanks|稍后|暂不|跳过|偏好|更喜欢/i.test(text)) {
          return { root: current, text };
        }
      }
    }
    const fallback = element.closest?.("[role='dialog'],[role='alertdialog'],[aria-modal='true']") || element.parentElement;
    return { root: fallback, text: normalize(fallback?.innerText || fallback?.textContent || "") };
  }

  function hardBlockerText(text) {
    return /(captcha|verify you are human|human verification|cloudflare|sign in|log in|enter password|two.factor|2fa|验证码|验证你是人类|登录|密码|双重验证)/i.test(text);
  }

  function connectorContext(text) {
    const value = lower(text);
    const service = /(github|google drive|onedrive|dropbox|sharepoint|notion|slack|connector|plugin|app|应用|连接器|插件)/i.test(value);
    const connection = /(reconnect|connect|connection|expired|authorization|authorize|permission|account|重新连接|连接|授权|权限|账户|已过期)/i.test(value);
    return service && connection && !hardBlockerText(value);
  }

  function feedbackContext(text) {
    return /(feedback|help improve|try a new version|experiment|survey|which response|prefer|反馈|帮助改进|新版本|实验|调查|更喜欢哪个|偏好)/i.test(text) && !hardBlockerText(text);
  }

  function safeDismissLabel(text) {
    return /^(not now|no thanks|maybe later|later|skip|dismiss|close|cancel|暂不|暂时不要|不用了|以后再说|稍后|跳过|关闭|取消)$/i.test(normalize(text));
  }

  function findSafeDismiss() {
    const candidates = [...document.querySelectorAll("button,[role='button']")].filter(visible);
    for (const button of candidates) {
      const label = labelOf(button);
      if (!safeDismissLabel(label)) continue;
      const context = boundedContext(button);
      if (!context.root || hardBlockerText(context.text)) continue;
      if (connectorContext(context.text) || feedbackContext(context.text)) {
        return { button, context, label };
      }
    }
    return null;
  }

  function preferenceContext(text) {
    return /(which response do you prefer|which answer do you prefer|which one do you prefer|choose the response you prefer|you.re giving feedback on a new version|更喜欢哪个回复|更喜欢哪一个回复|请选择你更喜欢的回复|哪个回答更好|选择你更喜欢的回答)/i.test(text);
  }

  function candidateScore(element) {
    const label = labelOf(element);
    const value = lower(label);
    let score = 0;
    if (/(response|answer|option|candidate|回复|回答|选项)\s*(1|a)\b/i.test(value)) score += 100;
    if (/^(1|a)[\s.:：、-]/i.test(value)) score += 60;
    const testid = lower(element?.dataset?.testid || "");
    if (/(response|answer|option|candidate).*(1|a)/i.test(testid)) score += 120;
    if (element.getAttribute?.("role") === "radio") score += 15;
    if (value.includes("response 2") || value.includes("answer 2") || value.includes("option 2") || value.includes("回复 2") || value.includes("回答 2")) score -= 200;
    return score;
  }

  function findPreferenceChoice() {
    const clickables = [...document.querySelectorAll("button,[role='button'],[role='radio'],label,[data-testid]")].filter(visible);
    const roots = [];
    const seen = new Set();
    for (const element of clickables) {
      const context = boundedContext(element);
      if (!context.root || seen.has(context.root) || !preferenceContext(context.text)) continue;
      seen.add(context.root);
      roots.push(context);
    }
    for (const context of roots) {
      const candidates = [...context.root.querySelectorAll("button,[role='button'],[role='radio'],label,[data-testid]")]
        .filter(visible)
        .map((element, index) => ({ element, index, score: candidateScore(element) }))
        .filter(item => item.score > 0)
        .sort((a, b) => b.score - a.score || a.index - b.index);
      if (candidates.length) return { choice: candidates[0].element, context, strategy: "labeled-first-response" };

      const responseCards = [...context.root.querySelectorAll("[data-testid*='response'],[data-testid*='answer'],[data-testid*='candidate']")]
        .filter(element => visible(element) && !/2|second|b$/i.test(lower(element.dataset?.testid || "")));
      if (responseCards.length) return { choice: responseCards[0], context, strategy: "first-response-card" };
    }
    return null;
  }

  async function resolveBlockingInterruption(options = {}) {
    if (state.resolving) return state.resolving;
    state.resolving = (async () => {
      const force = Boolean(options.force);
      if (!force && !activeRequest()) return { handled: false, reason: "idle" };

      // Resolve at most a few stacked safe interruptions. Never click positive
      // connect/authorize/consent actions and never attempt CAPTCHA/login bypass.
      const actions = [];
      for (let step = 0; step < 4; step += 1) {
        const preference = findPreferenceChoice();
        if (preference && clickSafe(preference.choice)) {
          const fingerprint = `preference:${lower(preference.context.text).slice(0, 180)}`;
          actions.push("preference-first");
          state.lastFingerprint = fingerprint;
          state.lastActionAt = Date.now();
          await log("interruption-auto-resolved", {
            kind: "response-preference",
            action: "select-first-response",
            strategy: preference.strategy,
            context: normalize(preference.context.text).slice(0, 500),
          });
          await new Promise(resolve => setTimeout(resolve, 220));
          continue;
        }

        const dismiss = findSafeDismiss();
        if (dismiss && clickSafe(dismiss.button)) {
          actions.push("safe-dismiss");
          state.lastFingerprint = `dismiss:${lower(dismiss.context.text).slice(0, 180)}`;
          state.lastActionAt = Date.now();
          await log("interruption-auto-resolved", {
            kind: connectorContext(dismiss.context.text) ? "connector-or-app-card" : "feedback-or-consent-card",
            action: "safe-negative-dismiss",
            button: dismiss.label,
            context: normalize(dismiss.context.text).slice(0, 500),
          });
          await new Promise(resolve => setTimeout(resolve, 180));
          continue;
        }
        break;
      }
      return { handled: actions.length > 0, actions };
    })();
    try {
      return await state.resolving;
    } finally {
      state.resolving = null;
    }
  }

  function schedule(reason = "mutation") {
    clearTimeout(state.timer);
    state.timer = setTimeout(() => {
      if (!activeRequest()) return;
      resolveBlockingInterruption({ reason }).catch(() => {});
    }, 70);
  }

  state.resolveBlockingInterruption = resolveBlockingInterruption;
  state.findPreferenceChoice = findPreferenceChoice;
  state.findSafeDismiss = findSafeDismiss;
  globalThis[KEY] = state;

  state.observer = new MutationObserver(() => schedule("mutation"));
  state.observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["role", "aria-modal", "aria-label", "aria-disabled", "data-testid", "disabled"] });
  log("interruption-guard-ready", { revision: 72 }).catch(() => {});
})();
