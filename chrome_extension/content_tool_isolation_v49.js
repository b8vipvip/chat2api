(() => {
  const KEY = "__CHAT2API_TOOL_ISOLATION_V49__";
  if (globalThis[KEY]) return;

  const state = {
    version: 49,
    scans: 0,
    blocked: 0,
    removed_app_chips: 0,
    preflight_checks: 0,
    safe_to_dispatch: null,
    selected_app_controls: 0,
    unresolved_connection_dialogs: 0,
    last_action: "",
    last_context: "",
    last_preflight_at_ms: 0,
  };
  const handled = new WeakSet();
  let scanTimer = 0;

  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  const visible = node => {
    if (!(node instanceof Element)) return false;
    const rect = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    return rect.width > 0 && rect.height > 0
      && style.display !== "none" && style.visibility !== "hidden"
      && Number(style.opacity || 1) > 0;
  };

  const labelOf = node => normalize(
    String(node?.getAttribute?.("aria-label") || "") + " "
    + String(node?.getAttribute?.("title") || "") + " "
    + String(node?.innerText || node?.textContent || "")
  );
  const attributesOf = node => normalize(
    String(node?.getAttribute?.("data-testid") || "") + " "
    + String(node?.getAttribute?.("class") || "") + " "
    + String(node?.getAttribute?.("data-state") || "")
  );

  const positiveAction = /^(connect|reconnect|enable|authorize|allow|install|use|continue|连接|重新连接|启用|授权|允许|安装|使用|继续)(?:\s|$)/i;
  const negativeAction = /^(not now|maybe later|later|cancel|close|dismiss|skip|no thanks|don't connect|暂不|以后再说|取消|关闭|跳过|不用了|不连接|拒绝)$/i;
  const removeAction = /^(remove|clear|disconnect|移除|清除|断开)(?:\s|$)/i;
  const integrationContext = /(plugin|plugins|connector|connectors|connected app|connected apps|integration|integrations|app connection|\bapps?\b|connection has expired|reconnect .* connection|use this connection|插件|连接器|已连接应用|应用连接|应用|集成|连接已过期|重新连接|使用该连接)/i;
  const providerLabel = /\b(github|gmail|google drive|google calendar|google docs|google sheets|slack|notion|linear|outlook|microsoft teams|onedrive|sharepoint|dropbox|box|salesforce|hubspot|jira|confluence|figma)\b/i;
  const authDanger = /(captcha|verify you are human|security check|two[- ]?factor|2fa|authenticator|passkey|password|payment|billing|subscription|identity verification|人机验证|安全验证|两步验证|双重验证|密码|付款|账单|订阅|身份验证)/i;

  function activeRequestId() {
    return String(
      globalThis.__CHAT2API_REQUEST_CONTENT_V6__?.active?.requestId
      || globalThis.__CHAT2API_REQUEST_CONTENT_V5__?.active?.requestId
      || ""
    );
  }

  async function report(category, action, context) {
    const requestId = activeRequestId();
    const diagnostics = {
      tool_isolation: "tool-isolation-v49",
      external_account_tools_disabled: true,
      native_github_tool_disabled: true,
      tool_surface_blocked: true,
      tool_surface_category: category,
      tool_surface_action: action,
      tool_surface_context: normalize(context).slice(0, 240),
    };
    try {
      if (requestId) {
        await chrome.runtime.sendMessage({
          type: "chat2api.event",
          event: { type: "chat.diagnostics", request_id: requestId, diagnostics },
        });
      }
    } catch (_) {}
  }

  function candidateRoots() {
    const result = [];
    const seen = new Set();
    for (const selector of ["[role='dialog']", "[aria-modal='true']", "[data-state='open'][role='dialog']"]) {
      for (const node of document.querySelectorAll(selector)) {
        if (!visible(node) || seen.has(node)) continue;
        seen.add(node);
        result.push(node);
      }
    }
    return result;
  }

  function actionButtons(root) {
    return [...root.querySelectorAll("button,[role='button']")]
      .filter(visible)
      .map(node => ({ node, label: labelOf(node) }))
      .filter(item => item.label && item.label.length <= 140);
  }

  function classify(root) {
    const context = labelOf(root).slice(0, 2200);
    const hasIntegrationContext = integrationContext.test(context);
    const hasProvider = providerLabel.test(context);
    const protectedAuth = authDanger.test(context);
    if (!context || (!hasIntegrationContext && !hasProvider)) return null;
    const buttons = actionButtons(root);
    const positive = buttons.find(item => positiveAction.test(item.label));
    const negative = buttons.find(item => negativeAction.test(item.label));
    if (!positive && !hasProvider && !protectedAuth) return null;
    return { context, buttons, positive, negative, protectedAuth, category: "external-account-tool" };
  }

  function integrationRootForAction(node) {
    if (!(node instanceof Element)) return null;
    for (const root of [node.closest("[role='dialog']"), node.closest("[aria-modal='true']")]) {
      if (!root) continue;
      const match = classify(root);
      if (match?.positive?.node === node) return { root, match };
    }
    return null;
  }

  function preventPositiveAction(event) {
    const target = event.target instanceof Element
      ? event.target.closest("button,[role='button']") : null;
    if (!target || !positiveAction.test(labelOf(target))) return;
    const found = integrationRootForAction(target);
    if (!found) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation?.();
    state.blocked += 1;
    state.last_action = "prevented:" + labelOf(target);
    state.last_context = String(found.match?.context || "").slice(0, 240);
    report("external-account-tool", state.last_action, state.last_context).catch(() => {});
  }

  function dismissRoot(root, match) {
    if (!match?.negative || match.protectedAuth || handled.has(root)) return false;
    handled.add(root);
    try {
      match.negative.node.click();
      state.blocked += 1;
      state.last_action = match.negative.label;
      state.last_context = match.context.slice(0, 240);
      report(match.category, match.negative.label, match.context).catch(() => {});
      return true;
    } catch (_) {
      return false;
    }
  }

  function composerRoot() {
    const editor = document.querySelector("#prompt-textarea");
    return editor?.closest?.("form") || editor?.parentElement?.parentElement || document.querySelector("form");
  }

  function isSelectedAppControl(node) {
    if (!visible(node)) return false;
    const label = labelOf(node);
    const attrs = attributesOf(node);
    const testId = String(node?.getAttribute?.("data-testid") || "");
    const className = String(node?.getAttribute?.("class") || "");
    const selected = /^(true|selected|checked)$/i.test(String(node?.getAttribute?.("aria-pressed") || ""))
      || /^(true|selected|checked)$/i.test(String(node?.getAttribute?.("aria-checked") || ""))
      || /^(selected|checked|active)$/i.test(String(node?.getAttribute?.("data-state") || ""));
    const tagged = /(app|plugin|connector)/i.test(testId + " " + className);
    const named = providerLabel.test(label);
    const contextual = integrationContext.test(label);
    const chipLike = /(chip|pill|selected)/i.test(attrs);
    return (tagged && (selected || named || contextual || chipLike))
      || (selected && (named || contextual || tagged))
      || (named && (chipLike || String(node?.tagName || "").toUpperCase() === "BUTTON"));
  }

  function selectedAppControls(composer) {
    if (!composer) return [];
    const nodes = composer.querySelectorAll(
      "button,[role='button'],[data-testid],[aria-pressed='true'],[aria-checked='true'],[data-state='selected'],[data-state='checked']"
    );
    return [...new Set(nodes)].filter(isSelectedAppControl);
  }

  function clearComposerIntegrationChips() {
    const composer = composerRoot();
    if (!composer) return 0;
    let cleared = 0;
    for (const button of composer.querySelectorAll("button,[role='button']")) {
      if (!visible(button)) continue;
      const label = labelOf(button);
      if (!removeAction.test(label) || !/(plugin|connector|app|integration|github|gmail|google drive|slack|notion|插件|连接器|应用|集成)/i.test(label)) continue;
      try {
        button.click();
        cleared += 1;
      } catch (_) {}
    }
    state.removed_app_chips += cleared;
    return cleared;
  }

  function inspectSurfaces() {
    const composer = composerRoot();
    const selected = selectedAppControls(composer);
    let unresolvedDialogs = 0;
    for (const root of candidateRoots()) {
      const match = classify(root);
      if (match) unresolvedDialogs += 1;
    }
    return {
      selected_app_controls: selected.length,
      unresolved_connection_dialogs: unresolvedDialogs,
      safe_to_dispatch: selected.length === 0 && unresolvedDialogs === 0,
    };
  }

  function scan() {
    state.scans += 1;
    clearComposerIntegrationChips();
    let blocked = 0;
    for (const root of candidateRoots()) {
      const match = classify(root);
      if (match && dismissRoot(root, match)) blocked += 1;
    }
    return blocked;
  }

  function schedule(delay = 50) {
    clearTimeout(scanTimer);
    scanTimer = setTimeout(scan, Math.max(0, Number(delay || 0)));
  }

  function preflight(sendResponse) {
    const blocked = scan();
    setTimeout(() => {
      const surfaces = inspectSurfaces();
      state.preflight_checks += 1;
      state.safe_to_dispatch = surfaces.safe_to_dispatch;
      state.selected_app_controls = surfaces.selected_app_controls;
      state.unresolved_connection_dialogs = surfaces.unresolved_connection_dialogs;
      state.last_preflight_at_ms = Date.now();
      if (!surfaces.safe_to_dispatch) {
        report("unresolved-native-app-surface", "fail-closed", JSON.stringify(surfaces)).catch(() => {});
      }
      sendResponse({
        ok: true,
        version: 49,
        external_account_tools_disabled: true,
        native_github_tool_disabled: true,
        blocked,
        removed_app_chips: state.removed_app_chips,
        ...surfaces,
      });
    }, 80);
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type === "chat2api.tool-isolation.preflight") {
      preflight(sendResponse);
      return true;
    }
    if (message?.type === "chat2api.tool-isolation.status") {
      sendResponse({ ok: true, ...state });
      return true;
    }
    return false;
  });

  document.addEventListener("pointerdown", preventPositiveAction, true);
  document.addEventListener("click", preventPositiveAction, true);
  const observer = new MutationObserver(() => schedule(30));
  observer.observe(document.documentElement, {
    childList: true, subtree: true, attributes: true,
    attributeFilter: ["role", "aria-modal", "data-state", "class", "style", "aria-pressed", "aria-checked"],
  });
  setInterval(scan, 1000);
  scan();

  globalThis[KEY] = Object.freeze({ version: 49, state, scan, inspectSurfaces });
})();