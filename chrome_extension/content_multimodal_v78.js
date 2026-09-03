(() => {
  const KEY = "__CHAT2API_MULTIMODAL_V4__";
  const existing = globalThis[KEY];
  if (Number(existing?.revision || 0) >= 78) return;
  // v78 is loaded before legacy v68/v4 by the manifest. If a stale page already
  // owns the v4 channel, runtime preflight forces a document reload instead of
  // hot-swapping listeners and risking two attachment owners.
  if (existing) return;

  const REVISION = 78;
  const CONTROLLER = "multimodal-main-world-v78";
  const MAIN_REQUEST = "chat2api.multimodal.main.request.v78";
  const MAIN_RESPONSE = "chat2api.multimodal.main.response.v78";
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const v3 = globalThis.__CHAT2API_MULTIMODAL_V3__;
  const state = { uploadEvents: [], duplicateEvents: [], mainBridgeFailures: [] };

  const MIME_BY_EXT = new Map([
    [".png", "image/png"], [".jpg", "image/jpeg"], [".jpeg", "image/jpeg"],
    [".webp", "image/webp"], [".gif", "image/gif"], [".bmp", "image/bmp"],
    [".svg", "image/svg+xml"], [".pdf", "application/pdf"], [".txt", "text/plain"],
    [".csv", "text/csv"], [".json", "application/json"], [".md", "text/markdown"],
  ]);

  function inferMime(name, value) {
    const raw = String(value || "").trim().toLowerCase();
    if (raw && raw !== "application/octet-stream") return raw;
    const match = String(name || "").toLowerCase().match(/(\.[a-z0-9]+)$/);
    return MIME_BY_EXT.get(match?.[1] || "") || raw || "application/octet-stream";
  }

  function decode(base64) {
    const raw = atob(String(base64 || ""));
    const bytes = new Uint8Array(raw.length);
    for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
    return bytes;
  }

  async function fetchAttachment(spec) {
    const response = await chrome.runtime.sendMessage({ type: "chat2api.attachment.fetch", fileId: spec.file_id });
    if (!response?.ok) throw new Error(response?.error || `Unable to download ${spec.file_id}`);
    const data = response.data || {};
    const name = String(data.filename || spec.filename || spec.file_id || "attachment");
    const mime = inferMime(name, data.mime_type || spec.mime_type);
    const base64 = String(data.base64 || "");
    return {
      file: new File([decode(base64)], name, { type: mime, lastModified: Date.now() }),
      bridge: { filename: name, mime_type: mime, base64, last_modified: Date.now() },
    };
  }

  function visible(el) {
    if (!el || typeof el.getBoundingClientRect !== "function") return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) !== 0;
  }

  const textOf = el => String(el?.innerText || el?.textContent || "").replace(/\s+/g, " ").trim();

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || null;
  }

  function attachmentRoots() {
    const root = composerRoot();
    if (!root) return [];
    const roots = [];
    const seen = new Set();
    let node = root;
    for (let depth = 0; node && depth < 6; depth += 1, node = node.parentElement) {
      if (!seen.has(node)) { seen.add(node); roots.push(node); }
    }
    for (const selector of ["[data-testid*='composer']", "[class*='composer']"]) {
      const match = root.closest(selector);
      if (match && !seen.has(match)) { seen.add(match); roots.push(match); }
    }
    return roots;
  }

  const CHIP_SELECTOR = [
    "[data-testid*='attachment']", "[data-testid*='file-chip']", "[data-testid*='file-preview']",
    "[data-testid*='upload-preview']", "[data-testid*='thumbnail']", "[aria-label*='Remove file']",
    "[aria-label*='Remove attachment']", "[aria-label*='删除文件']", "[aria-label*='移除附件']",
    "[class*='attachment'][class*='preview']", "[class*='file'][class*='preview']",
  ].join(",");

  function uniqueNodes(roots, selector) {
    const result = [];
    const seen = new Set();
    for (const root of roots) {
      for (const node of root.querySelectorAll(selector)) {
        if (!seen.has(node)) { seen.add(node); result.push(node); }
      }
    }
    return result;
  }

  function evidence() {
    const roots = attachmentRoots();
    if (!roots.length) return { chips: 0, media: 0, text: "", roots: 0 };
    const chips = uniqueNodes(roots, CHIP_SELECTOR).filter(visible);
    const media = uniqueNodes(roots, "img,video").filter(node => {
      if (!visible(node)) return false;
      const rect = node.getBoundingClientRect();
      if (rect.width < 32 || rect.height < 32) return false;
      const label = `${node.alt || ""} ${node.getAttribute?.("aria-label") || ""} ${node.currentSrc || node.src || ""}`;
      return !/avatar|emoji|icon|logo/i.test(label);
    });
    const text = roots.map(textOf).filter(Boolean).sort((a, b) => b.length - a.length)[0] || "";
    return { chips: chips.length, media: media.length, text, roots: roots.length };
  }

  function fileVisible(file, before) {
    const now = evidence();
    const name = String(file.name || "");
    const stem = name.replace(/\.[^.]+$/, "");
    if (name && now.text.includes(name)) return { ok: true, reason: "filename", now };
    if (stem.length >= 5 && now.text.includes(stem)) return { ok: true, reason: "filename-stem", now };
    if (now.chips > before.chips) return { ok: true, reason: "attachment-chip-count", now };
    if (/^(image|video)\//.test(String(file.type || "")) && now.media > before.media) return { ok: true, reason: "visual-preview-count", now };
    return { ok: false, reason: "", now };
  }

  function uploadBusy() {
    const roots = attachmentRoots();
    const progress = uniqueNodes(roots, "[role='progressbar'],[data-testid*='upload-progress'],[aria-label*='Uploading'],[aria-label*='正在上传']");
    if (progress.some(visible)) return true;
    return /(?:uploading|processing file|正在上传|正在处理文件)[…\.\s]*$/i.test(roots.map(textOf).join(" ").slice(-220));
  }

  function uploadErrorFor(name) {
    const stem = String(name || "").replace(/\.[^.]+$/, "");
    const alerts = [...document.querySelectorAll("[role='alert'],[data-sonner-toast],[data-toast],[class*='toast']")];
    for (const node of alerts.slice(-20)) {
      if (!visible(node)) continue;
      const text = textOf(node);
      if (/(无法上传|上传失败|文件处理失败|failed to upload|couldn.?t upload|cannot upload|upload failed)/i.test(text)
          && (!name || text.includes(name) || (stem && text.includes(stem)))) return text.slice(0, 500);
    }
    return "";
  }

  function duplicateDialog() {
    for (const dialog of [...document.querySelectorAll("[role='dialog'],[aria-modal='true']")].filter(visible).slice(-8)) {
      const text = textOf(dialog);
      if (!/(你已上传过此文件|已经上传过此文件|you(?:'|’)ve already uploaded this file|already uploaded this file)/i.test(text)) continue;
      const button = [...dialog.querySelectorAll("button")].find(node => visible(node) && !node.disabled && /确定|知道了|ok|okay|got it|confirm/i.test(`${node.getAttribute?.("aria-label") || ""} ${textOf(node)}`));
      return { dialog, text: text.slice(0, 500), button };
    }
    return null;
  }

  async function closeDuplicate(info) {
    if (!info) return false;
    state.duplicateEvents.push({ at: Date.now(), text: info.text });
    if (state.duplicateEvents.length > 30) state.duplicateEvents.splice(0, 15);
    if (!info.button) return false;
    info.button.click();
    await delay(250);
    return true;
  }

  function mutationTracker(file) {
    const target = document.body || document.documentElement;
    const name = String(file.name || "");
    const stem = name.replace(/\.[^.]+$/, "");
    const tracker = { total: 0, strong: 0, named: 0, media: 0, started_at_ms: Date.now() };
    if (!target || typeof MutationObserver !== "function") return { tracker, stop() {} };
    const observer = new MutationObserver(records => {
      for (const record of records) for (const node of record.addedNodes || []) {
        if (!(node instanceof Element)) continue;
        tracker.total += 1;
        if (node.matches?.(CHIP_SELECTOR) || node.querySelector?.(CHIP_SELECTOR)) tracker.strong += 1;
        if (node.matches?.("img,video") || node.querySelector?.("img,video")) tracker.media += 1;
        const text = textOf(node);
        if ((name && text.includes(name)) || (stem.length >= 5 && text.includes(stem))) tracker.named += 1;
      }
    });
    observer.observe(target, { childList: true, subtree: true });
    return { tracker, stop: () => observer.disconnect() };
  }

  function hasMutation(tracker) {
    return tracker.strong > 0 || tracker.named > 0 || tracker.media > 0;
  }

  async function mainAttempt(strategy, bridge) {
    const requestId = `mm78_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        window.removeEventListener("message", onMessage);
        reject(new Error(`MAIN-world ${strategy} bridge timed out`));
      }, 10000);
      function onMessage(event) {
        if (event.source !== window) return;
        const message = event.data;
        if (!message || message.type !== MAIN_RESPONSE || message.request_id !== requestId) return;
        clearTimeout(timer);
        window.removeEventListener("message", onMessage);
        if (message.ok) resolve(message.data || {});
        else reject(new Error(message.error || `MAIN-world ${strategy} failed`));
      }
      window.addEventListener("message", onMessage);
      window.postMessage({
        type: MAIN_REQUEST,
        revision: REVISION,
        request_id: requestId,
        payload: { ...bridge, strategy },
      }, "*");
    });
  }

  async function waitForUpload(file, before, tracker, timeoutMs, strongSignal = false) {
    const deadline = Date.now() + timeoutMs;
    let duplicate = false;
    let duplicateClosed = false;
    let lastSeen = before;
    while (Date.now() < deadline) {
      const dup = duplicateDialog();
      if (dup) {
        duplicate = true;
        duplicateClosed = await closeDuplicate(dup) || duplicateClosed;
      }
      const error = uploadErrorFor(file.name);
      if (error) throw new Error(`${file.name}: ${error}`);
      const seen = fileVisible(file, before);
      lastSeen = seen.now;
      if (seen.ok) {
        await delay(550);
        const late = uploadErrorFor(file.name);
        if (late) throw new Error(`${file.name}: ${late}`);
        return { ok: true, reason: seen.reason, lastSeen, duplicate, duplicateClosed, signal: true };
      }
      if (!strongSignal && (uploadBusy() || hasMutation(tracker))) {
        return { ok: false, pending: true, reason: uploadBusy() ? "upload-busy" : "dom-mutation", lastSeen, duplicate, duplicateClosed, signal: true };
      }
      await delay(150);
    }
    return { ok: false, pending: false, reason: "no-confirmation", lastSeen, duplicate, duplicateClosed, signal: strongSignal || uploadBusy() || hasMutation(tracker) };
  }

  function finalize(file, before, after, tracker, attempts, reason, duplicate, duplicateClosed) {
    const last = attempts[attempts.length - 1] || {};
    const result = {
      file,
      input_id: last.input_id || null,
      attempts: attempts.length,
      attempt_history: attempts,
      upload_strategy: last.strategy || "unknown",
      verify_reason: reason,
      evidence_before: { chips: before.chips, media: before.media },
      evidence_after: { chips: after.chips, media: after.media },
      mutation_evidence: { ...tracker },
      input_consumed: last.input_consumed ?? null,
      duplicate_upload_dialog: duplicate,
      duplicate_dialog_auto_closed: duplicateClosed,
      duplicate_upload_recovered: duplicate,
      main_world_bridge: true,
    };
    state.uploadEvents.push({ at: Date.now(), name: file.name, strategy: result.upload_strategy, reason, attempts: attempts.length });
    if (state.uploadEvents.length > 40) state.uploadEvents.splice(0, 20);
    return result;
  }

  async function runStrategy(strategy, item, before, tracker, attempts, initialWaitMs) {
    let bridgeResult;
    try {
      bridgeResult = await mainAttempt(strategy, item.bridge);
    } catch (error) {
      state.mainBridgeFailures.push({ at: Date.now(), strategy, error: String(error?.message || error) });
      if (state.mainBridgeFailures.length > 30) state.mainBridgeFailures.splice(0, 15);
      attempts.push({ strategy: `main-${strategy}`, source: "bridge-error", error: String(error?.message || error) });
      return { ok: false, signal: false, lastSeen: evidence(), duplicate: false, duplicateClosed: false };
    }
    const attempt = {
      strategy: `main-${strategy}`,
      source: bridgeResult.source || "main-world",
      input_id: bridgeResult.input_id || null,
      input_consumed: bridgeResult.input_consumed ?? null,
    };
    attempts.push(attempt);
    let result = await waitForUpload(item.file, before, tracker, initialWaitMs, false);
    if (!result.ok && (result.pending || bridgeResult.input_consumed === true)) {
      result = await waitForUpload(item.file, before, tracker, Math.max(12000, 45000 - initialWaitMs), true);
    }
    return result;
  }

  async function uploadOne(item) {
    const file = item.file;
    const before = evidence();
    const trackerHandle = mutationTracker(file);
    const tracker = trackerHandle.tracker;
    const attempts = [];
    let duplicate = false;
    let duplicateClosed = false;
    let lastSeen = before;
    try {
      const strategies = ["file-input"];
      if (String(file.type || "").startsWith("image/")) strategies.push("composer-paste");
      strategies.push("composer-drop");
      for (const strategy of strategies) {
        const result = await runStrategy(strategy, item, before, tracker, attempts, strategy === "file-input" ? 5000 : 6500);
        duplicate ||= Boolean(result.duplicate);
        duplicateClosed ||= Boolean(result.duplicateClosed);
        lastSeen = result.lastSeen || lastSeen;
        if (result.ok) return finalize(file, before, lastSeen, tracker, attempts, result.reason, duplicate, duplicateClosed);
        // Once ChatGPT has emitted any upload signal, never inject the same file a
        // second time. WaitForUpload already consumed the long settle window.
        if (result.signal) break;
      }
    } finally {
      trackerHandle.stop();
    }
    throw new Error(
      `${file.name}: ChatGPT did not confirm that the attachment finished uploading via MAIN-world bridge after ${attempts.map(item => item.strategy).join(" -> ")}; ` +
      `chips ${before.chips}->${lastSeen.chips}, previews ${before.media}->${lastSeen.media}, ` +
      `mutations=${tracker.strong}/${tracker.named}/${tracker.media}`
    );
  }

  async function attach(specs = []) {
    const started = performance.now();
    if (!Array.isArray(specs) || !specs.length) return { attachments_count: 0, attachment_prepare_ms: 0 };
    if (specs.length > 4) throw new Error("A maximum of 4 attachments is supported per request");
    const items = [];
    for (const spec of specs) items.push(await fetchAttachment(spec));
    const uploaded = [];
    for (const item of items) {
      uploaded.push(await uploadOne(item));
      await delay(250);
    }
    return {
      attachments_controller: CONTROLLER,
      attachments_revision: REVISION,
      attachments_count: uploaded.length,
      attachment_names: uploaded.map(item => item.file.name),
      attachment_bytes: uploaded.reduce((sum, item) => sum + item.file.size, 0),
      attachment_prepare_ms: Math.round((performance.now() - started) * 10) / 10,
      attachment_input_ids: uploaded.map(item => item.input_id),
      attachment_attempts: uploaded.map(item => ({
        name: item.file.name,
        attempts: item.attempts,
        attempt_history: item.attempt_history,
        upload_strategy: item.upload_strategy,
        verify_reason: item.verify_reason,
        input_consumed: item.input_consumed,
        mutation_evidence: item.mutation_evidence,
        main_world_bridge: item.main_world_bridge,
      })),
      attachment_verified: true,
      attachment_verification_revision: REVISION,
      duplicate_upload_dialog: uploaded.some(item => item.duplicate_upload_dialog),
      duplicate_dialog_auto_closed: uploaded.some(item => item.duplicate_dialog_auto_closed),
      duplicate_upload_recovered: uploaded.some(item => item.duplicate_upload_recovered),
      attachment_retry_suppressed_after_signal: true,
      attachment_main_world_bridge_v78: true,
    };
  }

  async function cleanup(names = []) {
    if (typeof v3?.removeAttachmentsByName === "function") return v3.removeAttachmentsByName(names);
    return { removed: 0, requested: Array.isArray(names) ? names.length : 0 };
  }

  const listener = (message, _sender, sendResponse) => {
    if (message.type === "chat2api.attach.ping.v4") {
      sendResponse({ ok: true, controller: CONTROLLER, revision: REVISION });
      return false;
    }
    if (message.type === "chat2api.attach.prepare.v4") {
      attach(message.attachments || [])
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

  globalThis[KEY] = { attach, cleanup, evidence, state, revision: REVISION, controller: CONTROLLER, listener };
  chrome.runtime.onMessage.addListener(listener);
})();
