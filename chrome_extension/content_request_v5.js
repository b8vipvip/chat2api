(() => {
  const KEY = "__CHAT2API_REQUEST_CONTENT_V5__";
  if (globalThis[KEY]) return;

  const v4 = globalThis.__CHAT2API_REQUEST_CONTENT_V4__;
  const priorListener = v4?.listener;
  if (typeof priorListener === "function") {
    try { chrome.runtime.onMessage.removeListener(priorListener); } catch (_) {}
  }

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const state = { active: null, listener: null };
  globalThis[KEY] = state;

  function visible(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || null;
  }

  function findComposer() {
    const root = composerRoot() || document;
    for (const selector of [
      "#prompt-textarea",
      "textarea[placeholder]",
      "div[contenteditable='true'][data-lexical-editor='true']",
      "div[contenteditable='true'].ProseMirror",
      "[contenteditable='true']",
    ]) {
      const found = [...root.querySelectorAll(selector)].find(visible);
      if (found) return found;
    }
    return null;
  }

  function composerText(element = findComposer()) {
    if (!element) return "";
    if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) return normalize(element.value || "");
    return normalize(element.innerText || element.textContent || "");
  }

  function setComposerText(element, text) {
    if (!element) return;
    element.focus();
    if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) {
      const proto = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(element, text);
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(element);
    selection.removeAllRanges();
    selection.addRange(range);
    if (text) document.execCommand("insertText", false, text);
    else document.execCommand("delete", false);
    if (text && !normalize(element.textContent || "")) {
      const p = document.createElement("p");
      p.textContent = text;
      element.replaceChildren(p);
    }
    if (!text && normalize(element.textContent || "")) element.replaceChildren();
    try {
      element.dispatchEvent(new InputEvent("input", {
        bubbles: true,
        inputType: text ? "insertText" : "deleteContentBackward",
        data: text || null,
      }));
    } catch (_) {
      element.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  function labelOf(element) {
    return normalize(`${element?.dataset?.testid || ""} ${element?.getAttribute?.("aria-label") || ""} ${element?.title || ""} ${element?.innerText || element?.textContent || ""}`);
  }

  function sendButton() {
    const root = composerRoot() || document;
    for (const selector of [
      "button[data-testid='send-button']",
      "button[aria-label='Send prompt']",
      "button[aria-label*='发送提示']",
      "button[aria-label*='发送消息']",
      "button[type='submit']",
    ]) {
      const button = [...root.querySelectorAll(selector)].find(visible);
      if (button) return button;
    }
    return [...root.querySelectorAll("button")]
      .find(button => visible(button) && /send prompt|send message|发送提示|发送消息|发送$/i.test(labelOf(button))) || null;
  }

  function buttonReady(button) {
    return Boolean(button && visible(button) && !button.disabled && button.getAttribute("aria-disabled") !== "true");
  }

  function stopButton() {
    for (const selector of [
      "button[data-testid='stop-button']",
      "button[aria-label='Stop streaming']",
      "button[aria-label='Stop generating']",
      "button[aria-label*='停止生成']",
    ]) {
      const button = [...document.querySelectorAll(selector)].find(item => visible(item) && !item.disabled);
      if (button) return button;
    }
    return null;
  }

  const isGenerating = () => Boolean(stopButton());

  function assistantNodes() {
    const result = [];
    const seen = new Set();
    for (const selector of ["[data-message-author-role='assistant']", "article[data-testid^='conversation-turn'] [data-message-author-role='assistant']"]) {
      for (const node of document.querySelectorAll(selector)) {
        if (!seen.has(node) && visible(node)) {
          seen.add(node);
          result.push(node);
        }
      }
    }
    return result;
  }

  function nodeIdentity(node) {
    const turn = node?.closest("[data-message-id], article[id], article[data-testid]");
    return node?.getAttribute("data-message-id") || turn?.getAttribute("data-message-id") || turn?.id || turn?.getAttribute("data-testid") || "";
  }

  function transientText(value) {
    const text = normalize(value).replace(/[.。…·:：]+$/g, "").trim().toLowerCase();
    return /^(正在)?(思考|分析|推理|生成|处理|搜索|浏览)(中)?$/.test(text) || /^(thinking|analyzing|reasoning|generating|working|searching|browsing)( now)?$/.test(text);
  }

  function nodeText(node) {
    if (!node) return "";
    for (const selector of ["[data-message-content]", ".markdown", "[class*='markdown']"]) {
      const candidates = [...node.querySelectorAll(selector)].filter(visible);
      for (let i = candidates.length - 1; i >= 0; i -= 1) {
        const text = normalize(candidates[i].innerText || candidates[i].textContent || "");
        if (text && !transientText(text)) return text;
      }
    }
    const clone = node.cloneNode(true);
    clone.querySelectorAll("button,svg,nav,footer,[aria-hidden='true'],[data-testid*='copy'],[data-testid*='feedback'],[data-testid*='action']").forEach(el => el.remove());
    const text = normalize(clone.innerText || clone.textContent || "");
    return transientText(text) ? "" : text;
  }

  function pageError() {
    const nodes = [...document.querySelectorAll("[role='alert'],[data-sonner-toast],[data-toast],[class*='toast']")].filter(visible).slice(-10);
    for (const node of nodes) {
      const text = normalize(node.innerText || node.textContent || "");
      if (/(出了点问题|发生错误|something went wrong|network error|无法上传|上传失败|failed to upload|请重试|please retry)/i.test(text)) return text.slice(0, 500);
    }
    return "";
  }

  async function waitFor(predicate, timeout = 10000, interval = 120) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const value = predicate();
      if (value) return value;
      await delay(interval);
    }
    return null;
  }

  async function ensureComposer(timeout = 25000) {
    const found = await waitFor(findComposer, timeout, 120);
    if (!found) throw new Error("ChatGPT composer did not become ready");
    return found;
  }

  async function emit(event) {
    try { await chrome.runtime.sendMessage({ type: "chat2api.event", event }); } catch (_) {}
  }

  async function diagnostic(active, stage, extra = {}) {
    await emit({
      type: "chat.diagnostics",
      request_id: active.requestId,
      diagnostics: { request_controller: "request-v5", submit_stage: stage, ...extra },
    });
  }

  function dispatchEnter(element) {
    if (!element) return;
    element.focus();
    for (const type of ["keydown", "keypress", "keyup"]) {
      element.dispatchEvent(new KeyboardEvent(type, {
        key: "Enter", code: "Enter", keyCode: 13, which: 13,
        bubbles: true, cancelable: true,
      }));
    }
  }

  function refreshAssistantBaseline(active) {
    const nodes = assistantNodes();
    active.baselineCount = nodes.length;
    active.baselineIdentity = nodeIdentity(nodes[nodes.length - 1]);
    active.baselineIds = new Set(nodes.map(nodeIdentity).filter(Boolean));
  }

  function newAssistantState(active) {
    const nodes = assistantNodes();
    const latest = nodes[nodes.length - 1];
    const identity = nodeIdentity(latest);
    const isNew = Boolean(latest) && (
      nodes.length > active.baselineCount ||
      (identity && !active.baselineIds.has(identity)) ||
      (identity && active.baselineIdentity && identity !== active.baselineIdentity)
    );
    return { nodes, latest, identity, isNew };
  }

  async function writePrompt(active, prompt) {
    let composer = await ensureComposer();
    const target = normalize(prompt);
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      setComposerText(composer, prompt);
      const retained = await waitFor(() => {
        const current = findComposer() || composer;
        const text = composerText(current);
        if (text === target || (target.length > 6 && text.includes(target))) return { composer: current, text };
        return null;
      }, 3500, 100);
      if (retained) {
        await diagnostic(active, "prompt-ready", {
          prompt_write_attempts: attempt,
          composer_chars: retained.text.length,
          submission_observed_during_write: false,
        });
        return retained;
      }
      composer = await ensureComposer(5000);
      await delay(180);
    }
    throw new Error("Prompt insertion could not be confirmed in the current ChatGPT composer");
  }

  function promptStillPresent(prompt) {
    const text = composerText(findComposer());
    const target = normalize(prompt);
    return Boolean(text && (text === target || (target.length > 6 && text.includes(target))));
  }

  async function waitAfterSend(active, prompt, reasonPrefix, timeout = 6000) {
    return waitFor(() => {
      const text = composerText(findComposer());
      const stillPresent = promptStillPresent(prompt);
      if (!stillPresent && !text) return { reason: `${reasonPrefix}-composer-cleared`, composerCleared: true, generating: isGenerating() };
      if (!stillPresent && isGenerating()) return { reason: `${reasonPrefix}-generating`, composerCleared: !text, generating: true };
      return null;
    }, timeout, 100);
  }

  async function submitAndConfirm(active, prompt) {
    const target = normalize(prompt);
    const totalStarted = performance.now();
    const requestTimeoutMs = Math.max(15000, Number(active.options.timeout_seconds || 300) * 1000);
    const readinessBudget = Math.max(15000, Math.min(90000, requestTimeoutMs - 5000));
    let attempts = 0;
    let enterFallbackUsed = false;

    while (attempts < 3 && !active.cancelled) {
      attempts += 1;
      const readyStarted = performance.now();
      const ready = await waitFor(() => {
        const current = findComposer();
        const text = composerText(current);
        const button = sendButton();
        const hasPrompt = text === target || (target.length > 6 && text.includes(target));
        if (current && hasPrompt && buttonReady(button)) return { current, text, button };
        return null;
      }, attempts === 1 ? readinessBudget : 15000, 120);

      if (!ready) {
        throw new Error(`ChatGPT send button did not become ready while this request's prompt remained in the composer (composer_chars=${composerText(findComposer()).length}, button_found=${Boolean(sendButton())}, disabled=${sendButton() ? !buttonReady(sendButton()) : null})`);
      }

      // Refresh the assistant baseline immediately before the real send. Historical
      // conversation hydration before this point must never count as this response.
      refreshAssistantBaseline(active);
      const waitMs = Math.round((performance.now() - readyStarted) * 10) / 10;
      ready.button.scrollIntoView?.({ block: "nearest", inline: "nearest" });
      ready.button.click();
      await diagnostic(active, "clicked", {
        send_attempts: attempts,
        send_button_label: labelOf(ready.button).slice(0, 120),
        submit_wait_ms: waitMs,
        hydration_safe_baseline: true,
      });

      let confirmed = await waitAfterSend(active, prompt, "click", 6000);
      if (!confirmed && promptStillPresent(prompt)) {
        dispatchEnter(findComposer());
        enterFallbackUsed = true;
        await diagnostic(active, "enter-fallback", {
          send_attempts: attempts,
          enter_fallback_used: true,
          composer_chars: composerText(findComposer()).length,
        });
        confirmed = await waitAfterSend(active, prompt, "enter", 6000);
      }

      if (confirmed) {
        await diagnostic(active, "confirmed", {
          send_attempts: attempts,
          submit_total_ms: Math.round((performance.now() - totalStarted) * 10) / 10,
          submission_confirmed: true,
          submission_confirm_reason: confirmed.reason,
          composer_cleared: confirmed.composerCleared,
          generating_observed: confirmed.generating,
          enter_fallback_used: enterFallbackUsed,
          historical_hydration_ignored: true,
        });
        return;
      }
      await delay(400);
    }
    throw new Error(`ChatGPT send action was not confirmed after ${attempts} attempts`);
  }

  async function updateCapturedText(active, text, previous) {
    if (!text || text === previous) return previous;
    if (text.startsWith(previous)) {
      const deltaText = text.slice(previous.length);
      if (deltaText) await emit({ type: "chat.delta", request_id: active.requestId, delta: deltaText });
    } else {
      await emit({ type: "chat.snapshot", request_id: active.requestId, text });
    }
    return text;
  }

  async function monitor(active) {
    const timeoutMs = Math.max(5000, Number(active.options.timeout_seconds || 300) * 1000);
    const startedAt = Date.now();
    let responseStarted = false;
    let lastText = "";
    let lastIdentity = "";
    let stableSince = 0;

    while (!active.cancelled && Date.now() - startedAt < timeoutMs) {
      const error = pageError();
      if (error && responseStarted) throw new Error(`ChatGPT response UI error: ${error}`);
      const latestState = newAssistantState(active);
      const text = latestState.isNew ? nodeText(latestState.latest) : "";
      const generating = isGenerating();

      if ((latestState.isNew || generating) && !responseStarted) {
        responseStarted = true;
        await emit({ type: "chat.started", request_id: active.requestId });
      }
      if (text) {
        const previous = lastText;
        lastText = await updateCapturedText(active, text, lastText);
        if (lastText !== previous || latestState.identity !== lastIdentity) {
          stableSince = Date.now();
          lastIdentity = latestState.identity;
        }
      }
      if (responseStarted && lastText && !generating && stableSince && Date.now() - stableSince >= 1500) {
        await delay(300);
        const finalState = newAssistantState(active);
        const finalText = finalState.isNew ? nodeText(finalState.latest) : "";
        if (!isGenerating() && finalText) {
          lastText = await updateCapturedText(active, finalText, lastText);
          await emit({ type: "chat.completed", request_id: active.requestId, text: lastText });
          return;
        }
      }
      await delay(120);
    }

    if (active.cancelled) await emit({ type: "chat.cancelled", request_id: active.requestId, reason: "Cancelled by API client" });
    else await emit({ type: "chat.error", request_id: active.requestId, error: "Timed out waiting for ChatGPT response" });
  }

  async function runRequest(message) {
    if (state.active) throw new Error("This ChatGPT tab is already handling another request");
    const prompt = String(message.prompt || "").trim();
    if (!prompt) throw new Error("Prompt is empty");
    const active = {
      requestId: message.requestId,
      options: message.options || {},
      cancelled: false,
      baselineCount: 0,
      baselineIdentity: "",
      baselineIds: new Set(),
    };
    state.active = active;
    if (v4) {
      v4.lastPrompt = prompt;
      v4.lastAttachmentNames = Array.isArray(message.options?.chat2api_diagnostics?.attachment_names)
        ? [...message.options.chat2api_diagnostics.attachment_names]
        : [];
    }

    try {
      await writePrompt(active, prompt);
      await submitAndConfirm(active, prompt);
      await monitor(active);
    } catch (error) {
      await emit({ type: "chat.error", request_id: active.requestId, error: String(error?.message || error) });
    } finally {
      if (state.active === active) state.active = null;
    }
  }

  const listener = (message, sender, sendResponse) => {
    if (message.type === "chat2api.request") {
      runRequest(message);
      sendResponse({ ok: true, controller: "request-v5" });
      return false;
    }
    if (message.type === "chat2api.cancel") {
      if (state.active && state.active.requestId === message.requestId) state.active.cancelled = true;
      sendResponse({ ok: true, controller: "request-v5" });
      return false;
    }
    if (typeof priorListener === "function") return priorListener(message, sender, sendResponse);
    return false;
  };

  state.listener = listener;
  chrome.runtime.onMessage.addListener(listener);
})();
