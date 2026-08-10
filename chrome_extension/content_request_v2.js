(() => {
  const KEY = "__CHAT2API_REQUEST_CONTENT_V2__";
  if (globalThis[KEY]) return;

  const baseState = globalThis.__CHAT2API_CONTENT__;
  const baseListener = baseState?.listener;
  if (typeof baseListener !== "function") return;
  try { chrome.runtime.onMessage.removeListener(baseListener); } catch (_) {}

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const state = { active: null, listener: null };
  globalThis[KEY] = state;

  function visible(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function labelOf(element) {
    return `${element?.getAttribute?.("aria-label") || ""} ${element?.getAttribute?.("title") || ""} ${element?.innerText || element?.textContent || ""}`
      .replace(/\s+/g, " ").trim();
  }

  function findComposer() {
    const selectors = [
      "#prompt-textarea",
      "textarea[placeholder]",
      "div[contenteditable='true'][data-lexical-editor='true']",
      "form div[contenteditable='true']",
    ];
    for (const selector of selectors) {
      const element = [...document.querySelectorAll(selector)].find(visible);
      if (element) return element;
    }
    return null;
  }

  function composerText(element = findComposer()) {
    if (!element) return "";
    if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) return String(element.value || "").trim();
    return String(element.innerText || element.textContent || "").replace(/\s+/g, " ").trim();
  }

  function setComposerText(element, text) {
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
    document.execCommand("insertText", false, text);
    if (!(element.textContent || "").trim()) {
      const p = document.createElement("p");
      p.textContent = text;
      element.replaceChildren(p);
    }
    element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
  }

  function rawSendButton() {
    const root = findComposer()?.closest("form[data-type='unified-composer'], form") || document;
    const selectors = [
      "button[data-testid='send-button']",
      "button[aria-label='Send prompt']",
      "button[aria-label*='发送']",
      "button[type='submit']",
    ];
    for (const selector of selectors) {
      const button = [...root.querySelectorAll(selector)].find(visible);
      if (button) return button;
    }
    return [...root.querySelectorAll("button")].find(button => visible(button) && /send prompt|send message|发送提示|发送消息|发送$/i.test(labelOf(button))) || null;
  }

  function buttonReady(button) {
    return Boolean(button && visible(button) && !button.disabled && button.getAttribute("aria-disabled") !== "true" && !/disabled/i.test(button.getAttribute("data-state") || ""));
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
    return [...document.querySelectorAll("button")].find(button => visible(button) && !button.disabled && /stop streaming|stop generating|停止生成|停止回答/i.test(labelOf(button))) || null;
  }

  const isGenerating = () => Boolean(stopButton());
  const userMessageCount = () => document.querySelectorAll("[data-message-author-role='user']").length;
  const attachmentCount = () => document.querySelectorAll("[data-testid*='attachment'], [data-testid*='file-chip'], [aria-label*='Remove file'], [aria-label*='删除文件']").length;

  function pageError() {
    const nodes = [...document.querySelectorAll("[role='alert'], [data-sonner-toast], [data-toast], [class*='toast']")].filter(visible).slice(-10);
    for (const node of nodes) {
      const text = String(node.innerText || node.textContent || "").replace(/\s+/g, " ").trim();
      if (/(出了点问题|发生错误|something went wrong|network error|无法上传|上传失败|failed to upload|couldn.?t upload|cannot upload|请重试|please retry)/i.test(text)) return text.slice(0, 500);
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

  async function ensureTextMode() {
    let composer = findComposer();
    if (composer) return composer;
    const endVoice = [...document.querySelectorAll("button")].find(button => visible(button) && /end voice|exit voice|close voice|结束语音|退出语音|关闭语音/i.test(labelOf(button)));
    if (endVoice) {
      endVoice.click();
      await delay(400);
    }
    composer = await waitFor(findComposer, 20000, 150);
    if (!composer) throw new Error("ChatGPT composer did not become ready");
    return composer;
  }

  async function emit(event) {
    try { await chrome.runtime.sendMessage({ type: "chat2api.event", event }); }
    catch (_) {}
  }

  async function diagnostic(active, stage, extra = {}) {
    await emit({
      type: "chat.diagnostics",
      request_id: active.requestId,
      diagnostics: {
        request_controller: "request-v2",
        submit_stage: stage,
        ...extra,
      },
    });
  }

  async function waitForSendReady(active, composer, maxWaitMs) {
    const started = performance.now();
    let last = {};
    await diagnostic(active, "waiting-ready", {
      submit_wait_timeout_ms: maxWaitMs,
      attachment_ui_count: attachmentCount(),
    });
    const deadline = Date.now() + maxWaitMs;
    while (Date.now() < deadline && !active.cancelled) {
      const error = pageError();
      if (error) throw new Error(`ChatGPT UI error before submit: ${error}`);
      const button = rawSendButton();
      const text = composerText(composer);
      last = {
        send_button_found: Boolean(button),
        send_button_disabled: button ? !buttonReady(button) : null,
        composer_chars: text.length,
        attachment_ui_count: attachmentCount(),
      };
      if (text && buttonReady(button)) {
        const waitMs = Math.round((performance.now() - started) * 10) / 10;
        await diagnostic(active, "ready", { ...last, submit_wait_ms: waitMs });
        return { button, waitMs };
      }
      await delay(150);
    }
    if (active.cancelled) throw new Error("Request cancelled while waiting for ChatGPT send button");
    const waitMs = Math.round((performance.now() - started) * 10) / 10;
    await diagnostic(active, "ready-timeout", { ...last, submit_wait_ms: waitMs });
    throw new Error(`ChatGPT send button did not become ready within ${Math.round(maxWaitMs / 1000)} seconds (button_found=${Boolean(last.send_button_found)}, disabled=${String(last.send_button_disabled)}, composer_chars=${last.composer_chars || 0}, attachments=${last.attachment_ui_count || 0})`);
  }

  async function submitAndConfirm(active, composer) {
    const beforeUsers = userMessageCount();
    const totalStarted = performance.now();
    const requestTimeoutMs = Math.max(15000, Number(active.options.timeout_seconds || 300) * 1000);
    const readinessBudget = Math.max(15000, Math.min(90000, requestTimeoutMs - 5000));
    let attempts = 0;
    let readyWaitMs = 0;

    while (attempts < 3 && !active.cancelled) {
      attempts += 1;
      const ready = await waitForSendReady(active, composer, attempts === 1 ? readinessBudget : 15000);
      readyWaitMs += ready.waitMs;
      ready.button.scrollIntoView?.({ block: "nearest", inline: "nearest" });
      ready.button.click();
      await diagnostic(active, "clicked", {
        send_attempts: attempts,
        send_button_label: labelOf(ready.button).slice(0, 120),
        submit_wait_ms: readyWaitMs,
      });

      const confirmed = await waitFor(() => {
        const users = userMessageCount();
        const text = composerText(composer);
        const generating = isGenerating();
        if (users > beforeUsers) return { reason: "user-message", users, composer_cleared: !text, generating };
        if (!text && generating) return { reason: "composer-cleared+generating", users, composer_cleared: true, generating };
        if (!text) return { reason: "composer-cleared", users, composer_cleared: true, generating };
        return null;
      }, 8000, 120);

      if (confirmed) {
        const submitMs = Math.round((performance.now() - totalStarted) * 10) / 10;
        await diagnostic(active, "confirmed", {
          send_attempts: attempts,
          submit_wait_ms: readyWaitMs,
          submit_total_ms: submitMs,
          submission_confirmed: true,
          submission_confirm_reason: confirmed.reason,
          composer_cleared: confirmed.composer_cleared,
          user_message_observed: confirmed.users > beforeUsers,
          generating_observed: confirmed.generating,
        });
        return { attempts, submitMs, readyWaitMs };
      }

      await diagnostic(active, "retry", {
        send_attempts: attempts,
        submit_wait_ms: readyWaitMs,
        composer_chars: composerText(composer).length,
        send_button_disabled: rawSendButton() ? !buttonReady(rawSendButton()) : null,
      });
      await delay(600);
    }

    throw new Error(`ChatGPT send action was not confirmed after ${attempts} attempts`);
  }

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

  function normalizeText(value) {
    return String(value || "")
      .replace(/[\u200B-\u200D\uFEFF]/g, "")
      .replace(/\r\n?/g, "\n")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function transientText(value) {
    const text = normalizeText(value).replace(/[.。…·:：]+$/g, "").trim().toLowerCase();
    return /^(正在)?(思考|分析|推理|生成|处理|搜索|浏览)(中)?$/.test(text) || /^(thinking|analyzing|reasoning|generating|working|searching|browsing)( now)?$/.test(text);
  }

  function nodeText(node) {
    if (!node) return "";
    for (const selector of ["[data-message-content]", ".markdown", "[class*='markdown']"]) {
      const candidates = [...node.querySelectorAll(selector)].filter(visible);
      for (let i = candidates.length - 1; i >= 0; i -= 1) {
        const text = normalizeText(candidates[i].innerText || candidates[i].textContent || "");
        if (text && !transientText(text)) return text;
      }
    }
    const clone = node.cloneNode(true);
    clone.querySelectorAll("button,svg,nav,footer,[aria-hidden='true'],[data-testid*='copy'],[data-testid*='feedback'],[data-testid*='action']").forEach(el => el.remove());
    const text = normalizeText(clone.innerText || clone.textContent || "");
    return transientText(text) ? "" : text;
  }

  function nodeIdentity(node) {
    const turn = node?.closest("[data-message-id], article[id], article[data-testid]");
    return node?.getAttribute("data-message-id") || turn?.getAttribute("data-message-id") || turn?.id || turn?.getAttribute("data-testid") || "";
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
        const prior = lastText;
        lastText = await updateCapturedText(active, text, lastText);
        if (lastText !== prior || identity !== lastIdentity) {
          stableSince = Date.now();
          lastIdentity = identity;
        }
      }
      if (responseStarted && lastText && !generating && stableSince && Date.now() - stableSince >= 1800) {
        await delay(450);
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

  async function runRequest(message) {
    if (state.active) throw new Error("This ChatGPT tab is already processing another request");
    const prompt = String(message.prompt || "").trim();
    if (!prompt) throw new Error("Prompt is empty");
    if (isGenerating()) throw new Error("ChatGPT is already generating a response");

    const before = assistantNodes();
    const active = {
      requestId: message.requestId,
      options: message.options || {},
      baselineCount: before.length,
      baselineIdentity: nodeIdentity(before[before.length - 1]),
      cancelled: false,
    };
    state.active = active;
    try {
      const composer = await ensureTextMode();
      setComposerText(composer, prompt);
      const textReady = await waitFor(() => composerText(composer).length ? true : null, 8000, 100);
      if (!textReady) throw new Error("Prompt was inserted but ChatGPT composer did not retain it");
      await diagnostic(active, "prompt-ready", {
        composer_chars: composerText(composer).length,
        attachment_ui_count: attachmentCount(),
      });
      await submitAndConfirm(active, composer);
      await monitor(active);
    } finally {
      if (state.active === active) state.active = null;
    }
  }

  async function cancelRequest(requestId) {
    if (!state.active || state.active.requestId !== requestId) return;
    state.active.cancelled = true;
    try { stopButton()?.click(); } catch (_) {}
  }

  const listener = (message, sender, sendResponse) => {
    if (message.type === "chat2api.request") {
      runRequest(message).catch(error => emit({
        type: "chat.error",
        request_id: message.requestId,
        error: String(error?.message || error),
      }));
      sendResponse({ ok: true, controller: "request-v2" });
      return false;
    }
    if (message.type === "chat2api.cancel") {
      cancelRequest(message.requestId).then(() => sendResponse({ ok: true, controller: "request-v2" }));
      return true;
    }
    return baseListener(message, sender, sendResponse);
  };

  state.listener = listener;
  chrome.runtime.onMessage.addListener(listener);
  if (baseState) {
    baseState.listener = listener;
    baseState.stop = () => {
      try { chrome.runtime.onMessage.removeListener(listener); } catch (_) {}
      state.active = null;
    };
  }
})();
