(() => {
  const KEY = "__CHAT2API_MULTIMODAL_MAIN_V78__";
  if (globalThis[KEY]) return;

  const REVISION = 78;
  const REQUEST_TYPE = "chat2api.multimodal.main.request.v78";
  const RESPONSE_TYPE = "chat2api.multimodal.main.response.v78";
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

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

  function attachmentButton() {
    const root = composerRoot() || document;
    return [...root.querySelectorAll("button")].find(button => visible(button) && !button.disabled && /composer-plus|attach|attachment|add files|add photos|upload|添加文件|添加照片|附件|上传/i.test(labelOf(button))) || null;
  }

  function uploadMenuAction() {
    const selectors = ["[role='menuitem']", "[role='option']", "button", "[data-radix-menu-content] [tabindex]"];
    const nodes = [...new Set(selectors.flatMap(selector => [...document.querySelectorAll(selector)]))];
    return nodes.find(node => visible(node) && !node.disabled && /upload from computer|upload file|add photos?\s*(?:&|and)?\s*files?|photos? and files?|从计算机上传|上传文件|添加照片和文件|添加照片或文件|照片和文件/i.test(labelOf(node))) || null;
  }

  function extension(name) {
    const match = String(name || "").toLowerCase().match(/(\.[a-z0-9]+)$/);
    return match ? match[1] : "";
  }

  function accepts(input, file) {
    const accept = String(input?.accept || "").trim().toLowerCase();
    if (!accept || accept === "*/*") return true;
    const mime = String(file.type || "").toLowerCase();
    const ext = extension(file.name);
    return accept.split(",").map(value => value.trim()).filter(Boolean).some(rule => {
      if (rule === "*/*") return true;
      if (rule.startsWith(".")) return rule === ext;
      if (rule.endsWith("/*")) return mime.startsWith(rule.slice(0, -1));
      return mime === rule;
    });
  }

  function inputs() {
    return [...document.querySelectorAll("input[type='file']")];
  }

  function scoreInput(input, file, before = new Set()) {
    if (!input || input.disabled || !accepts(input, file)) return -10000;
    const root = composerRoot();
    const id = `${input.id || ""} ${input.name || ""} ${input.dataset?.testid || ""} ${input.getAttribute?.("aria-label") || ""}`.toLowerCase();
    const accept = String(input.accept || "").toLowerCase();
    const mime = String(file.type || "").toLowerCase();
    let score = 10;
    if (root?.contains(input)) score += 500;
    if (!before.has(input)) score += 400;
    if (/file|attachment|document|upload/.test(id)) score += 120;
    if (/image|photo|camera/.test(id) && mime.startsWith("image/")) score += 100;
    if (mime.startsWith("image/") && accept.includes("image")) score += 80;
    if (!accept || accept === "*/*") score += 60;
    return score;
  }

  function bestInput(file, before = new Set()) {
    return inputs()
      .map(input => ({ input, score: scoreInput(input, file, before) }))
      .filter(item => item.score > 0)
      .sort((a, b) => b.score - a.score)[0]?.input || null;
  }

  async function exposeInput(file) {
    const before = new Set(inputs());
    let input = bestInput(file, before);
    if (input && composerRoot()?.contains(input)) return { input, source: "main-composer-existing" };

    attachmentButton()?.click();
    for (let index = 0; index < 24; index += 1) {
      await delay(100);
      const action = uploadMenuAction();
      if (action) {
        action.click();
        break;
      }
      input = bestInput(file, before);
      if (input && (!before.has(input) || composerRoot()?.contains(input))) {
        return { input, source: !before.has(input) ? "main-fresh-after-plus" : "main-composer-after-plus" };
      }
    }

    for (let index = 0; index < 45; index += 1) {
      await delay(100);
      input = bestInput(file, before);
      if (input && (!before.has(input) || composerRoot()?.contains(input))) {
        return { input, source: !before.has(input) ? "main-fresh-after-menu" : "main-composer-after-menu" };
      }
    }
    input = bestInput(file, before);
    return input ? { input, source: "main-global-fallback" } : { input: null, source: "main-none" };
  }

  function bytes(base64) {
    const raw = atob(String(base64 || ""));
    const result = new Uint8Array(raw.length);
    for (let index = 0; index < raw.length; index += 1) result[index] = raw.charCodeAt(index);
    return result;
  }

  function fileFrom(payload) {
    return new File([bytes(payload.base64)], String(payload.filename || "attachment"), {
      type: String(payload.mime_type || "application/octet-stream"),
      lastModified: Number(payload.last_modified || Date.now()),
    });
  }

  function setInputFiles(input, file) {
    const transfer = new DataTransfer();
    transfer.items.add(file);
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "files")?.set;
    if (!setter) throw new Error("MAIN-world HTMLInputElement.files setter is unavailable");
    setter.call(input, transfer.files);
    input.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertFromPaste" }));
    input.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  }

  function paste(file) {
    if (!String(file.type || "").startsWith("image/")) throw new Error("MAIN-world paste requires an image MIME type");
    const editor = composerEditor();
    if (!editor) throw new Error("MAIN-world composer editor was not found");
    const transfer = new DataTransfer();
    transfer.items.add(file);
    editor.focus();
    let event;
    try {
      event = new ClipboardEvent("paste", { bubbles: true, cancelable: true, composed: true, clipboardData: transfer });
    } catch (_) {
      event = new Event("paste", { bubbles: true, cancelable: true, composed: true });
      Object.defineProperty(event, "clipboardData", { value: transfer });
    }
    editor.dispatchEvent(event);
  }

  function drop(file) {
    const root = composerRoot();
    const target = root?.querySelector("[data-testid*='drop'],[class*='drop']") || root || composerEditor();
    if (!target) throw new Error("MAIN-world composer drop target was not found");
    const transfer = new DataTransfer();
    transfer.items.add(file);
    for (const type of ["dragenter", "dragover", "drop"]) {
      let event;
      try {
        event = new DragEvent(type, { bubbles: true, cancelable: true, composed: true, dataTransfer: transfer });
      } catch (_) {
        event = new Event(type, { bubbles: true, cancelable: true, composed: true });
        Object.defineProperty(event, "dataTransfer", { value: transfer });
      }
      target.dispatchEvent(event);
    }
  }

  async function execute(payload) {
    const strategy = String(payload.strategy || "file-input");
    const file = fileFrom(payload);
    if (strategy === "file-input") {
      const exposed = await exposeInput(file);
      if (!exposed.input) throw new Error("MAIN-world ChatGPT file input was not found");
      setInputFiles(exposed.input, file);
      await delay(250);
      let consumed = false;
      try { consumed = !exposed.input.isConnected || !exposed.input.files || exposed.input.files.length === 0; } catch (_) {}
      return { strategy, source: exposed.source, input_id: exposed.input.id || null, input_consumed: consumed };
    }
    if (strategy === "composer-paste") {
      paste(file);
      return { strategy, source: "main-composer-editor", input_id: null, input_consumed: null };
    }
    if (strategy === "composer-drop") {
      drop(file);
      return { strategy, source: "main-composer-root", input_id: null, input_consumed: null };
    }
    throw new Error(`Unknown MAIN-world multimodal strategy: ${strategy}`);
  }

  async function listener(event) {
    if (event.source !== window) return;
    const message = event.data;
    if (!message || message.type !== REQUEST_TYPE || Number(message.revision || 0) !== REVISION) return;
    const requestId = String(message.request_id || "");
    if (!requestId) return;
    try {
      const data = await execute(message.payload || {});
      window.postMessage({ type: RESPONSE_TYPE, revision: REVISION, request_id: requestId, ok: true, data }, "*");
    } catch (error) {
      window.postMessage({ type: RESPONSE_TYPE, revision: REVISION, request_id: requestId, ok: false, error: String(error?.message || error) }, "*");
    }
  }

  window.addEventListener("message", listener);
  document.documentElement?.setAttribute?.("data-chat2api-multimodal-main-v78", String(REVISION));
  globalThis[KEY] = Object.freeze({ revision: REVISION, request_type: REQUEST_TYPE, response_type: RESPONSE_TYPE });
})();
