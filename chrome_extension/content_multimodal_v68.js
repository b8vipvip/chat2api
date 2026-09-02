(() => {
  const KEY = "__CHAT2API_MULTIMODAL_V4__";
  if (globalThis[KEY]) return;

  const CONTROLLER = "multimodal-v4-r68";
  const REVISION = 68;
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const state = { duplicateEvents: [], uploadEvents: [] };
  const v3 = globalThis.__CHAT2API_MULTIMODAL_V3__;

  function decode(base64) {
    const raw = atob(base64);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
    return bytes;
  }

  async function fetchFile(spec) {
    const response = await chrome.runtime.sendMessage({ type: "chat2api.attachment.fetch", fileId: spec.file_id });
    if (!response?.ok) throw new Error(response?.error || `Unable to download ${spec.file_id}`);
    const data = response.data || {};
    return new File([decode(data.base64 || "")], data.filename || spec.filename || spec.file_id, {
      type: data.mime_type || spec.mime_type || "application/octet-stream",
      lastModified: Date.now(),
    });
  }

  function visible(el) {
    if (!el || typeof el.getBoundingClientRect !== "function") return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) !== 0;
  }

  const textOf = el => String(el?.innerText || el?.textContent || "").replace(/\s+/g, " ").trim();
  const labelOf = el => `${el?.dataset?.testid || ""} ${el?.getAttribute?.("aria-label") || ""} ${el?.getAttribute?.("title") || ""} ${textOf(el)}`.replace(/\s+/g, " ").trim();

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || null;
  }

  function composerEditor() {
    const root = composerRoot() || document;
    return [
      "#prompt-textarea",
      "textarea[placeholder]",
      "div[contenteditable='true'][data-lexical-editor='true']",
      "div[contenteditable='true'].ProseMirror",
      "[contenteditable='true']",
    ].flatMap(selector => [...root.querySelectorAll(selector)]).find(visible) || null;
  }

  function attachmentRoots() {
    const root = composerRoot();
    if (!root) return [];
    const roots = [];
    const seen = new Set();
    let node = root;
    for (let depth = 0; node && depth < 5; depth += 1, node = node.parentElement) {
      if (!seen.has(node)) {
        seen.add(node);
        roots.push(node);
      }
    }
    for (const selector of ["[data-testid*='composer']", "[class*='composer']"]) {
      const match = root.closest(selector);
      if (match && !seen.has(match)) {
        seen.add(match);
        roots.push(match);
      }
    }
    return roots;
  }

  function fileExtension(name) {
    const match = String(name || "").toLowerCase().match(/(\.[a-z0-9]+)$/);
    return match ? match[1] : "";
  }

  function acceptsFile(input, file) {
    const accept = String(input?.accept || "").trim().toLowerCase();
    if (!accept || accept === "*/*") return true;
    const mime = String(file.type || "").toLowerCase();
    const ext = fileExtension(file.name);
    return accept.split(",").map(value => value.trim()).filter(Boolean).some(rule => {
      if (rule === "*/*") return true;
      if (rule.startsWith(".")) return rule === ext;
      if (rule.endsWith("/*")) return mime.startsWith(rule.slice(0, -1));
      return mime === rule;
    });
  }

  function inputScore(input, file, root = null, fresh = false) {
    if (!input || input.disabled || !acceptsFile(input, file)) return -10000;
    const id = `${input.id || ""} ${input.name || ""} ${input.dataset?.testid || ""} ${input.getAttribute?.("aria-label") || ""}`.toLowerCase();
    const accept = String(input.accept || "").toLowerCase();
    const mime = String(file.type || "").toLowerCase();
    const isImage = mime.startsWith("image/");
    const isVideo = mime.startsWith("video/");
    let score = 10;
    if (root && root.contains(input)) score += 400;
    if (fresh) score += 350;
    if (!accept || accept === "*/*") score += 100;
    if (/upload-photos|upload-camera|photo|image/.test(id)) score += isImage || isVideo ? 90 : -500;
    if (/file|attachment|document|upload/.test(id)) score += 90;
    if (isImage && accept.includes("image")) score += 50;
    if (isVideo && accept.includes("video")) score += 50;
    if (!isImage && !isVideo && /pdf|text|word|sheet|presentation|json|csv|\*/.test(accept)) score += 60;
    return score;
  }

  function fileInputs() {
    return [...document.querySelectorAll("input[type='file']")];
  }

  function candidateInput(file, beforeInputs = new Set()) {
    const root = composerRoot();
    return fileInputs()
      .map(input => ({ input, score: inputScore(input, file, root, !beforeInputs.has(input)) }))
      .filter(item => item.score > 0)
      .sort((a, b) => b.score - a.score)[0]?.input || null;
  }

  function attachmentButton() {
    const root = composerRoot() || document;
    return [...root.querySelectorAll("button")].find(button => visible(button) && !button.disabled && /composer-plus|attach|attachment|add files|add photos|upload|添加文件|添加照片|附件|上传/i.test(labelOf(button))) || null;
  }

  function uploadMenuAction() {
    const selectors = ["[role='menuitem']", "[role='option']", "button", "[data-radix-menu-content] [tabindex]"];
    const candidates = [...new Set(selectors.flatMap(selector => [...document.querySelectorAll(selector)]))];
    return candidates.find(node => visible(node) && !node.disabled && /upload from computer|upload file|add photos?\s*(?:&|and)?\s*files?|photos? and files?|从计算机上传|上传文件|添加照片和文件|添加照片或文件|照片和文件/i.test(labelOf(node))) || null;
  }

  const CHIP_SELECTOR = [
    "[data-testid*='attachment']",
    "[data-testid*='file-chip']",
    "[data-testid*='file-preview']",
    "[data-testid*='upload-preview']",
    "[data-testid*='thumbnail']",
    "[aria-label*='Remove file']",
    "[aria-label*='Remove attachment']",
    "[aria-label*='删除文件']",
    "[aria-label*='移除附件']",
    "[class*='attachment'][class*='preview']",
    "[class*='file'][class*='preview']",
  ].join(",");

  function uniqueNodes(roots, selector) {
    const result = [];
    const seen = new Set();
    for (const root of roots) {
      for (const node of root.querySelectorAll(selector)) {
        if (seen.has(node)) continue;
        seen.add(node);
        result.push(node);
      }
    }
    return result;
  }

  function previewMedia(roots) {
    const local = uniqueNodes(roots, "img,video").filter(node => {
      if (!visible(node)) return false;
      const r = node.getBoundingClientRect();
      if (r.width < 32 || r.height < 32) return false;
      const src = String(node.currentSrc || node.src || "");
      const label = `${node.alt || ""} ${node.getAttribute("aria-label") || ""}`;
      return !/avatar|emoji|icon|logo/i.test(`${src} ${label}`);
    });
    const documentBlob = [...document.querySelectorAll("img[src^='blob:'],img[src^='data:'],video[src^='blob:'],video[src^='data:']")].filter(visible);
    return [...new Set([...local, ...documentBlob])];
  }

  function evidence() {
    const roots = attachmentRoots();
    if (!roots.length) return { chips: 0, media: 0, text: "", roots: 0 };
    const chips = uniqueNodes(roots, CHIP_SELECTOR).filter(visible);
    const media = previewMedia(roots);
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

  function duplicateDialog() {
    const dialogs = [...document.querySelectorAll("[role='dialog'],[aria-modal='true']")].filter(visible);
    for (const dialog of dialogs.slice(-6)) {
      const text = textOf(dialog);
      if (!/(你已上传过此文件|已经上传过此文件|you(?:'|’)ve already uploaded this file|already uploaded this file)/i.test(text)) continue;
      const button = [...dialog.querySelectorAll("button")].find(btn => visible(btn) && !btn.disabled && /确定|知道了|ok|okay|got it|confirm/i.test(labelOf(btn)));
      return { dialog, text: text.slice(0, 500), button };
    }
    return null;
  }

  async function closeDuplicate(info) {
    if (!info) return false;
    state.duplicateEvents.push({ at: Date.now(), text: info.text });
    if (state.duplicateEvents.length > 20) state.duplicateEvents.splice(0, 10);
    if (!info.button) return false;
    info.button.click();
    await delay(300);
    return true;
  }

  function uploadErrorFor(name) {
    const stem = String(name || "").replace(/\.[^.]+$/, "");
    const alerts = [...document.querySelectorAll("[role='alert'],[data-sonner-toast],[data-toast],[class*='toast']")];
    for (const node of alerts.slice(-16)) {
      if (!visible(node)) continue;
      const text = textOf(node);
      if (/(无法上传|上传失败|文件处理失败|failed to upload|couldn.?t upload|cannot upload|upload failed)/i.test(text)
          && (!name || text.includes(name) || (stem && text.includes(stem)))) return text.slice(0, 500);
    }
    return "";
  }

  function uploadBusy() {
    const roots = attachmentRoots();
    const nodes = uniqueNodes(roots, "[role='progressbar'],[data-testid*='upload-progress'],[aria-label*='Uploading'],[aria-label*='正在上传']");
    if (nodes.some(visible)) return true;
    const text = roots.map(textOf).join(" ");
    return /(?:uploading|processing file|正在上传|正在处理文件)[…\.\s]*$/i.test(text.slice(-180));
  }

  function mutationTracker(file) {
    const body = document.body || document.documentElement;
    const name = String(file.name || "");
    const stem = name.replace(/\.[^.]+$/, "");
    const tracker = { total: 0, strong: 0, named: 0, media: 0, started_at_ms: Date.now() };
    if (!body || typeof MutationObserver !== "function") return { tracker, stop() {} };
    const inspect = node => {
      if (!(node instanceof Element)) return;
      tracker.total += 1;
      const blobMedia = node.matches?.("img[src^='blob:'],img[src^='data:'],video[src^='blob:'],video[src^='data:']")
        || node.querySelector?.("img[src^='blob:'],img[src^='data:'],video[src^='blob:'],video[src^='data:']");
      if (blobMedia) tracker.media += 1;
      const strong = node.matches?.(CHIP_SELECTOR) || node.querySelector?.(CHIP_SELECTOR);
      if (strong) tracker.strong += 1;
      const text = textOf(node);
      if ((name && text.includes(name)) || (stem.length >= 5 && text.includes(stem))) tracker.named += 1;
    };
    const observer = new MutationObserver(records => {
      for (const record of records) for (const node of record.addedNodes || []) inspect(node);
    });
    observer.observe(body, { childList: true, subtree: true });
    return { tracker, stop: () => observer.disconnect() };
  }

  function inputConsumed(input) {
    if (!input?.isConnected) return true;
    try { return !input.files || input.files.length === 0; } catch (_) { return false; }
  }

  function assignInput(input, file) {
    const transfer = new DataTransfer();
    transfer.items.add(file);
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "files")?.set;
    if (setter) setter.call(input, transfer.files);
    else input.files = transfer.files;
    input.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    input.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  }

  function dispatchPaste(file) {
    if (!String(file.type || "").startsWith("image/")) return false;
    const editor = composerEditor();
    if (!editor) return false;
    const transfer = new DataTransfer();
    transfer.items.add(file);
    editor.focus();
    let event;
    try {
      event = new ClipboardEvent("paste", { bubbles: true, cancelable: true, composed: true, clipboardData: transfer });
    } catch (_) {
      event = new Event("paste", { bubbles: true, cancelable: true, composed: true });
      try { Object.defineProperty(event, "clipboardData", { value: transfer }); } catch (_) {}
    }
    return editor.dispatchEvent(event) === false || true;
  }

  function dispatchDrop(file) {
    const root = composerRoot();
    const target = root?.querySelector("[data-testid*='drop'],[class*='drop']") || root || composerEditor();
    if (!target) return false;
    const transfer = new DataTransfer();
    transfer.items.add(file);
    for (const type of ["dragenter", "dragover", "drop"]) {
      let event;
      try {
        event = new DragEvent(type, { bubbles: true, cancelable: true, composed: true, dataTransfer: transfer });
      } catch (_) {
        event = new Event(type, { bubbles: true, cancelable: true, composed: true });
        try { Object.defineProperty(event, "dataTransfer", { value: transfer }); } catch (_) {}
      }
      target.dispatchEvent(event);
    }
    return true;
  }

  async function exposeComposerInput(file) {
    const beforeInputs = new Set(fileInputs());
    let input = candidateInput(file, beforeInputs);
    const root = composerRoot();
    if (input && root?.contains(input)) return { input, source: "composer-existing" };

    const button = attachmentButton();
    if (button) button.click();
    for (let i = 0; i < 20; i += 1) {
      await delay(100);
      const duplicate = duplicateDialog();
      if (duplicate) await closeDuplicate(duplicate);
      const action = uploadMenuAction();
      if (action) {
        action.click();
        break;
      }
      input = candidateInput(file, beforeInputs);
      if (input && (!beforeInputs.has(input) || root?.contains(input))) return { input, source: !beforeInputs.has(input) ? "fresh-after-plus" : "composer-after-plus" };
    }

    for (let i = 0; i < 40; i += 1) {
      await delay(100);
      input = candidateInput(file, beforeInputs);
      if (input && (!beforeInputs.has(input) || composerRoot()?.contains(input))) {
        return { input, source: !beforeInputs.has(input) ? "fresh-after-upload-menu" : "composer-after-upload-menu" };
      }
    }
    input = candidateInput(file, beforeInputs);
    return input ? { input, source: "global-compatible-fallback" } : { input: null, source: "none" };
  }

  function mutationEvidence(tracker) {
    return tracker.strong > 0 || tracker.named > 0 || tracker.media > 0;
  }

  async function waitAttempt(file, before, attempt, tracker, timeoutMs, allowZeroSignalExit = false) {
    const deadline = Date.now() + timeoutMs;
    let duplicate = false;
    let duplicateClosed = false;
    let consumedSince = 0;
    let lastSeen = before;
    while (Date.now() < deadline) {
      const dialog = duplicateDialog();
      if (dialog) {
        duplicate = true;
        duplicateClosed = await closeDuplicate(dialog) || duplicateClosed;
      }
      const uploadError = uploadErrorFor(file.name);
      if (uploadError) throw new Error(`${file.name}: ${uploadError}`);
      const seen = fileVisible(file, before);
      lastSeen = seen.now;
      if (seen.ok) {
        await delay(650);
        const lateError = uploadErrorFor(file.name);
        if (lateError) throw new Error(`${file.name}: ${lateError}`);
        return { ok: true, reason: seen.reason, duplicate, duplicateClosed, lastSeen };
      }

      const consumed = attempt.input ? inputConsumed(attempt.input) : false;
      const mutations = mutationEvidence(tracker);
      if (consumed && mutations && !uploadBusy()) {
        if (!consumedSince) consumedSince = Date.now();
        if (Date.now() - consumedSince >= 1200) {
          return { ok: true, reason: "input-consumed-stable", duplicate, duplicateClosed, lastSeen: evidence() };
        }
      } else consumedSince = 0;

      if (allowZeroSignalExit && Date.now() + 200 < deadline && uploadBusy()) {
        // A real upload started. Do not perform another injection and risk a duplicate.
        return { ok: false, pending: true, reason: "upload-busy", duplicate, duplicateClosed, lastSeen };
      }
      await delay(160);
    }
    return {
      ok: false,
      zeroSignal: !mutationEvidence(tracker) && !uploadBusy() && (!attempt.input || !inputConsumed(attempt.input)),
      reason: "no-confirmation",
      duplicate,
      duplicateClosed,
      lastSeen,
    };
  }

  async function uploadOne(file) {
    const before = evidence();
    const trackerHandle = mutationTracker(file);
    const tracker = trackerHandle.tracker;
    const attempts = [];
    let duplicate = false;
    let duplicateClosed = false;
    let lastSeen = before;
    try {
      const exposed = await exposeComposerInput(file);
      if (exposed.input) {
        const attempt = { strategy: "file-input", source: exposed.source, input: exposed.input };
        attempts.push({ strategy: attempt.strategy, source: attempt.source, input_id: exposed.input.id || null });
        assignInput(exposed.input, file);
        const result = await waitAttempt(file, before, attempt, tracker, 4200, true);
        duplicate ||= result.duplicate;
        duplicateClosed ||= result.duplicateClosed;
        lastSeen = result.lastSeen;
        if (result.ok) return finalize(file, before, lastSeen, tracker, attempts, result.reason, exposed.input, duplicate, duplicateClosed);
        if (result.pending) {
          const settled = await waitAttempt(file, before, attempt, tracker, 40800, false);
          duplicate ||= settled.duplicate;
          duplicateClosed ||= settled.duplicateClosed;
          lastSeen = settled.lastSeen;
          if (settled.ok) return finalize(file, before, lastSeen, tracker, attempts, settled.reason, exposed.input, duplicate, duplicateClosed);
        }
      }

      if (String(file.type || "").startsWith("image/")) {
        attempts.push({ strategy: "composer-paste", source: "current-composer" });
        dispatchPaste(file);
        let result = await waitAttempt(file, before, { strategy: "composer-paste", input: null }, tracker, 7000, true);
        duplicate ||= result.duplicate;
        duplicateClosed ||= result.duplicateClosed;
        lastSeen = result.lastSeen;
        if (result.ok) return finalize(file, before, lastSeen, tracker, attempts, result.reason, null, duplicate, duplicateClosed);
        if (result.pending) {
          result = await waitAttempt(file, before, { strategy: "composer-paste", input: null }, tracker, 38000, false);
          duplicate ||= result.duplicate;
          duplicateClosed ||= result.duplicateClosed;
          lastSeen = result.lastSeen;
          if (result.ok) return finalize(file, before, lastSeen, tracker, attempts, result.reason, null, duplicate, duplicateClosed);
        }
      }

      attempts.push({ strategy: "composer-drop", source: "current-composer-root" });
      dispatchDrop(file);
      const dropped = await waitAttempt(file, before, { strategy: "composer-drop", input: null }, tracker, 45000, false);
      duplicate ||= dropped.duplicate;
      duplicateClosed ||= dropped.duplicateClosed;
      lastSeen = dropped.lastSeen;
      if (dropped.ok) return finalize(file, before, lastSeen, tracker, attempts, dropped.reason, null, duplicate, duplicateClosed);
    } finally {
      trackerHandle.stop();
    }

    throw new Error(
      `${file.name}: ChatGPT did not confirm that the attachment finished uploading after ${attempts.map(item => item.strategy).join(" -> ")}; ` +
      `chips ${before.chips}->${lastSeen.chips}, previews ${before.media}->${lastSeen.media}, ` +
      `mutations=${tracker.strong}/${tracker.named}/${tracker.media}`
    );
  }

  function finalize(file, before, after, tracker, attempts, reason, input, duplicate, duplicateClosed) {
    const result = {
      file,
      input_id: input?.id || null,
      attempts: attempts.length,
      attempt_history: attempts,
      upload_strategy: attempts[attempts.length - 1]?.strategy || "unknown",
      verify_reason: reason,
      evidence_before: { chips: before.chips, media: before.media },
      evidence_after: { chips: after.chips, media: after.media },
      mutation_evidence: { ...tracker },
      input_consumed: input ? inputConsumed(input) : null,
      duplicate_upload_dialog: duplicate,
      duplicate_dialog_auto_closed: duplicateClosed,
      duplicate_upload_recovered: duplicate,
    };
    state.uploadEvents.push({ at: Date.now(), name: file.name, reason, strategy: result.upload_strategy, attempts: attempts.length });
    if (state.uploadEvents.length > 30) state.uploadEvents.splice(0, 15);
    return result;
  }

  async function attach(specs = []) {
    const started = performance.now();
    if (!Array.isArray(specs) || !specs.length) return { attachments_count: 0, attachment_prepare_ms: 0 };
    if (specs.length > 4) throw new Error("A maximum of 4 attachments is supported per request");
    const files = [];
    for (const spec of specs) files.push(await fetchFile(spec));
    const uploaded = [];
    for (const file of files) {
      uploaded.push(await uploadOne(file));
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
      })),
      attachment_verified: true,
      attachment_verification_revision: REVISION,
      duplicate_upload_dialog: uploaded.some(item => item.duplicate_upload_dialog),
      duplicate_dialog_auto_closed: uploaded.some(item => item.duplicate_dialog_auto_closed),
      duplicate_upload_recovered: uploaded.some(item => item.duplicate_upload_recovered),
      attachment_retry_suppressed_after_signal: true,
      attachment_strategy_fallback_v68: true,
    };
  }

  async function cleanup(names = []) {
    if (typeof v3?.removeAttachmentsByName === "function") return v3.removeAttachmentsByName(names);
    return { removed: 0, requested: Array.isArray(names) ? names.length : 0 };
  }

  globalThis[KEY] = { attach, cleanup, evidence, state, revision: REVISION, controller: CONTROLLER };
  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
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
  });
})();
