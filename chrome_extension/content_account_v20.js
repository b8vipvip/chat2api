(() => {
  const KEY = "__CHAT2API_ACCOUNT_DETECTOR_V20__";
  if (globalThis[KEY]) return;

  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  function visible(node) {
    if (!(node instanceof Element)) return false;
    const rect = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function explicitBootstrapPlan() {
    const node = document.querySelector("#client-bootstrap, script[id*='bootstrap'], script[data-testid*='bootstrap']");
    const raw = String(node?.textContent || node?.innerHTML || "");
    if (!raw) return null;
    const lower = raw.toLowerCase();
    const freePatterns = [
      /[\"'](?:plan|plan_type|planType|account_plan|subscription_plan|subscriptionPlan)[\"']\s*[:=]\s*[\"']free[\"']/i,
      /[\"'](?:is_paid|isPaid|paid)[\"']\s*[:=]\s*false/i,
      /[\"'](?:subscription_tier|subscriptionTier|account_tier|accountTier)[\"']\s*[:=]\s*[\"']free[\"']/i,
    ];
    if (freePatterns.some(pattern => pattern.test(raw))) {
      return { account_type: "free", confidence: "high", strategy: "client-bootstrap-free" };
    }
    const paidPatterns = [
      /[\"'](?:plan|plan_type|planType|account_plan|subscription_plan|subscriptionPlan)[\"']\s*[:=]\s*[\"'](?:plus|pro|team|business|enterprise|edu)[\"']/i,
      /[\"'](?:subscription_tier|subscriptionTier|account_tier|accountTier)[\"']\s*[:=]\s*[\"'](?:plus|pro|team|business|enterprise|edu)[\"']/i,
      /[\"'](?:is_paid|isPaid|paid)[\"']\s*[:=]\s*true/i,
    ];
    if (paidPatterns.some(pattern => pattern.test(raw))) {
      return { account_type: "paid", confidence: "high", strategy: "client-bootstrap-paid" };
    }
    if (/\bchatgpt\s+(plus|pro|team|business|enterprise|edu)\b/i.test(lower)) {
      return { account_type: "paid", confidence: "high", strategy: "client-bootstrap-plan-name" };
    }
    return null;
  }

  function explicitUiPlan() {
    const scopes = [
      document.querySelector("nav"),
      document.querySelector("aside"),
      document.querySelector("header"),
      ...document.querySelectorAll("[data-testid*='account'],[data-testid*='profile'],[data-testid*='plan'],[aria-label*='account' i],[aria-label*='profile' i]"),
    ].filter(Boolean);
    const seen = new Set();
    for (const scope of scopes) {
      if (seen.has(scope)) continue;
      seen.add(scope);
      const text = normalize(scope.innerText || scope.textContent || "");
      if (!text || text.length > 4000) continue;
      if (/(^|\s)(ChatGPT\s+)?Free($|\s)|免费版|免费计划/i.test(text)) {
        return { account_type: "free", confidence: "high", strategy: "account-ui-free" };
      }
      if (/(^|\s)ChatGPT\s+(Plus|Pro|Team|Business|Enterprise|Edu)($|\s)|企业版|团队版/i.test(text)) {
        return { account_type: "paid", confidence: "high", strategy: "account-ui-paid" };
      }
    }
    return null;
  }

  function composerRoot() {
    return document.querySelector("form[data-type='unified-composer']")
      || document.querySelector("form")?.closest("main")
      || document.querySelector("main");
  }

  function composerReady() {
    const root = composerRoot();
    if (!root) return false;
    const editable = root.querySelector("[contenteditable='true'],textarea") || document.querySelector("[contenteditable='true'],textarea");
    return Boolean(editable && visible(editable));
  }

  function selectableModelControl() {
    const roots = [composerRoot(), document.querySelector("header")].filter(Boolean);
    for (const root of roots) {
      const controls = root.querySelectorAll("button,[role='button'],[aria-haspopup='menu'],[data-testid*='model']");
      for (const control of controls) {
        if (!visible(control)) continue;
        const text = normalize(`${control.getAttribute("aria-label") || ""} ${control.getAttribute("data-testid") || ""} ${control.innerText || control.textContent || ""}`);
        if (!text || text.length > 160) continue;
        if (/\bGPT[- ]?5(?:\.5|\.6)?\b|\b5\.(?:5|6)\b|model\s*(selector|picker)|选择模型|模型选择/i.test(text)) {
          return { text: text.slice(0, 120), strategy: "selectable-model-control" };
        }
      }
    }
    return null;
  }

  function signedInEvidence() {
    if (document.querySelector("[data-message-author-role], [data-testid^='conversation-turn']")) return true;
    const sidebar = document.querySelector("nav,aside");
    if (sidebar && visible(sidebar)) return true;
    const profile = [...document.querySelectorAll("button,[role='button']")].find(node => {
      if (!visible(node)) return false;
      const text = normalize(`${node.getAttribute("aria-label") || ""} ${node.getAttribute("data-testid") || ""}`);
      return /profile|account|user menu|个人资料|账号|账户/i.test(text);
    });
    return Boolean(profile);
  }

  function detect() {
    const explicit = explicitBootstrapPlan() || explicitUiPlan();
    if (explicit) {
      return {
        ...explicit,
        detector: "account-v20",
        model_control_present: Boolean(selectableModelControl()),
        composer_ready: composerReady(),
      };
    }

    const modelControl = selectableModelControl();
    if (modelControl) {
      return {
        account_type: "paid",
        confidence: "medium",
        strategy: modelControl.strategy,
        detector: "account-v20",
        model_control_present: true,
        composer_ready: composerReady(),
      };
    }

    const ready = composerReady();
    if (ready && signedInEvidence() && document.readyState === "complete") {
      return {
        account_type: "free",
        confidence: "medium",
        strategy: "ready-composer-without-model-selector",
        detector: "account-v20",
        model_control_present: false,
        composer_ready: true,
      };
    }

    return {
      account_type: "unknown",
      confidence: "low",
      strategy: "insufficient-passive-evidence",
      detector: "account-v20",
      model_control_present: false,
      composer_ready: ready,
    };
  }

  globalThis[KEY] = { detect };

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "chat2api.account.detect.v20") return false;
    try {
      sendResponse({ ok: true, data: detect() });
    } catch (error) {
      sendResponse({ ok: false, error: String(error?.message || error) });
    }
    return true;
  });
})();
