(() => {
  const KEY = "__CHAT2API_UI_HYGIENE_V31__";
  if (globalThis[KEY]) return;

  const state = {
    scans: 0,
    dismissed: 0,
    lastAction: "",
    lastCategory: "",
    lastAt: 0,
  };
  const handled = new WeakSet();
  let scanTimer = 0;

  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  function visible(node) {
    if (!(node instanceof Element)) return false;
    const rect = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) > 0;
  }

  function textOf(node) {
    return normalize(`${node?.getAttribute?.("aria-label") || ""} ${node?.innerText || node?.textContent || ""}`);
  }

  function dialogRoots() {
    const selectors = [
      "[role='dialog']",
      "[aria-modal='true']",
      "[data-state='open'][role='dialog']",
    ];
    const roots = [];
    for (const selector of selectors) {
      for (const node of document.querySelectorAll(selector)) {
        if (!visible(node) || roots.includes(node)) continue;
        roots.push(node);
      }
    }
    return roots;
  }

  // Never automate security/authentication, financial, destructive-account or
  // identity-verification surfaces. Generic footer links such as "Privacy
  // policy" are intentionally not included because harmless product-tour
  // dialogs frequently contain them.
  const dangerousContext = /(captcha|verify you are human|security check|two[- ]?factor|2fa|authenticator|passkey|password|email address|phone number|log ?in|sign ?in|sign ?up|payment|billing|subscription|purchase|delete|remove account|log ?out|sign ?out|age verification|identity verification|验证码|人机验证|安全验证|两步验证|双重验证|密码|邮箱|手机号|登录|注册|付款|账单|订阅|购买|删除账号|退出登录|年龄验证|身份验证)/i;
  const nuisanceContext = /(more relevant, personalized replies|personalized replies|personalised replies|memory|remembering|what'?s new|new feature|new features|introducing|welcome to|tips|onboarding|try .*feature|get notified|desktop notifications|notifications|stay up to date|remind me|product update|feature update|个性化回复|个性化|记忆|新功能|功能更新|欢迎|使用提示|新手引导|通知|桌面通知|保持更新|产品更新)/i;
  const microphoneContext = /(microphone|audio input|voice mode|voice chat|voice conversation|麦克风|音频输入|语音模式|语音聊天|语音对话)/i;
  const safeDismissButton = /^(got it|ok|okay|done|close|dismiss|skip|not now|maybe later|later|no thanks|understood|知道了|好的|确定|完成|关闭|跳过|暂不|以后再说|不用了)$/i;
  const micAllowButton = /^(allow|enable|continue|got it|ok|okay|允许|启用|继续|知道了|确定)$/i;

  function candidateButtons(root) {
    return [...root.querySelectorAll("button,[role='button']")]
      .filter(visible)
      .map(node => ({ node, text: textOf(node) }))
      .filter(item => item.text && item.text.length <= 100);
  }

  function chooseAction(root) {
    const context = textOf(root).slice(0, 1600);
    if (!context || dangerousContext.test(context)) return null;
    const buttons = candidateButtons(root);

    if (microphoneContext.test(context)) {
      const allow = buttons.find(item => micAllowButton.test(item.text));
      if (allow) return { ...allow, category: "microphone-preflight" };
    }

    if (!nuisanceContext.test(context)) return null;
    const dismiss = buttons.find(item => safeDismissButton.test(item.text));
    return dismiss ? { ...dismiss, category: "nuisance-modal" } : null;
  }

  function scan() {
    state.scans += 1;
    for (const root of dialogRoots()) {
      if (handled.has(root)) continue;
      const action = chooseAction(root);
      if (!action) continue;
      handled.add(root);
      try {
        action.node.click();
        state.dismissed += 1;
        state.lastAction = action.text.slice(0, 80);
        state.lastCategory = action.category;
        state.lastAt = Date.now();
      } catch (_) {}
    }
  }

  function scheduleScan(delay = 50) {
    clearTimeout(scanTimer);
    scanTimer = setTimeout(scan, delay);
  }

  const observer = new MutationObserver(() => scheduleScan(80));
  observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["role", "aria-modal", "data-state", "class", "style"] });

  document.addEventListener("visibilitychange", () => { if (!document.hidden) scheduleScan(20); });
  window.addEventListener("focus", () => scheduleScan(20));
  setInterval(() => scan(), 3000);
  scan();

  globalThis[KEY] = Object.freeze({ state, scan });
})();
