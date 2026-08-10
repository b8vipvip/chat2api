(() => {
  const KEY = "__CHAT2API_MULTIMODAL_V2__";
  if (globalThis[KEY]) return;
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

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

  function uploadErrorFor(name) {
    const shortName = String(name || "").slice(0, 36);
    const alerts = [...document.querySelectorAll("[role='alert'], [data-sonner-toast], [data-toast], [class*='toast']")];
    for (const node of alerts.slice(-10)) {
      const text = String(node.innerText || node.textContent || "").replace(/\s+/g, " ").trim();
      if (!text) continue;
      if (/(无法上传|上传失败|failed to upload|couldn.?t upload|cannot upload)/i.test(text) && (!shortName || text.includes(shortName) || text.includes(name))) {
        return text.slice(0, 500);
      }
    }
    return "";
  }

  function attachmentCount() {
    return document.querySelectorAll("[data-testid*='attachment'], [data-testid*='file-chip'], [aria-label*='Remove file'], [aria-label*='删除文件']").length;
  }

  function fileVisible(name, beforeCount) {
    const body = document.body?.innerText || "";
    if (body.includes(name)) return true;
    const stem = String(name || "").replace(/\.[^.]+$/, "");
    if (stem && stem.length >= 8 && body.includes(stem)) return true;
    return attachmentCount() > beforeCount;
  }

  async function exposeFileInput(file) {
    let found = candidateInput(file);
    if (found) return found;
    const button = attachmentButton();
    if (button) button.click();
    for (let i = 0; i < 40; i += 1) {
      await delay(100);
      found = candidateInput(file);
      if (found) return found;
    }
    throw new Error(`ChatGPT has no compatible file input for ${file.name} (${file.type || "unknown type"})`);
  }

  async function uploadOne(file, attempt = 1) {
    const beforeCount = attachmentCount();
    const input = await exposeFileInput(file);
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));

    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      const uploadError = uploadErrorFor(file.name);
      if (uploadError) {
        if (attempt < 2) {
          await delay(700);
          return uploadOne(file, attempt + 1);
        }
        throw new Error(`${file.name}: ${uploadError}`);
      }
      if (fileVisible(file.name, beforeCount)) {
        await delay(350);
        return { file, input_id: input.id || null, attempt };
      }
      await delay(200);
    }
    if (attempt < 2) return uploadOne(file, attempt + 1);
    throw new Error(`${file.name}: ChatGPT did not confirm that the attachment finished uploading`);
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
      attachments_count: uploaded.length,
      attachment_names: uploaded.map(item => item.file.name),
      attachment_bytes: uploaded.reduce((sum, item) => sum + item.file.size, 0),
      attachment_prepare_ms: Math.round((performance.now() - started) * 10) / 10,
      attachment_input_ids: uploaded.map(item => item.input_id),
      attachment_attempts: uploaded.map(item => ({ name: item.file.name, attempts: item.attempt })),
      attachment_verified: true,
    };
  }

  globalThis[KEY] = { attach };
  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type !== "chat2api.attach.prepare") return false;
    attach(message.attachments || [])
      .then(data => sendResponse({ ok: true, data }))
      .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  });
})();
