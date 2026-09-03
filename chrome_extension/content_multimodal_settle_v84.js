(() => {
  const KEY = "__CHAT2API_MULTIMODAL_V4__";
  const prior = globalThis[KEY];
  if (!prior || Number(prior.revision || 0) < 78 || Number(prior.revision || 0) >= 84) return;

  const REVISION = 84;
  const CONTROLLER = "multimodal-upload-ready-v84";
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const state = prior.state || {};
  state.readyGate = state.readyGate || {
    checks: 0,
    waits: 0,
    busy_seen: 0,
    timeouts: 0,
    last: null,
  };

  function visible(element) {
    if (!element || typeof element.getBoundingClientRect !== "function") return false;
    try {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) !== 0;
    } catch (_) {
      return false;
    }
  }

  const textOf = element => String(element?.innerText || element?.textContent || "").replace(/\s+/g, " ").trim();

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || null;
  }

  const REMOVE_SELECTOR = [
    "[aria-label*='Remove file']", "[aria-label*='Remove attachment']",
    "[aria-label*='移除文件']", "[aria-label*='移除附件']", "[aria-label*='删除文件']",
  ].join(",");
  const BUSY_SELECTOR = [
    "[role='progressbar']",
    "[aria-busy='true']",
    "[data-state='loading']", "[data-state='uploading']", "[data-state='processing']",
    "[data-testid*='progress']", "[data-testid*='spinner']", "[data-testid*='loading']", "[data-testid*='uploading']",
    "[class*='animate-spin']", "[class*='spinner']", "[class*='loading']", "[class*='progress']",
  ].join(",");

  function unique(elements) {
    return [...new Set((elements || []).filter(Boolean))];
  }

  function attachmentContainers() {
    const root = composerRoot();
    if (!root) return [];
    const containers = [];

    const addContainer = seed => {
      if (!seed || !root.contains(seed)) return;
      let node = seed;
      let fallback = seed.parentElement;
      for (let depth = 0; node && node !== root && depth < 7; depth += 1, node = node.parentElement) {
        const hasMedia = Boolean(node.querySelector?.("img,video"));
        const hasRemove = Boolean(node.matches?.(REMOVE_SELECTOR) || node.querySelector?.(REMOVE_SELECTOR));
        if (hasMedia && hasRemove) {
          containers.push(node);
          return;
        }
        if (hasMedia) fallback = node;
      }
      if (fallback && root.contains(fallback)) containers.push(fallback);
    };

    root.querySelectorAll(REMOVE_SELECTOR).forEach(addContainer);
    root.querySelectorAll("img,video").forEach(media => {
      if (!visible(media)) return;
      const rect = media.getBoundingClientRect();
      if (rect.width < 32 || rect.height < 32) return;
      const label = `${media.alt || ""} ${media.getAttribute?.("aria-label") || ""} ${media.currentSrc || media.src || ""}`;
      if (/avatar|emoji|icon|logo/i.test(label)) return;
      addContainer(media);
    });
    return unique(containers);
  }

  function animatedBusy(container) {
    if (!container) return null;
    const nodes = unique([container, ...container.querySelectorAll("div,span,svg,circle,path")]);
    for (const node of nodes) {
      if (!visible(node)) continue;
      let style;
      try { style = getComputedStyle(node); } catch (_) { continue; }
      const name = String(style.animationName || "").trim().toLowerCase();
      const duration = String(style.animationDuration || "").trim();
      if (!name || name === "none" || duration === "0s" || duration === "0ms") continue;
      const rect = node.getBoundingClientRect();
      if (rect.width < 6 || rect.height < 6 || rect.width > 120 || rect.height > 120) continue;
      const label = `${node.className || ""} ${node.getAttribute?.("aria-label") || ""} ${node.getAttribute?.("data-testid") || ""}`;
      if (/remove|close|delete|移除|删除/i.test(label)) continue;
      return { reason: "animated-upload-indicator", label: String(label).slice(0, 180) };
    }
    return null;
  }

  function busySnapshot() {
    state.readyGate.checks += 1;
    const containers = attachmentContainers();
    const root = composerRoot();
    const evidence = typeof prior.evidence === "function" ? prior.evidence() : { chips: 0, media: 0 };
    const visibleAttachments = Math.max(Number(evidence?.chips || 0), Number(evidence?.media || 0), containers.length);

    for (const container of containers) {
      const semantic = unique([
        ...(container.matches?.(BUSY_SELECTOR) ? [container] : []),
        ...container.querySelectorAll(BUSY_SELECTOR),
      ]).find(visible);
      if (semantic) {
        return {
          busy: true,
          reason: "semantic-upload-indicator",
          visible_attachments: visibleAttachments,
          marker: `${semantic.getAttribute?.("data-testid") || ""} ${semantic.getAttribute?.("aria-label") || ""} ${semantic.className || ""}`.trim().slice(0, 220),
        };
      }
      const animated = animatedBusy(container);
      if (animated) return { busy: true, visible_attachments: visibleAttachments, ...animated };
      const text = textOf(container);
      if (/(uploading|processing file|processing image|正在上传|正在处理|上传中|处理中)/i.test(text)) {
        return { busy: true, reason: "attachment-busy-text", visible_attachments: visibleAttachments, marker: text.slice(0, 220) };
      }
    }

    // Keep the legacy document-level checks as a fallback, but only while an
    // attachment is actually present so unrelated page spinners cannot block a request.
    if (visibleAttachments > 0 && root) {
      const fallback = [...root.querySelectorAll("[role='progressbar'],[aria-busy='true'],[aria-label*='Uploading'],[aria-label*='正在上传']")].find(visible);
      if (fallback) {
        return {
          busy: true,
          reason: "composer-upload-indicator",
          visible_attachments: visibleAttachments,
          marker: `${fallback.getAttribute?.("aria-label") || ""} ${fallback.className || ""}`.trim().slice(0, 220),
        };
      }
    }
    return { busy: false, reason: "ready", visible_attachments: visibleAttachments, marker: "" };
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
            attachment_ready_gate_revision: REVISION,
            attachment_ready_stage: stage,
            ...extra,
          },
        },
      });
    } catch (_) {}
  }

  async function waitForReady(expectedCount = 1, options = {}) {
    const timeoutMs = Math.max(5000, Number(options.timeout_ms || 90000));
    const stableMs = Math.max(500, Number(options.stable_ms || 1800));
    const requestId = String(options.request_id || "");
    const deadline = Date.now() + timeoutMs;
    let stableSince = 0;
    let busySeen = false;
    let last = busySnapshot();
    state.readyGate.waits += 1;

    await emitDiagnostic(requestId, "waiting", {
      attachment_expected_count: Number(expectedCount || 0),
      attachment_visible_count: last.visible_attachments,
      attachment_busy: last.busy,
      attachment_busy_reason: last.reason,
    });

    while (Date.now() < deadline) {
      last = busySnapshot();
      if (last.busy) {
        busySeen = true;
        state.readyGate.busy_seen += 1;
      }
      const present = last.visible_attachments >= Math.max(1, Number(expectedCount || 1));
      if (!present || last.busy) {
        stableSince = 0;
        await delay(120);
        continue;
      }
      if (!stableSince) stableSince = Date.now();
      if (Date.now() - stableSince >= stableMs) {
        const result = {
          ok: true,
          revision: REVISION,
          busy_seen: busySeen,
          ready_stable_ms: Date.now() - stableSince,
          visible_attachments: last.visible_attachments,
          reason: last.reason,
          waited_ms: timeoutMs - Math.max(0, deadline - Date.now()),
        };
        state.readyGate.last = { ...result, at_ms: Date.now() };
        await emitDiagnostic(requestId, "ready", {
          attachment_expected_count: Number(expectedCount || 0),
          attachment_visible_count: result.visible_attachments,
          attachment_busy_seen: result.busy_seen,
          attachment_ready_stable_ms: result.ready_stable_ms,
          attachment_ready_wait_ms: result.waited_ms,
        });
        return result;
      }
      await delay(120);
    }

    state.readyGate.timeouts += 1;
    state.readyGate.last = { ok: false, revision: REVISION, ...last, at_ms: Date.now() };
    await emitDiagnostic(requestId, "timeout", {
      attachment_expected_count: Number(expectedCount || 0),
      attachment_visible_count: last.visible_attachments,
      attachment_busy: last.busy,
      attachment_busy_reason: last.reason,
      attachment_busy_marker: last.marker,
    });
    throw new Error(
      `ChatGPT attachment preview did not become upload-ready before timeout; ` +
      `visible=${last.visible_attachments}, expected=${Math.max(1, Number(expectedCount || 1))}, busy=${last.busy}, reason=${last.reason}`
    );
  }

  async function attach(specs = [], options = {}) {
    const data = await prior.attach(specs);
    const count = Number(data?.attachments_count || 0);
    if (!count) return { ...data, attachment_ready_gate_v84: true, attachment_ready_gate_revision: REVISION };
    const ready = await waitForReady(count, {
      request_id: options.request_id,
      timeout_ms: options.timeout_ms || 90000,
      stable_ms: options.stable_ms || 1800,
    });
    const attempts = Array.isArray(data.attachment_attempts)
      ? data.attachment_attempts.map(item => ({
          ...item,
          upload_settle_revision: REVISION,
          upload_ready_gate_revision: REVISION,
          upload_ready_busy_seen: ready.busy_seen,
          upload_ready_wait_ms: ready.waited_ms,
          upload_ready_stable_ms: ready.ready_stable_ms,
        }))
      : data.attachment_attempts;
    return {
      ...data,
      attachments_controller: CONTROLLER,
      attachments_revision: REVISION,
      attachment_verification_revision: REVISION,
      attachment_attempts: attempts,
      attachment_ready_gate_v84: true,
      attachment_ready_gate_revision: REVISION,
      attachment_ready_busy_seen: ready.busy_seen,
      attachment_ready_wait_ms: ready.waited_ms,
      attachment_ready_stable_ms: ready.ready_stable_ms,
    };
  }

  async function cleanup(names = []) {
    return prior.cleanup(names);
  }

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
    busySnapshot,
    waitForReady,
    readyGate: state.readyGate,
  };
  chrome.runtime.onMessage.addListener(listener);
})();
