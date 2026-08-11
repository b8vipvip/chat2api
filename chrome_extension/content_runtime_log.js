(() => {
  const KEY = "__CHAT2API_CONTENT_RUNTIME_LOG_V1__";
  if (globalThis[KEY]) return;

  const state = {
    activeRequestId: null,
    lastFingerprint: "",
    timer: null,
    observer: null,
  };
  globalThis[KEY] = state;

  function visible(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  const normalize = value => String(value || "").replace(/\s+/g, " ").trim();
  const labelOf = el => normalize(`${el?.dataset?.testid || ""} ${el?.getAttribute?.("aria-label") || ""} ${el?.title || ""} ${el?.innerText || el?.textContent || ""}`);

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || null;
  }

  function composer() {
    const root = composerRoot() || document;
    return [...root.querySelectorAll("#prompt-textarea,textarea,[contenteditable='true'][data-lexical-editor='true'],[contenteditable='true'].ProseMirror,[contenteditable='true']")].find(visible) || null;
  }

  function composerChars() {
    const el = composer();
    if (!el) return 0;
    const text = el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement ? el.value : (el.innerText || el.textContent || "");
    return normalize(text).length;
  }

  function buttonState() {
    const root = composerRoot() || document;
    const buttons = [...root.querySelectorAll("button")].filter(visible);
    const send = buttons.find(btn => btn.dataset?.testid === "send-button") ||
      buttons.find(btn => /send prompt|send message|发送提示|发送消息|发送$/i.test(labelOf(btn))) || null;
    return {
      send_found: Boolean(send),
      send_label: send ? labelOf(send).slice(0, 120) : "",
      send_disabled: send ? Boolean(send.disabled || send.getAttribute("aria-disabled") === "true") : null,
      composer_button_labels: buttons.map(labelOf).filter(Boolean).slice(0, 20),
    };
  }

  function attachmentState() {
    const root = composerRoot()?.parentElement || composerRoot();
    if (!root) return { attachment_chips: 0, media_previews: 0 };
    const chips = root.querySelectorAll("[data-testid*='attachment'],[data-testid*='file-chip'],[aria-label*='Remove file'],[aria-label*='删除文件']").length;
    const media = [...root.querySelectorAll("img,video")].filter(node => {
      if (!visible(node)) return false;
      const rect = node.getBoundingClientRect();
      if (rect.width < 48 || rect.height < 48) return false;
      const src = node.currentSrc || node.src || "";
      return !/avatar|emoji|icon|logo/i.test(src);
    }).length;
    return { attachment_chips: chips, media_previews: media };
  }

  function alertState() {
    const rows = [];
    const nodes = [...document.querySelectorAll("[role='dialog'],[aria-modal='true'],[role='alert'],[data-sonner-toast],[data-toast],[class*='toast']")].filter(visible).slice(-10);
    for (const node of nodes) {
      const text = normalize(node.innerText || node.textContent || "");
      if (text) rows.push(text.slice(0, 240));
    }
    return rows.slice(-6);
  }

  function pageState() {
    return {
      href: location.href,
      title: document.title,
      ready_state: document.readyState,
      visibility_state: document.visibilityState,
      has_composer: Boolean(composer()),
      composer_chars: composerChars(),
      user_messages: document.querySelectorAll("[data-message-author-role='user']").length,
      assistant_messages: document.querySelectorAll("[data-message-author-role='assistant']").length,
      generating: Boolean([...document.querySelectorAll("button")].find(btn => visible(btn) && /stop streaming|stop generating|停止生成|停止回答/i.test(labelOf(btn)))),
      ...buttonState(),
      ...attachmentState(),
      visible_alerts: alertState(),
    };
  }

  function fingerprint(snapshot) {
    return JSON.stringify({
      href: snapshot.href,
      ready_state: snapshot.ready_state,
      visibility_state: snapshot.visibility_state,
      has_composer: snapshot.has_composer,
      composer_chars: snapshot.composer_chars,
      user_messages: snapshot.user_messages,
      assistant_messages: snapshot.assistant_messages,
      generating: snapshot.generating,
      send_found: snapshot.send_found,
      send_disabled: snapshot.send_disabled,
      send_label: snapshot.send_label,
      attachment_chips: snapshot.attachment_chips,
      media_previews: snapshot.media_previews,
      visible_alerts: snapshot.visible_alerts,
    });
  }

  async function append(action, data = {}, level = "info") {
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.log.append",
        entry: {
          component: "page",
          action,
          level,
          request_id: state.activeRequestId,
          data,
        },
      });
    } catch (_) {}
  }

  function scheduleState(reason = "mutation") {
    clearTimeout(state.timer);
    state.timer = setTimeout(async () => {
      const snapshot = pageState();
      const next = fingerprint(snapshot);
      if (next === state.lastFingerprint) return;
      state.lastFingerprint = next;
      await append("page-state", { reason, ...snapshot });
    }, 180);
  }

  const activityMessages = new Set([
    "chat2api.request",
    "chat2api.request.preflight",
    "chat2api.attach.prepare",
    "chat2api.attach.prepare.v4",
    "chat2api.image.request.v2",
    "chat2api.image.request.v3",
    "chat2api.voice.request.v2",
    "chat2api.model.prepare.v5",
  ]);
  const cancelMessages = new Set([
    "chat2api.cancel",
    "chat2api.image.cancel.v2",
    "chat2api.image.cancel.v3",
    "chat2api.voice.cancel.v2",
  ]);

  chrome.runtime.onMessage.addListener(message => {
    if (activityMessages.has(message?.type)) {
      state.activeRequestId = message.requestId || message.request_id || state.activeRequestId;
      append("automation-message", {
        type: message.type,
        request_id: state.activeRequestId,
        model: message?.options?.requested_model || message?.options?.model || message?.model || null,
        attachment_count: Array.isArray(message?.attachments) ? message.attachments.length : 0,
        page: pageState(),
      }).catch(() => {});
      scheduleState("automation-message");
      return false;
    }
    if (cancelMessages.has(message?.type)) {
      append("automation-cancel", { type: message.type, request_id: message.requestId || message.request_id || state.activeRequestId, page: pageState() }, "warn").catch(() => {});
      return false;
    }
    return false;
  });

  window.addEventListener("error", event => {
    append("window-error", {
      message: event.message || "window error",
      filename: event.filename || "",
      lineno: event.lineno || 0,
      colno: event.colno || 0,
      page: pageState(),
    }, "error").catch(() => {});
  });

  window.addEventListener("unhandledrejection", event => {
    append("unhandled-rejection", {
      reason: String(event.reason?.message || event.reason || "unhandled rejection"),
      page: pageState(),
    }, "error").catch(() => {});
  });

  document.addEventListener("visibilitychange", () => scheduleState("visibilitychange"));
  window.addEventListener("popstate", () => scheduleState("popstate"));
  window.addEventListener("hashchange", () => scheduleState("hashchange"));

  state.observer = new MutationObserver(() => scheduleState("mutation"));
  state.observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "aria-disabled", "aria-label", "data-testid"] });

  append("content-runtime-ready", { extension_version: chrome.runtime.getManifest().version, page: pageState() }).catch(() => {});
  scheduleState("startup");
})();
