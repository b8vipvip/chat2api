(() => {
  const KEY = "__CHAT2API_MULTIMODAL_V1__";
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

  function candidateInputs(files) {
    const inputs = [...document.querySelectorAll("input[type='file']")].filter(input => !input.disabled);
    return inputs.sort((a, b) => {
      const score = input => {
        const accept = String(input.accept || "").toLowerCase();
        const id = String(input.id || "").toLowerCase();
        let value = 0;
        if (/upload-photos-input|upload-camera/.test(id)) value += files.every(file => file.type.startsWith("image/")) ? 100 : -20;
        if (!accept) value += 30;
        if (files.every(file => file.type.startsWith("image/")) && accept.includes("image")) value += 60;
        if (files.some(file => !file.type.startsWith("image/")) && /pdf|text|word|sheet|presentation|\*/.test(accept)) value += 50;
        if (input.multiple) value += 10;
        return value;
      };
      return score(b) - score(a);
    });
  }

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")].find(form => form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || null;
  }

  async function exposeFileInput(files) {
    let found = candidateInputs(files)[0];
    if (found) return found;
    const root = composerRoot() || document;
    const button = [...root.querySelectorAll("button")].find(el => {
      const text = `${el.getAttribute("aria-label") || ""} ${el.innerText || ""}`.toLowerCase();
      return /attach|add files|upload|附件|添加|上传/.test(text);
    });
    if (button) button.click();
    for (let i = 0; i < 30; i += 1) {
      await delay(100);
      found = candidateInputs(files)[0];
      if (found) return found;
    }
    throw new Error("ChatGPT file input was not found. The attachment UI may have changed.");
  }

  async function attach(specs = []) {
    const started = performance.now();
    if (!Array.isArray(specs) || !specs.length) return { attachments_count: 0, attachment_prepare_ms: 0 };
    if (specs.length > 4) throw new Error("A maximum of 4 attachments is supported per request");
    const files = [];
    for (const spec of specs) files.push(await fetchFile(spec));
    const input = await exposeFileInput(files);
    const transfer = new DataTransfer();
    files.forEach(file => transfer.items.add(file));
    input.files = transfer.files;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    const names = files.map(file => file.name);
    const deadline = Date.now() + 12000;
    while (Date.now() < deadline) {
      const pageText = document.body?.innerText || "";
      if (names.some(name => pageText.includes(name)) || document.querySelector("[data-testid*='attachment'], [data-testid*='file'], [aria-label*='Remove file'], [aria-label*='删除文件']")) break;
      await delay(250);
    }
    await delay(500);
    return {
      attachments_count: files.length,
      attachment_names: names,
      attachment_bytes: files.reduce((sum, file) => sum + file.size, 0),
      attachment_prepare_ms: Math.round((performance.now() - started) * 10) / 10,
      attachment_input_id: input.id || null,
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
