(() => {
  const KEY = "__CHAT2API_REQUEST_CONTENT_V4__";
  if (globalThis[KEY]) return;

  const v3 = globalThis.__CHAT2API_REQUEST_CONTENT_V3__;
  const priorListener = v3?.listener;
  if (typeof priorListener === "function") {
    try { chrome.runtime.onMessage.removeListener(priorListener); } catch (_) {}
  }

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const state = {
    active: null,
    listener: null,
    lastPrompt: String(v3?.lastPrompt || ""),
    lastAttachmentNames: Array.isArray(v3?.lastAttachmentNames) ? [...v3.lastAttachmentNames] : [],
  };
  globalThis[KEY] = state;

  function visible(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  const normalize = value => String(value || "").replace(/[\u200B-\u200D\uFEFF]/g, "").replace(/\s+/g, " ").trim();
  const delayValue = (value, ms = 0) => new Promise(resolve => setTimeout(() => resolve(value), ms));

  function labelOf(element) {
    return `${element?.dataset?.testid || ""} ${element?.getAttribute?.("aria-label") || ""} ${element?.getAttribute?.("title") || ""} ${element?.innerText || element?.textContent || ""}`
      .replace(/\s+/g, " ").trim();
  }

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || null;
  }

  function findComposer() {
    const root = composerRoot() || document;
    const selectors = [
      "#prompt-textarea",
      "textarea[placeholder]",
      "div[contenteditable='true'][data-lexical-editor='true']",
      "div[contenteditable='true'].ProseMirror",
      "[contenteditable='true']",
    ];
    for (const selector of selectors) {
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
    element.dispatchEvent(new InputEvent("input", {
      bubbles: true,
      inputType: text ? "insertText" : "deleteContentBackward",
      data: text || null,
    }));
  }

  function sendButton() {
    const root = composerRoot() || document;
    const selectors = [
      "button[data-testid='send-button']",
      "button[aria-label='Send prompt']",
      "button[aria-label*='发送提示']",
      "button[aria-label*='发送消息']",
      "button[type='submit']",
    ];
    for (const selector of selectors) {
      const button = [...root.querySelectorAll(selector)].find(visible);
      if (button) return button;
    }
    return [...root.querySelectorAll("button")].find(button => visible(button) && /send prompt|send message|发送提示|发送消息|发送$/i.test(labelOf(button))) || null;
  }

  function buttonReady(button) {
    return Boolean(button && visible(button) && !button.disabled && button.getAttribute("aria-disabled") !== "true");
  }

  function stopButton() {
    const selectors = [
      "button[data-testid='stop-button']",
      "button[aria-label='Stop streaming']",
      "button[aria-label='Stop generating']",
      "button[aria-label*='停止生成']",
    ];
    for (const selector of selectors) {
      const button = [...document.querySelectorAll(selector)].find(item => visible(item) && !item.disabled);
      if (button) return button;
    }
    return null;
  }

  const isGenerating = () => Boolean(stopButton());
  const userMessageCount = () => document.querySelectorAll("[data-message-author-role='user']").length;

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
    const nodes = [...document.querySelectorAll("[role='alert'], [data-sonner-toast], [data-toast], [class*='toast']")].filter(visible).slice(-10);
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
      diagnostics: {
        request_controller: "request-v4",
        submit_stage: stage,
        ...extra,
      },
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

  function submittedEvidence(active) {
    const users = userMessageCount();
    const assistants = assistantNodes();
    const latest = assistants[assistants.length - 1];
    const identity = nodeIdentity(latest);
    const newAssistant = assistants.length > active.baselineCount || Boolean(latest && identity && identity !== active.baselineIdentity);
    const generating = isGenerating();
    return {
      submitted: users > active.beforeUsers || generating || newAssistant,
      users,
      generating,
      newAssistant,
    };
  }

  async function writePrompt(active, prompt) {
    let composer = await ensureComposer();
    const normalizedPrompt = normalize(prompt);
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      setComposerText(composer, prompt);
      const result = await waitFor(() => {
        const evidence = submittedEvidence(active);
        if (evidence.submitted) return { submitted: true, evidence };
        const current = findComposer() || composer;
        const text = composerText(current);
        if (text === normalizedPrompt || (normalizedPrompt.length > 6 && text.includes(normalizedPrompt))) {
          return { submitted: false, composer: current, text };
        }
        return null;
      }, 3500, 100);
      if (result) {
        await diagnostic(active, result.submitted ? "prompt-auto-submitted" : "prompt-ready", {
          prompt_write_attempts: attempt,
          composer_chars: result.text?.length || composerText(findComposer()).length,
          submission_observed_during_write: Boolean(result.submitted),
        });
        return result;
      }
      composer = await ensureComposer(5000);
      await delay(180);
    }
    const evidence = submittedEvidence(active);
    if (evidence.submitted) return { submitted: true, evidence };
    throw new Error("Prompt insertion could not be confirmed in the current ChatGPT composer");
  }

  async function submitAndConfirm(active, prompt) {
    const normalizedPrompt = normalize(prompt);
    const totalStarted = performance.now();
    const requestTimeoutMs = Math.max(15000, Number(active.options.timeout_seconds || 300) * 1000);
    const readinessBudget = Math.max(15000, Math.min(90000, requestTimeoutMs - 5000));
    let attempts = 0;
    let enterFallbackUsed = false;

    while (attempts < 3 && !active.cancelled) {
      attempts += 1;
      const readyStarted = performance.now();
      const ready = await waitFor(() => {
        const evidence = submittedEvidence(active);
        if (evidence.submitted) return { alreadySubmitted: true, evidence };
        const current = findComposer();
        const text = composerText(current);
        const button = sendButton();
        if (current && text && buttonReady(button)) return { current, text, button };
        return null;
      }, attempts === 1 ? readinessBudget : 15000, 120);

      if (!ready) {
        const current = findComposer();
        throw new Error(`ChatGPT send button did not become ready (composer_chars=${composerText(current).length}, button_found=${Boolean(sendButton())}, disabled=${sendButton() ? !buttonReady(sendButton()) : null})`);
      }
      if (ready.alreadySubmitted) {
        await diagnostic(active, "confirmed-before-click", {
          send_attempts: attempts - 1,
          submission_confirmed: true,
          submission_confirm_reason: "page-state",
          user_message_observed: ready.evidence.users > active.beforeUsers,
          generating_observed: ready.evidence.generating,
          assistant_observed: ready.evidence.newAssistant,
        });
        return;
      }

      const waitMs = Math.round((performance.now() - readyStarted) * 10) / 10;
      ready.button.scrollIntoView?.({ block: "nearest", inline: "nearest" });
      ready.button.click();
      await diagnostic(active, "clicked", {
        send_attempts: attempts,
        send_button_label: labelOf(ready.button).slice(0, 120),
        submit_wait_ms: waitMs,
      });

      let confirmed = await waitFor(() => {
        const evidence = submittedEvidence(active);
        const current = findComposer();
        const text = composerText(current);
        if (evidence.submitted) return { reason: "page-state", evidence, composerCleared: !text };
        if (!text) return { reason: "composer-cleared", evidence, composerCleared: true };
        return null;
      }, 5500, 120);

      if (!confirmed) {
        const current = findComposer();
        const text = composerText(current);
        if (current && (text === normalizedPrompt || text.includes(normalizedPrompt))) {
          dispatchEnter(current);
          enterFallbackUsed = true;
          await diagnostic(active, "enter-fallback", { send_attempts: attempts, enter_fallback_used: true, composer_chars: text.length });
          confirmed = await waitFor(() => {
            const evidence = submittedEvidence(active);
            const latestComposer = findComposer();
            const latestText = composerText(latestComposer);
            if (evidence.submitted) return { reason: "page-state-after-enter", evidence, composerCleared: !latestText };
            if (!latestText) return { reason: "composer-cleared-after-enter", evidence, composerCleared: true };
            return null;
          }, 5500, 120);
        }
      }

      if (confirmed) {
        await diagnostic(active, "confirmed", {
          send_attempts: attempts,
          submit_total_ms: Math.round((performance.now() - totalStarted) * 10) / 10,
          submission_confirmed: true,
          submission_confirm_reason: confirmed.reason,
          composer_cleared: confirmed.composerCleared,
          user_message_observed: confirmed.evidence.users > active.beforeUsers,
          generating_observed: confirmed.evidence.generating,
          assistant_observed: confirmed.evidence.newAssistant,
          enter_fallback_used: enterFallbackUsed,
        });
        return;
      }
      await delay(500);
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
      const nodes = assistantNodes();
      const latest = nodes[nodes.length - 1];
      const identity = nodeIdentity(latest);
      const isNew = nodes.length > active.baselineCount || Boolean(latest && identity && identity !== active.baselineIdentity);
      const text = isNew ? nodeText(latest) : "";
      const generating = isGenerating();

      if ((isNew || generating) && !responseStarted) {
        responseStarted = true;
        await emit({ type: "chat.started", request_id: active.requestId });
      }
      if (text) {
        const previous = lastText;
        lastText = await updateCapturedText(active, text, lastText);
        if (lastText !== previous || identity !== lastIdentity) {
          stableSince = Date.now();
          lastIdentity = identity;
        }
      }
      if (responseStarted && lastText && !generating && stableSince && Date.now() - stableSince >= 1500) {
        await delay(350);
        const finalNode = assistantNodes().slice(-1)[0];
        const finalText = nodeText(finalNode);
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

  async function cleanupPreviousAutomationDraft() {
    const composer = findComposer();
    const currentText = normalize(composerText(composer));
    const previousText = normalize(state.lastPrompt);
    const multimodal = globalThis.__CHAT2API_MULTIMODAL_V3__;
    let staleDraftRecovered = false;
    let removedAttachments = 0;

    if (currentText) {
      if (previousText && currentText === previousText) {
        setComposerText(composer, "");
        await delay(150);
        staleDraftRecovered = !composerText(findComposer());
        if (!staleDraftRecovered) throw new Error("Unable to clear the previous chat2api automation draft");
      } else {
        throw new Error("ChatGPT composer contains a manual or unknown draft; refusing to overwrite it");
      }
    }

    if (state.lastAttachmentNames.length && typeof multimodal?.removeAttachmentsByName === "function") {
      const result = await multimodal.removeAttachmentsByName(state.lastAttachmentNames);
      removedAttachments = Number(result?.removed || 0);
    }
    return { stale_draft_recovered: staleDraftRecovered, stale_attachments_removed: removedAttachments };
  }

  async function preflight(message) {
    const recovered = await cleanupPreviousAutomationDraft();
    await emit({
      type: "chat.diagnostics",
      request_id: message.requestId,
      diagnostics: { request_controller_overlay: "request-v4", submit_recovery_stage: "preflight-ready", ...recovered },
    });
    return recovered;
  }

  async function runRequest(message) {
    if (state.active) throw new Error("This ChatGPT tab is already handling another request");
    const prompt = String(message.prompt || "").trim();
    if (!prompt) throw new Error("Prompt is empty");
    const before = assistantNodes();
    const active = {
      requestId: message.requestId,
      options: message.options || {},
      beforeUsers: userMessageCount(),
      baselineCount: before.length,
      baselineIdentity: nodeIdentity(before[before.length - 1]),
      cancelled: false,
    };
    state.active = active;
    state.lastPrompt = prompt;
    state.lastAttachmentNames = Array.isArray(message.options?.chat2api_diagnostics?.attachment_names)
      ? [...message.options.chat2api_diagnostics.attachment_names]
      : [];

    try {
      const written = await writePrompt(active, prompt);
      if (!written.submitted) await submitAndConfirm(active, prompt);
      else await diagnostic(active, "confirmed-during-write", { submission_confirmed: true, submission_confirm_reason: "page-state-during-write" });
      await monitor(active);
    } catch (error) {
      await emit({ type: "chat.error", request_id: active.requestId, error: String(error?.message || error) });
    } finally {
      if (state.active === active) state.active = null;
    }
  }

  const listener = (message, sender, sendResponse) => {
    if (message.type === "chat2api.request.preflight") {
      preflight(message)
        .then(data => sendResponse({ ok: true, data, controller: "request-v4" }))
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error), controller: "request-v4" }));
      return true;
    }
    if (message.type === "chat2api.request") {
      runRequest(message);
      sendResponse({ ok: true, controller: "request-v4" });
      return false;
    }
    if (message.type === "chat2api.cancel") {
      if (state.active && state.active.requestId === message.requestId) state.active.cancelled = true;
      sendResponse({ ok: true, controller: "request-v4" });
      return false;
    }
    if (typeof priorListener === "function") return priorListener(message, sender, sendResponse);
    return false;
  };

  state.listener = listener;
  chrome.runtime.onMessage.addListener(listener);
})();
