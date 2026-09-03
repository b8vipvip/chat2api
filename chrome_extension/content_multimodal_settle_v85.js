(() => {
  const KEY = "__CHAT2API_MULTIMODAL_V4__";
  const prior = globalThis[KEY];
  if (!prior || Number(prior.revision || 0) < 84 || Number(prior.revision || 0) >= 85) return;

  const REVISION = 85;
  const CONTROLLER = "multimodal-upload-safe-submit-v85";
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const state = prior.state || {};
  state.safeSubmitGate = state.safeSubmitGate || { waits: 0, busy_seen: 0, timeouts: 0, last: null };

  function visible(element) {
    if (!element || typeof element.getBoundingClientRect !== "function") return false;
    try {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) !== 0;
    } catch (_) { return false; }
  }

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || null;
  }

  const REMOVE_SELECTOR = [
    "[aria-label*='Remove file']", "[aria-label*='Remove attachment']",
    "[aria-label*='移除文件']", "[aria-label*='移除附件']", "[aria-label*='删除文件']",
  ].join(",");

  function unique(rows) { return [...new Set((rows || []).filter(Boolean))]; }

  function attachmentContainers() {
    const root = composerRoot();
    if (!root) return [];
    const result = [];
    const add = seed => {
      if (!seed || !root.contains(seed)) return;
      let node = seed;
      let fallback = seed.parentElement;
      for (let depth = 0; node && node !== root && depth < 8; depth += 1, node = node.parentElement) {
        const hasMedia = Boolean(node.matches?.("img,video") || node.querySelector?.("img,video"));
        const hasRemove = Boolean(node.matches?.(REMOVE_SELECTOR) || node.querySelector?.(REMOVE_SELECTOR));
        if (hasMedia && hasRemove) { result.push(node); return; }
        if (hasMedia) fallback = node;
      }
      if (fallback && root.contains(fallback)) result.push(fallback);
    };
    root.querySelectorAll(REMOVE_SELECTOR).forEach(add);
    root.querySelectorAll("img,video").forEach(media => {
      if (!visible(media)) return;
      const rect = media.getBoundingClientRect();
      if (rect.width < 32 || rect.height < 32) return;
      const label = `${media.alt || ""} ${media.getAttribute?.("aria-label") || ""} ${media.currentSrc || media.src || ""}`;
      if (/avatar|emoji|icon|logo/i.test(label)) return;
      add(media);
    });
    return unique(result);
  }

  function animatedStyle(node, pseudo = null) {
    let style;
    try { style = getComputedStyle(node, pseudo); } catch (_) { return null; }
    const name = String(style.animationName || "").trim().toLowerCase();
    const duration = String(style.animationDuration || "").trim();
    const iterations = String(style.animationIterationCount || "").trim().toLowerCase();
    if (!name || name === "none" || duration === "0s" || duration === "0ms") return null;
    return { name, duration, iterations, pseudo: pseudo || "element" };
  }

  function visualBusySnapshot() {
    const base = typeof prior.busySnapshot === "function" ? prior.busySnapshot() : null;
    if (base?.busy) return { ...base, source: "v84" };
    const evidence = typeof prior.evidence === "function" ? prior.evidence() : { chips: 0, media: 0 };
    const containers = attachmentContainers();
    const visibleAttachments = Math.max(Number(evidence?.chips || 0), Number(evidence?.media || 0), containers.length);
    for (const container of containers) {
      const svgAnimator = container.querySelector("animate,animateTransform,animateMotion");
      if (svgAnimator) return { busy: true, reason: "svg-animation", visible_attachments: visibleAttachments, marker: svgAnimator.tagName };
      const nodes = [container, ...container.querySelectorAll("*")].slice(0, 360);
      for (const node of nodes) {
        if (!visible(node)) continue;
        const rect = node.getBoundingClientRect();
        if (rect.width < 5 || rect.height < 5 || rect.width > 160 || rect.height > 160) continue;
        const label = `${node.className || ""} ${node.getAttribute?.("aria-label") || ""} ${node.getAttribute?.("data-testid") || ""}`;
        if (/remove|close|delete|移除|删除/i.test(label)) continue;
        for (const pseudo of [null, "::before", "::after"]) {
          const animation = animatedStyle(node, pseudo);
          if (!animation) continue;
          return {
            busy: true,
            reason: pseudo ? "pseudo-element-animation" : "element-animation",
            visible_attachments: visibleAttachments,
            marker: `${animation.pseudo}:${animation.name}:${animation.duration}:${label}`.slice(0, 240),
          };
        }
      }
    }
    return { busy: false, reason: "quiet", visible_attachments: visibleAttachments, marker: "" };
  }

  function safetyGraceMs(bytes) {
    const size = Math.max(0, Number(bytes || 0));
    if (size <= 512 * 1024) return 8000;
    if (size <= 2 * 1024 * 1024) return 12000;
    return 18000;
  }

  async function emitDiagnostic(requestId, stage, extra = {}) {
    if (!requestId) return;
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "chat.diagnostics",
          request_id: requestId,
          diagnostics: {
            attachment_safe_submit_revision: REVISION,
            attachment_safe_submit_stage: stage,
            ...extra,
          },
        },
      });
    } catch (_) {}
  }

  async function waitForSafeSubmit(expectedCount, bytes, options = {}) {
    const requestId = String(options.request_id || "");
    const graceMs = Math.max(3000, Number(options.grace_ms || safetyGraceMs(bytes)));
    const timeoutMs = Math.max(90000, graceMs + 30000, Number(options.timeout_ms || 0));
    const deadline = Date.now() + timeoutMs;
    let quietSince = 0;
    let busySeen = false;
    let last = visualBusySnapshot();
    state.safeSubmitGate.waits += 1;
    await emitDiagnostic(requestId, "guarding", {
      attachment_expected_count: Number(expectedCount || 0),
      attachment_bytes: Number(bytes || 0),
      attachment_safe_grace_ms: graceMs,
      attachment_busy: Boolean(last.busy),
      attachment_busy_reason: last.reason,
    });
    while (Date.now() < deadline) {
      last = visualBusySnapshot();
      const present = last.visible_attachments >= Math.max(1, Number(expectedCount || 1));
      if (!present || last.busy) {
        if (last.busy) {
          busySeen = true;
          state.safeSubmitGate.busy_seen += 1;
        }
        quietSince = 0;
        await delay(160);
        continue;
      }
      if (!quietSince) quietSince = Date.now();
      if (Date.now() - quietSince >= graceMs) {
        const result = {
          ok: true,
          revision: REVISION,
          busy_seen: busySeen,
          grace_ms: graceMs,
          quiet_ms: Date.now() - quietSince,
          visible_attachments: last.visible_attachments,
          waited_ms: timeoutMs - Math.max(0, deadline - Date.now()),
        };
        state.safeSubmitGate.last = { ...result, at_ms: Date.now() };
        await emitDiagnostic(requestId, "ready", {
          attachment_visible_count: result.visible_attachments,
          attachment_safe_busy_seen: result.busy_seen,
          attachment_safe_grace_ms: result.grace_ms,
          attachment_safe_quiet_ms: result.quiet_ms,
          attachment_safe_wait_ms: result.waited_ms,
        });
        return result;
      }
      await delay(160);
    }
    state.safeSubmitGate.timeouts += 1;
    state.safeSubmitGate.last = { ok: false, revision: REVISION, ...last, at_ms: Date.now() };
    await emitDiagnostic(requestId, "timeout", {
      attachment_visible_count: last.visible_attachments,
      attachment_busy: Boolean(last.busy),
      attachment_busy_reason: last.reason,
      attachment_busy_marker: last.marker,
      attachment_safe_grace_ms: graceMs,
    });
    throw new Error(`ChatGPT attachment did not remain safely upload-ready before timeout; visible=${last.visible_attachments}, busy=${Boolean(last.busy)}, reason=${last.reason}`);
  }

  async function attach(specs = [], options = {}) {
    const data = await prior.attach(specs, options);
    const count = Number(data?.attachments_count || 0);
    if (!count) return { ...data, attachment_safe_submit_v85: true, attachment_safe_submit_revision: REVISION };
    const bytes = Number(data?.attachment_bytes || 0);
    const safe = await waitForSafeSubmit(count, bytes, {
      request_id: options.request_id,
      timeout_ms: options.timeout_ms || 0,
    });
    const attempts = Array.isArray(data.attachment_attempts)
      ? data.attachment_attempts.map(item => ({
          ...item,
          upload_settle_revision: REVISION,
          upload_safe_submit_revision: REVISION,
          upload_safe_busy_seen: safe.busy_seen,
          upload_safe_grace_ms: safe.grace_ms,
          upload_safe_wait_ms: safe.waited_ms,
        }))
      : data.attachment_attempts;
    return {
      ...data,
      attachments_controller: CONTROLLER,
      attachments_revision: REVISION,
      attachment_verification_revision: REVISION,
      attachment_attempts: attempts,
      attachment_safe_submit_v85: true,
      attachment_safe_submit_revision: REVISION,
      attachment_safe_busy_seen: safe.busy_seen,
      attachment_safe_grace_ms: safe.grace_ms,
      attachment_safe_wait_ms: safe.waited_ms,
      attachment_safe_quiet_ms: safe.quiet_ms,
    };
  }

  async function cleanup(names = []) { return prior.cleanup(names); }

  if (typeof prior.listener === "function") {
    try { chrome.runtime.onMessage.removeListener(prior.listener); } catch (_) {}
  }

  const listener = (message, _sender, sendResponse) => {
    if (message.type === "chat2api.attach.ping.v4") {
      sendResponse({ ok: true, controller: CONTROLLER, revision: REVISION });
      return false;
    }
    if (message.type === "chat2api.attach.prepare.v4") {
      attach(message.attachments || [], { request_id: message.request_id })
        .then(data => sendResponse({ ok: true, data, controller: CONTROLLER, revision: REVISION }))
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error), controller: CONTROLLER, revision: REVISION }));
      return true;
    }
    if (message.type === "chat2api.attach.cleanup.v4") {
      cleanup(message.names || [])
        .then(data => sendResponse({ ok: true, data, controller: CONTROLLER, revision: REVISION }))
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error), controller: CONTROLLER, revision: REVISION }));
      return true;
    }
    return false;
  };

  globalThis[KEY] = {
    ...prior,
    attach,
    cleanup,
    revision: REVISION,
    controller: CONTROLLER,
    listener,
    visualBusySnapshot,
    waitForSafeSubmit,
    safeSubmitGate: state.safeSubmitGate,
  };
  chrome.runtime.onMessage.addListener(listener);
})();
