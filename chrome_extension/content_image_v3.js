(() => {
  const KEY = "__CHAT2API_IMAGE_CONTROLLER_V3__";
  if (globalThis[KEY]) return;

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const state = { active: null };
  globalThis[KEY] = state;

  function visible(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  const normalize = value => String(value || "").replace(/[\u200B-\u200D\uFEFF]/g, "").replace(/\s+/g, " ").trim();
  const textOf = el => normalize(el?.innerText || el?.textContent || "");
  const labelOf = el => normalize(`${el?.dataset?.testid || ""} ${el?.getAttribute?.("aria-label") || ""} ${el?.title || ""} ${textOf(el)}`);

  async function emit(event) {
    try { await chrome.runtime.sendMessage({ type: "chat2api.event", event }); }
    catch (_) {}
  }

  async function diagnostic(active, stage, extra = {}) {
    await emit({
      type: "image.diagnostics",
      request_id: active.requestId,
      diagnostics: { image_controller: "image-v3", image_stage: stage, ...extra },
    });
  }

  function composer() {
    const selectors = [
      "div.prompt-textarea.ProseMirror[contenteditable='true']",
      "#prompt-textarea[contenteditable='true']",
      "#prompt-textarea",
      "[data-testid*='composer'] [contenteditable='true']",
      "form [contenteditable='true'].ProseMirror",
      "form [contenteditable='true']",
      "textarea",
    ];
    for (const selector of selectors) {
      const found = [...document.querySelectorAll(selector)].find(visible);
      if (found) return found;
    }
    return null;
  }

  function composerText(el = composer()) {
    if (!el) return "";
    if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) return normalize(el.value || "");
    return normalize(el.innerText || el.textContent || "");
  }

  function setText(el, text) {
    if (!el) return;
    el.focus();
    if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(el, text);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }

    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    selection.removeAllRanges();
    selection.addRange(range);
    try {
      el.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, cancelable: true, inputType: "insertText", data: text }));
    } catch (_) {}
    document.execCommand("insertText", false, text);
    if (!normalize(el.textContent || "")) {
      const p = document.createElement("p");
      p.textContent = text;
      el.replaceChildren(p);
    }
    try {
      el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
    } catch (_) {
      el.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  function sendButton() {
    const input = composer();
    const root = input?.closest("form") || input?.parentElement?.parentElement || document;
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
    return [...root.querySelectorAll("button")].find(button => visible(button) && /send|submit|发送|生成|arrow-up/i.test(labelOf(button) + " " + (button.innerHTML || ""))) || null;
  }

  function buttonReady(button) {
    return Boolean(button && visible(button) && !button.disabled && button.getAttribute("aria-disabled") !== "true");
  }

  function generationIndicator() {
    const buttons = [...document.querySelectorAll("button")].filter(visible);
    const stop = buttons.find(button => /stop generating|stop|停止生成|停止/i.test(labelOf(button)));
    if (stop) return "stop-button";
    const bodyText = textOf(document.body);
    if (/(正在生成|正在创建图片|generating image|creating image|creating your image|working on your image)/i.test(bodyText)) return "generating-text";
    return "";
  }

  function promptEchoedOutsideComposer(prompt) {
    const input = composer();
    const target = normalize(prompt);
    if (!target) return false;
    const nodes = [...document.querySelectorAll("main p,main div,article p,article div")].filter(visible).slice(-250);
    return nodes.some(node => {
      if (input && (node === input || input.contains(node) || node.contains(input))) return false;
      const text = textOf(node);
      return text === target || (target.length >= 10 && text.includes(target));
    });
  }

  async function waitForComposer(timeout = 30000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const input = composer();
      if (input) return input;
      await delay(180);
    }
    throw new Error("ChatGPT Images composer did not become ready");
  }

  async function writePrompt(active, prompt) {
    const target = normalize(prompt);
    let last = null;
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      const input = await waitForComposer(attempt === 1 ? 30000 : 7000);
      setText(input, prompt);
      const deadline = Date.now() + 3500;
      while (Date.now() < deadline) {
        const current = composer();
        const text = composerText(current);
        const indicator = generationIndicator();
        if (text === target || (target.length > 8 && text.includes(target))) {
          await diagnostic(active, "prompt-ready", { prompt_write_attempts: attempt, composer_chars: text.length });
          return { input: current || input, submittedEarly: false };
        }
        if (!text && (indicator || promptEchoedOutsideComposer(prompt))) {
          await diagnostic(active, "prompt-auto-submitted", { prompt_write_attempts: attempt, generation_indicator: indicator || null });
          return { input: current || input, submittedEarly: true };
        }
        last = { text, indicator };
        await delay(100);
      }
      await delay(250);
    }
    throw new Error(`ChatGPT Images prompt could not be confirmed in the current composer (composer_chars=${last?.text?.length || 0})`);
  }

  function dispatchEnter(input) {
    if (!input) return;
    input.focus();
    for (const type of ["keydown", "keypress", "keyup"]) {
      input.dispatchEvent(new KeyboardEvent(type, {
        key: "Enter", code: "Enter", keyCode: 13, which: 13,
        bubbles: true, cancelable: true,
      }));
    }
  }

  async function submitAndConfirm(active, prompt) {
    const target = normalize(prompt);
    const started = performance.now();
    let strategy = "";
    let clicked = false;
    const readyDeadline = Date.now() + 25000;

    while (Date.now() < readyDeadline) {
      const current = composer();
      const text = composerText(current);
      const indicator = generationIndicator();
      if (!text && (indicator || promptEchoedOutsideComposer(prompt))) {
        return { strategy: "already-submitted", reason: indicator || "prompt-echo", submit_ms: performance.now() - started };
      }
      const button = sendButton();
      if (text && (text === target || text.includes(target)) && buttonReady(button)) {
        strategy = button.dataset?.testid === "send-button" ? "send-button-testid" : "send-button";
        button.click();
        clicked = true;
        await diagnostic(active, "submit-clicked", { submission_strategy: strategy, send_button_label: labelOf(button).slice(0, 120) });
        break;
      }
      await delay(120);
    }

    if (!clicked) {
      const current = composer();
      const text = composerText(current);
      if (current && text && (text === target || text.includes(target))) {
        dispatchEnter(current);
        strategy = "enter-key";
        await diagnostic(active, "submit-enter", { submission_strategy: strategy });
      } else if (generationIndicator() || promptEchoedOutsideComposer(prompt)) {
        return { strategy: "already-submitted", reason: generationIndicator() || "prompt-echo", submit_ms: performance.now() - started };
      } else {
        throw new Error("ChatGPT Images send control never became ready while the prompt was present");
      }
    }

    let deadline = Date.now() + 14000;
    while (Date.now() < deadline) {
      const text = composerText(composer());
      const indicator = generationIndicator();
      if (!text) return { strategy, reason: indicator || "composer-cleared", submit_ms: performance.now() - started };
      if (indicator) return { strategy, reason: indicator, submit_ms: performance.now() - started };
      if (promptEchoedOutsideComposer(prompt)) return { strategy, reason: "prompt-echo", submit_ms: performance.now() - started };
      await delay(120);
    }

    const current = composer();
    const text = composerText(current);
    if (current && text && (text === target || text.includes(target)) && strategy !== "enter-key") {
      dispatchEnter(current);
      strategy += "+enter-fallback";
      deadline = Date.now() + 9000;
      while (Date.now() < deadline) {
        const after = composerText(composer());
        const indicator = generationIndicator();
        if (!after || indicator || promptEchoedOutsideComposer(prompt)) {
          return { strategy, reason: indicator || (!after ? "composer-cleared" : "prompt-echo"), submit_ms: performance.now() - started };
        }
        await delay(120);
      }
    }
    throw new Error("ChatGPT Images prompt submission was not confirmed");
  }

  function imageNodes() {
    return [...document.querySelectorAll("img")].filter(img => {
      const rect = img.getBoundingClientRect();
      const src = img.currentSrc || img.src || "";
      if (!src || /avatar|emoji|icon|logo/i.test(src)) return false;
      return rect.width >= 120 && rect.height >= 120;
    });
  }

  async function stableBaseline() {
    const map = new Map();
    const deadline = Date.now() + 1400;
    while (Date.now() < deadline) {
      for (const img of imageNodes()) map.set(img, img.currentSrc || img.src || "");
      await delay(180);
    }
    return map;
  }

  function changedImageCandidates(baseline) {
    return imageNodes().filter(img => {
      const src = img.currentSrc || img.src || "";
      return !baseline.has(img) || baseline.get(img) !== src;
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
      const baseline = await stableBaseline();
      const written = await writePrompt(active, prompt);
      const submitted = written.submittedEarly
        ? { strategy: "auto-submitted", reason: generationIndicator() || "prompt-echo", submit_ms: 0 }
        : await submitAndConfirm(active, prompt);

      await emit({
        type: "image.started",
        request_id: active.requestId,
        diagnostics: {
          images_page: true,
          image_controller: "image-v3",
          submission_confirmed: true,
          submission_strategy: submitted.strategy,
          submission_reason: submitted.reason,
          submit_ms: Math.round(submitted.submit_ms * 10) / 10,
          baseline_image_nodes: baseline.size,
        },
      });

      const timeout = Math.max(30000, Number(message.options?.timeout_seconds || 300) * 1000);
      const deadline = Date.now() + timeout;
      let lastProgress = 0;
      let generationSeen = Boolean(generationIndicator());
      while (!active.cancelled && Date.now() < deadline) {
        const indicator = generationIndicator();
        if (indicator) generationSeen = true;
        const candidates = changedImageCandidates(baseline);
        const complete = candidates.find(img => img.complete && (img.naturalWidth || 0) >= 256 && (img.naturalHeight || 0) >= 256);
        if (complete && (generationSeen || performance.now() - started > 4500)) {
          const captured = await imageToBase64(complete);
          if (!captured.b64_json || captured.b64_json.length < 100) throw new Error("Generated image appeared but could not be captured as image bytes");
          await emit({
            type: "image.completed",
            request_id: active.requestId,
            images: [captured],
            diagnostics: {
              images_page: true,
              image_controller: "image-v3",
              submission_confirmed: true,
              generation_indicator_seen: generationSeen,
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
            stage: indicator || "generating",
            elapsed_ms: Math.round(performance.now() - started),
            diagnostics: {
              image_controller: "image-v3",
              submission_confirmed: true,
              generation_indicator_seen: generationSeen,
              generated_node_count: candidates.length,
            },
          });
        }
        await delay(450);
      }

      if (active.cancelled) await emit({ type: "image.cancelled", request_id: active.requestId, reason: "Cancelled" });
      else await emit({ type: "image.error", request_id: active.requestId, error: "Timed out waiting for a generated image after confirmed Images submission" });
    } catch (error) {
      await emit({ type: "image.error", request_id: active.requestId, error: String(error?.message || error) });
    } finally {
      if (state.active === active) state.active = null;
    }
  }

  const listener = (message, _sender, sendResponse) => {
    if (message.type === "chat2api.image.ping.v3") {
      sendResponse({ ok: true, controller: "image-v3" });
      return false;
    }
    if (message.type === "chat2api.image.request.v3") {
      run(message);
      sendResponse({ ok: true, controller: "image-v3" });
      return false;
    }
    if (message.type === "chat2api.image.cancel.v3") {
      if (state.active && state.active.requestId === message.requestId) state.active.cancelled = true;
      sendResponse({ ok: true, controller: "image-v3" });
      return false;
    }
    return false;
  };

  chrome.runtime.onMessage.addListener(listener);
  globalThis[KEY] = { state, listener };
})();
