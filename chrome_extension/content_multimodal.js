(() => {
  const KEY = "__CHAT2API_MULTIMODAL_V3__";
  if (globalThis[KEY]) return;
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const state = { duplicateEvents: [] };

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
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== "none" && s.visibility !== "hidden";
  }

  function textOf(el) {
    return String(el?.innerText || el?.textContent || "").replace(/\s+/g, " ").trim();
  }

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || null;
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
    const id = String(input.id || "").toLowerCase();
    const accept = String(input.accept || "").toLowerCase();
    const isImage = String(file.type || "").startsWith("image/");
    const isVideo = String(file.type || "").startsWith("video/");
    let score = 10;
    if (!accept || accept === "*/*") score += 100;
    if (/upload-photos|upload-camera/.test(id)) {
      if (isImage || isVideo) score += 80;
      else score -= 500;
    }
    if (/file|attachment|document/.test(id)) score += 80;
    if (isImage && accept.includes("image")) score += 40;
    if (isVideo && accept.includes("video")) score += 40;
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
    const buttons = [...root.querySelectorAll("button")].filter(button => !button.disabled);
    return buttons.find(button => /composer-plus|attach|attachment|add files|add photos|upload|添加文件|添加照片|附件|上传/.test(
      `${button.dataset.testid || ""} ${button.getAttribute("aria-label") || ""} ${button.title || ""} ${button.innerText || ""}`.toLowerCase()
    )) || null;
  }

  function attachmentSurface() {
    const root = composerRoot();
    if (!root) return null;
    return root.parentElement || root;
  }

  function attachmentCount() {
    const root = attachmentSurface();
    if (!root) return 0;
    return root.querySelectorAll(
      "[data-testid*='attachment'],[data-testid*='file-chip'],[aria-label*='Remove file'],[aria-label*='删除文件']"
    ).length;
  }

  function normalizedNames(names = []) {
    return [...new Set((Array.isArray(names) ? names : []).map(name => String(name || "").trim()).filter(Boolean))];
  }

  function attachmentRemoveButtons() {
    const root = attachmentSurface();
    if (!root) return [];
    return [...root.querySelectorAll("button")].filter(button => {
      if (!visible(button) || button.disabled) return false;
      const label = `${button.dataset.testid || ""} ${button.getAttribute("aria-label") || ""} ${button.title || ""} ${textOf(button)}`;
      return /remove file|remove attachment|删除文件|移除文件|移除附件|attachment-remove|file-remove/i.test(label);
    });
  }

  async function removeAttachmentsByName(names = []) {
    const targets = normalizedNames(names);
    if (!targets.length) return { removed: 0, requested: 0 };
    let removed = 0;
    for (const button of attachmentRemoveButtons()) {
      const container = button.closest("[data-testid*='attachment'],[data-testid*='file-chip'],li,div") || button.parentElement || button;
      const haystack = `${textOf(container)} ${button.getAttribute("aria-label") || ""} ${button.title || ""}`;
      const matched = targets.some(name => {
        const stem = name.replace(/\.[^.]+$/, "");
        return haystack.includes(name) || (stem.length >= 8 && haystack.includes(stem));
      });
      if (!matched) continue;
      button.click();
      removed += 1;
      await delay(180);
    }
    return { removed, requested: targets.length };
  }

  function fileVisible(name, beforeCount) {
    const root = attachmentSurface();
    if (!root) return false;
    const text = textOf(root);
    const stem = String(name || "").replace(/\.[^.]+$/, "");
    if (name && text.includes(name)) return true;
    if (stem && stem.length >= 8 && text.includes(stem)) return true;
    return attachmentCount() > beforeCount;
  }

  function uploadErrorFor(name) {
    const shortName = String(name || "").slice(0, 36);
    const alerts = [...document.querySelectorAll("[role='alert'], [data-sonner-toast], [data-toast], [class*='toast']")];
    for (const node of alerts.slice(-12)) {
      const text = textOf(node);
      if (!text) continue;
      if (/(无法上传|上传失败|failed to upload|couldn.?t upload|cannot upload)/i.test(text) &&
          (!shortName || text.includes(shortName) || text.includes(name))) return text.slice(0, 500);
    }
    return "";
  }

  function duplicateDialog() {
    const dialogs = [...document.querySelectorAll("[role='dialog'],[aria-modal='true']")].filter(visible);
    for (const dialog of dialogs.slice(-6)) {
      const text = textOf(dialog);
      if (!/(你已上传过此文件|已经上传过此文件|you(?:'|’)ve already uploaded this file|already uploaded this file)/i.test(text)) continue;
      const button = [...dialog.querySelectorAll("button")].find(btn => {
        if (!visible(btn) || btn.disabled) return false;
        const label = `${btn.getAttribute("aria-label") || ""} ${btn.innerText || ""}`.trim();
        return /^(确定|知道了|好|ok|okay|got it|confirm)$/i.test(label) || /确定|got it|confirm/i.test(label);
      });
      return { dialog, text: text.slice(0, 500), button };
    }
    return null;
  }

  function rememberDuplicate(info) {
    const event = { at: Date.now(), text: info.text };
    state.duplicateEvents.push(event);
    if (state.duplicateEvents.length > 20) state.duplicateEvents.splice(0, 10);
    return event;
  }

  function recentDuplicate(since) {
    return [...state.duplicateEvents].reverse().find(event => event.at >= since) || null;
  }

  async function closeDuplicateDialog(info) {
    if (!info) return false;
    rememberDuplicate(info);
    if (info.button) {
      info.button.click();
      await delay(300);
      return true;
    }
    return false;
  }

  async function recoverDuplicateDialogs() {
    const info = duplicateDialog();
    if (info) await closeDuplicateDialog(info);
  }

  const observer = new MutationObserver(() => { recoverDuplicateDialogs().catch(() => {}); });
  observer.observe(document.documentElement, { childList: true, subtree: true });

  async function exposeFileInput(file) {
    let found = candidateInput(file);
    if (found) return found;
    const button = attachmentButton();
    if (button) button.click();
    for (let i = 0; i < 50; i += 1) {
      await delay(100);
      await recoverDuplicateDialogs();
      found = candidateInput(file);
      if (found) return found;
    }
    throw new Error(`ChatGPT has no compatible file input for ${file.name} (${file.type || "unknown type"})`);
  }

  async function uploadOne(file) {
    const startedAt = Date.now();
    const beforeCount = attachmentCount();
    const input = await exposeFileInput(file);
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));

    const deadline = Date.now() + 45000;
    let duplicateRecovered = false;
    let duplicateDialogClosed = false;
    while (Date.now() < deadline) {
      const duplicate = duplicateDialog();
      if (duplicate) {
        duplicateDialogClosed = await closeDuplicateDialog(duplicate);
        duplicateRecovered = true;
      } else if (recentDuplicate(startedAt)) {
        duplicateRecovered = true;
        duplicateDialogClosed = true;
      }

      const uploadError = uploadErrorFor(file.name);
      if (uploadError) throw new Error(`${file.name}: ${uploadError}`);

      if (fileVisible(file.name, beforeCount)) {
        await delay(500);
        return {
          file,
          input_id: input.id || null,
          attempts: 1,
          duplicate_upload_dialog: duplicateRecovered,
          duplicate_dialog_auto_closed: duplicateDialogClosed,
          duplicate_upload_recovered: duplicateRecovered,
          attachment_retry_suppressed: true,
        };
      }
      await delay(200);
    }

    const duplicate = duplicateDialog();
    if (duplicate) {
      duplicateDialogClosed = await closeDuplicateDialog(duplicate);
      duplicateRecovered = true;
    }
    if (duplicateRecovered) {
      throw new Error(`${file.name}: ChatGPT reported a duplicate upload, but the attachment is not present in the current composer`);
    }
    throw new Error(`${file.name}: ChatGPT did not confirm that the attachment finished uploading; automatic duplicate retry was suppressed`);
  }

  async function attach(specs = []) {
    const started = performance.now();
    if (!Array.isArray(specs) || !specs.length) return { attachments_count: 0, attachment_prepare_ms: 0 };
    if (specs.length > 4) throw new Error("A maximum of 4 attachments is supported per request");

    await recoverDuplicateDialogs();
    const files = [];
    for (const spec of specs) files.push(await fetchFile(spec));
    const uploaded = [];
    for (const file of files) {
      uploaded.push(await uploadOne(file));
      await recoverDuplicateDialogs();
      await delay(300);
    }

    return {
      attachments_count: uploaded.length,
      attachment_names: uploaded.map(item => item.file.name),
      attachment_bytes: uploaded.reduce((sum, item) => sum + item.file.size, 0),
      attachment_prepare_ms: Math.round((performance.now() - started) * 10) / 10,
      attachment_input_ids: uploaded.map(item => item.input_id),
      attachment_attempts: uploaded.map(item => ({ name: item.file.name, attempts: item.attempts })),
      attachment_verified: true,
      duplicate_upload_dialog: uploaded.some(item => item.duplicate_upload_dialog),
      duplicate_dialog_auto_closed: uploaded.some(item => item.duplicate_dialog_auto_closed),
      duplicate_upload_recovered: uploaded.some(item => item.duplicate_upload_recovered),
      attachment_retry_suppressed: true,
    };
  }

  globalThis[KEY] = { attach, recoverDuplicateDialogs, removeAttachmentsByName, attachmentCount, state };
  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type === "chat2api.attach.prepare") {
      attach(message.attachments || [])
        .then(data => sendResponse({ ok: true, data }))
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }
    if (message.type === "chat2api.attach.dismissDuplicate") {
      recoverDuplicateDialogs().then(() => sendResponse({ ok: true })).catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }
    if (message.type === "chat2api.attach.cleanup") {
      removeAttachmentsByName(message.names || []).then(data => sendResponse({ ok: true, data })).catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }
    return false;
  });
})();