(() => {
  const KEY = "__CHAT2API_MULTIMODAL_V4__";
  if (globalThis[KEY]) return;

  const CONTROLLER = "multimodal-v4-r64";
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
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== "none" && s.visibility !== "hidden" && Number(s.opacity || 1) !== 0;
  }

  const textOf = el => String(el?.innerText || el?.textContent || "").replace(/\s+/g, " ").trim();

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || null;
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
    const accept = String(input.accept || "").trim().toLowerCase();
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

  function inputScore(input, file) {
    if (input.disabled || !acceptsFile(input, file)) return -10000;
    const id = `${input.id || ""} ${input.name || ""} ${input.dataset?.testid || ""}`.toLowerCase();
    const accept = String(input.accept || "").toLowerCase();
    const mime = String(file.type || "").toLowerCase();
    const isImage = mime.startsWith("image/");
    const isVideo = mime.startsWith("video/");
    let score = 10;
    if (!accept || accept === "*/*") score += 100;
    if (/upload-photos|upload-camera|photo|image/.test(id)) {
      if (isImage || isVideo) score += 90;
      else score -= 500;
    }
    if (/file|attachment|document|upload/.test(id)) score += 90;
    if (isImage && accept.includes("image")) score += 50;
    if (isVideo && accept.includes("video")) score += 50;
    if (!isImage && !isVideo && /pdf|text|word|sheet|presentation|json|csv|\*/.test(accept)) score += 60;
    return score;
  }

  function candidateInput(file) {
    return [...document.querySelectorAll("input[type='file']")]
      .map(input => ({ input, score: inputScore(input, file) }))
      .filter(item => item.score > 0)
      .sort((a, b) => b.score - a.score)[0]?.input || null;
  }

  function attachmentButton() {
    const root = composerRoot() || document;
    return [...root.querySelectorAll("button")].find(button => !button.disabled && /composer-plus|attach|attachment|add files|add photos|upload|添加文件|添加照片|附件|上传/i.test(
      `${button.dataset?.testid || ""} ${button.getAttribute("aria-label") || ""} ${button.title || ""} ${button.innerText || ""}`
    )) || null;
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
    const documentBlob = [...document.querySelectorAll("img[src^='blob:'],img[src^='data:'],video[src^='blob:'],video[src^='data:']")]
      .filter(node => visible(node));
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
      const button = [...dialog.querySelectorAll("button")].find(btn => visible(btn) && !btn.disabled && /确定|知道了|ok|okay|got it|confirm/i.test(`${btn.getAttribute("aria-label") || ""} ${btn.innerText || ""}`));
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

  async function exposeInput(file) {
    let input = candidateInput(file);
    if (input) return input;
    attachmentButton()?.click();
    for (let i = 0; i < 60; i += 1) {
      await delay(100);
      const dup = duplicateDialog();
      if (dup) await closeDuplicate(dup);
      input = candidateInput(file);
      if (input) return input;
    }
    throw new Error(`ChatGPT has no compatible file input for ${file.name} (${file.type || "unknown type"})`);
  }

  function inputConsumed(input) {
    if (!input?.isConnected) return true;
    try { return !input.files || input.files.length === 0; } catch (_) { return false; }
  }

  async function uploadOne(file) {
    const before = evidence();
    const input = await exposeInput(file);
    const mutations = mutationTracker(file);
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));

    const deadline = Date.now() + 45000;
    let duplicate = false;
    let duplicateClosed = false;
    let consumedSince = 0;
    let lastSeen = before;
    try {
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
          return {
            file,
            input_id: input.id || null,
            attempts: 1,
            verify_reason: seen.reason,
            evidence_before: { chips: before.chips, media: before.media },
            evidence_after: { chips: seen.now.chips, media: seen.now.media },
            mutation_evidence: { ...mutations.tracker },
            input_consumed: inputConsumed(input),
            duplicate_upload_dialog: duplicate,
            duplicate_dialog_auto_closed: duplicateClosed,
            duplicate_upload_recovered: duplicate,
          };
        }

        const consumed = inputConsumed(input);
        const mutationEvidence = mutations.tracker.strong > 0 || mutations.tracker.named > 0 || mutations.tracker.media > 0;
        if (consumed && mutationEvidence && !uploadBusy()) {
          if (!consumedSince) consumedSince = Date.now();
          if (Date.now() - consumedSince >= 1200) {
            const lateError = uploadErrorFor(file.name);
            if (lateError) throw new Error(`${file.name}: ${lateError}`);
            const finalEvidence = evidence();
            state.uploadEvents.push({ at: Date.now(), name: file.name, reason: "input-consumed-stable", mutations: { ...mutations.tracker } });
            if (state.uploadEvents.length > 30) state.uploadEvents.splice(0, 15);
            return {
              file,
              input_id: input.id || null,
              attempts: 1,
              verify_reason: "input-consumed-stable",
              evidence_before: { chips: before.chips, media: before.media },
              evidence_after: { chips: finalEvidence.chips, media: finalEvidence.media },
              mutation_evidence: { ...mutations.tracker },
              input_consumed: true,
              duplicate_upload_dialog: duplicate,
              duplicate_dialog_auto_closed: duplicateClosed,
              duplicate_upload_recovered: duplicate,
            };
          }
        } else {
          consumedSince = 0;
        }
        await delay(160);
      }
    } finally {
      mutations.stop();
    }
    throw new Error(
      `${file.name}: ChatGPT did not confirm that the attachment finished uploading; ` +
      `chips ${before.chips}->${lastSeen.chips}, previews ${before.media}->${lastSeen.media}, ` +
      `input_consumed=${inputConsumed(input)}, mutations=${mutations.tracker.strong}/${mutations.tracker.named}/${mutations.tracker.media}`
    );
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
      attachments_revision: 64,
      attachments_count: uploaded.length,
      attachment_names: uploaded.map(item => item.file.name),
      attachment_bytes: uploaded.reduce((sum, item) => sum + item.file.size, 0),
      attachment_prepare_ms: Math.round((performance.now() - started) * 10) / 10,
      attachment_input_ids: uploaded.map(item => item.input_id),
      attachment_attempts: uploaded.map(item => ({
        name: item.file.name,
        attempts: item.attempts,
        verify_reason: item.verify_reason,
        input_consumed: item.input_consumed,
        mutation_evidence: item.mutation_evidence,
      })),
      attachment_verified: true,
      attachment_verification_revision: 64,
      duplicate_upload_dialog: uploaded.some(item => item.duplicate_upload_dialog),
      duplicate_dialog_auto_closed: uploaded.some(item => item.duplicate_dialog_auto_closed),
      duplicate_upload_recovered: uploaded.some(item => item.duplicate_upload_recovered),
      attachment_retry_suppressed: true,
    };
  }

  async function cleanup(names = []) {
    if (typeof v3?.removeAttachmentsByName === "function") return v3.removeAttachmentsByName(names);
    return { removed: 0, requested: Array.isArray(names) ? names.length : 0 };
  }

  globalThis[KEY] = { attach, cleanup, evidence, state, revision: 64, controller: CONTROLLER };
  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type === "chat2api.attach.ping.v4") {
      sendResponse({ ok: true, controller: CONTROLLER, revision: 64 });
      return false;
    }
    if (message.type === "chat2api.attach.prepare.v4") {
      attach(message.attachments || [])
        .then(data => sendResponse({ ok: true, data, controller: CONTROLLER, revision: 64 }))
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error), controller: CONTROLLER, revision: 64 }));
      return true;
    }
    if (message.type === "chat2api.attach.cleanup.v4") {
      cleanup(message.names || [])
        .then(data => sendResponse({ ok: true, data, controller: CONTROLLER, revision: 64 }))
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error), controller: CONTROLLER, revision: 64 }));
      return true;
    }
    return false;
  });
})();
