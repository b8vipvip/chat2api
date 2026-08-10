(() => {
  const KEY = "__CHAT2API_IMAGE_CONTROLLER_V2__";
  if (globalThis[KEY]) return;
  const state = { active: null };
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

  function visible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== "none" && s.visibility !== "hidden";
  }

  function textOf(el) {
    return String(el?.innerText || el?.textContent || "").replace(/\s+/g, " ").trim();
  }

  function labelOf(el) {
    return `${el?.dataset?.testid || ""} ${el?.getAttribute?.("aria-label") || ""} ${el?.title || ""} ${textOf(el)}`
      .replace(/\s+/g, " ").trim();
  }

  async function emit(event) {
    try { await chrome.runtime.sendMessage({ type: "chat2api.event", event }); }
    catch (error) { console.warn("chat2api image event failed", error); }
  }

  function composer() {
    const selectors = [
      "#prompt-textarea",
      "textarea",
      "[contenteditable='true'][data-lexical-editor='true']",
      "form [contenteditable='true']",
    ];
    for (const selector of selectors) {
      const found = [...document.querySelectorAll(selector)].find(visible);
      if (found) return found;
    }
    return null;
  }

  function composerText(el = composer()) {
    if (!el) return "";
    if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) return String(el.value || "").trim();
    return textOf(el);
  }

  function setText(el, text) {
    el.focus();
    if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(el, text);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    const selection = getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    selection.removeAllRanges();
    selection.addRange(range);
    document.execCommand("insertText", false, text);
    if (!(el.textContent || "").trim()) {
      const p = document.createElement("p");
      p.textContent = text;
      el.replaceChildren(p);
    }
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
  }

  function rawSendButton() {
    const root = composer()?.closest("form") || document;
    const selectors = [
      "button[data-testid='send-button']",
      "button[aria-label='Send prompt']",
      "button[aria-label*='发送']",
      "button[aria-label*='生成']",
      "button[type='submit']",
    ];
    for (const selector of selectors) {
      const button = [...root.querySelectorAll(selector)].find(visible);
      if (button) return button;
    }
    return [...root.querySelectorAll("button")].find(button => visible(button) && /send|submit|发送|生成/.test(labelOf(button).toLowerCase())) || null;
  }

  function buttonReady(button) {
    return Boolean(button && visible(button) && !button.disabled && button.getAttribute("aria-disabled") !== "true");
  }

  function generationIndicator() {
    const buttons = [...document.querySelectorAll("button")].filter(visible);
    const stop = buttons.find(button => /stop generating|stop|停止生成|停止/i.test(labelOf(button)));
    if (stop) return "stop-button";
    const bodyText = textOf(document.body);
    if (/(正在生成|generating image|creating image|正在创建图片)/i.test(bodyText)) return "generating-text";
    return "";
  }

  async function waitForComposer(timeout = 30000) {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      const el = composer();
      if (el) return el;
      await delay(200);
    }
    throw new Error("ChatGPT Images composer did not become ready");
  }

  async function waitForPromptRetained(input, prompt, timeout = 6000) {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      if (composerText(input) === prompt) return true;
      await delay(100);
    }
    return false;
  }

  function dispatchEnter(input) {
    input.focus();
    for (const type of ["keydown", "keypress", "keyup"]) {
      input.dispatchEvent(new KeyboardEvent(type, {
        key: "Enter", code: "Enter", keyCode: 13, which: 13,
        bubbles: true, cancelable: true,
      }));
    }
  }

  async function submitAndConfirm(input, prompt) {
    const started = performance.now();
    const button = rawSendButton();
    let strategy = "enter-key";
    if (buttonReady(button)) {
      strategy = button.dataset.testid === "send-button" ? "send-button-testid" : "send-button";
      button.click();
    } else {
      dispatchEnter(input);
    }

    const deadline = Date.now() + 12000;
    while (Date.now() < deadline) {
      const current = composerText(input);
      const indicator = generationIndicator();
      if (!current) return { strategy, reason: "composer-cleared", submit_ms: performance.now() - started };
      if (indicator) return { strategy, reason: indicator, submit_ms: performance.now() - started };
      await delay(120);
    }

    if (composerText(input) === prompt && strategy !== "enter-key") {
      dispatchEnter(input);
      const retryDeadline = Date.now() + 8000;
      while (Date.now() < retryDeadline) {
        const current = composerText(input);
        const indicator = generationIndicator();
        if (!current) return { strategy: `${strategy}+enter-fallback`, reason: "composer-cleared", submit_ms: performance.now() - started };
        if (indicator) return { strategy: `${strategy}+enter-fallback`, reason: indicator, submit_ms: performance.now() - started };
        await delay(120);
      }
    }

    throw new Error("ChatGPT Images prompt submission was not confirmed; refusing to treat gallery images as generated output");
  }

  function imageCandidates() {
    return [...document.querySelectorAll("img")].filter(img => {
      const r = img.getBoundingClientRect();
      const src = img.currentSrc || img.src || "";
      if (!src || /avatar|emoji|icon|logo/i.test(src)) return false;
      return r.width >= 140 && r.height >= 140;
    });
  }

  async function imageToBase64(img) {
    const src = img.currentSrc || img.src || "";
    const response = await fetch(src);
    if (!response.ok) throw new Error(`Generated image fetch failed: HTTP ${response.status}`);
    const blob = await response.blob();
    const buffer = await blob.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
    return {
      b64_json: btoa(binary),
      mime_type: blob.type || "image/png",
      url: src,
      width: img.naturalWidth || 0,
      height: img.naturalHeight || 0,
    };
  }

  async function run(message) {
    if (state.active) throw new Error("ChatGPT Images is already processing another request");
    const prompt = String(message.prompt || "").trim();
    if (!prompt) throw new Error("Image prompt is empty");
    const active = { requestId: message.requestId, cancelled: false };
    state.active = active;
    const started = performance.now();

    try {
      const input = await waitForComposer();
      const baselineNodes = new Set([...document.querySelectorAll("img")]);
      setText(input, prompt);
      const retained = await waitForPromptRetained(input, prompt);
      if (!retained) throw new Error("Image prompt was inserted but ChatGPT Images did not retain it");

      const submitted = await submitAndConfirm(input, prompt);
      await emit({
        type: "image.started",
        request_id: active.requestId,
        diagnostics: {
          images_page: true,
          image_controller: "image-v2",
          submission_confirmed: true,
          submission_strategy: submitted.strategy,
          submission_reason: submitted.reason,
          submit_ms: Math.round(submitted.submit_ms * 10) / 10,
          baseline_image_nodes: baselineNodes.size,
        },
      });

      const timeout = Math.max(30000, Number(message.options?.timeout_seconds || 300) * 1000);
      const end = Date.now() + timeout;
      let lastProgress = 0;
      while (!active.cancelled && Date.now() < end) {
        const candidates = imageCandidates().filter(img => !baselineNodes.has(img));
        const complete = candidates.find(img => img.complete && (img.naturalWidth || 0) >= 256 && (img.naturalHeight || 0) >= 256);
        if (complete) {
          const captured = await imageToBase64(complete);
          if (!captured.b64_json || captured.b64_json.length < 100) throw new Error("Generated image appeared but could not be captured as image bytes");
          await emit({
            type: "image.completed",
            request_id: active.requestId,
            images: [captured],
            diagnostics: {
              images_page: true,
              image_controller: "image-v2",
              submission_confirmed: true,
              generated_node_count: candidates.length,
              capture_ms: Math.round(performance.now() - started),
            },
          });
          return;
        }
        if (Date.now() - lastProgress > 5000) {
          lastProgress = Date.now();
          await emit({
            type: "image.progress",
            request_id: active.requestId,
            stage: "generating",
            elapsed_ms: Math.round(performance.now() - started),
            diagnostics: { image_controller: "image-v2", submission_confirmed: true, generated_node_count: candidates.length },
          });
        }
        await delay(500);
      }

      if (active.cancelled) await emit({ type: "image.cancelled", request_id: active.requestId, reason: "Cancelled" });
      else await emit({ type: "image.error", request_id: active.requestId, error: "Timed out waiting for a newly-created generated image node on ChatGPT Images" });
    } finally {
      if (state.active === active) state.active = null;
    }
  }

  const listener = (message, _sender, sendResponse) => {
    if (message.type === "chat2api.image.ping.v2") {
      sendResponse({ ok: true, controller: "image-v2" });
      return false;
    }
    if (message.type === "chat2api.image.request.v2") {
      run(message).catch(error => emit({ type: "image.error", request_id: message.requestId, error: String(error?.message || error) }));
      sendResponse({ ok: true, controller: "image-v2" });
      return false;
    }
    if (message.type === "chat2api.image.cancel.v2") {
      if (state.active && state.active.requestId === message.requestId) state.active.cancelled = true;
      sendResponse({ ok: true, controller: "image-v2" });
      return false;
    }
    return false;
  };

  chrome.runtime.onMessage.addListener(listener);
  globalThis[KEY] = { state, listener };
})();
