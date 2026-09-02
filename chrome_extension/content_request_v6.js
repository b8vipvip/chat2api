(() => {
  const KEY = "__CHAT2API_REQUEST_CONTENT_V6__";
  if (globalThis[KEY]) return;

  const v5 = globalThis.__CHAT2API_REQUEST_CONTENT_V5__;
  const priorListener = v5?.listener;
  if (typeof priorListener === "function") {
    try { chrome.runtime.onMessage.removeListener(priorListener); } catch (_) {}
  }

  const rich = globalThis.__CHAT2API_RICH_RESPONSE_V69__ || null;
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const state = { active: null, listener: null, contract: null, revision: 69 };
  globalThis[KEY] = state;

  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  function visible(element) {
    if (!element) return false;
    try {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
    } catch (_) {
      return false;
    }
  }

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

  function conversationTurns() {
    const result = [];
    const seen = new Set();
    for (const selector of [
      "article[data-testid^='conversation-turn']",
      "[data-testid^='conversation-turn']",
      "article[data-message-id]",
    ]) {
      for (const turn of document.querySelectorAll(selector)) {
        if (seen.has(turn)) continue;
        seen.add(turn);
        result.push(turn);
      }
    }
    return result;
  }

  function userTurn(turn) {
    return turn?.getAttribute?.("data-message-author-role") === "user"
      || Boolean(turn?.querySelector?.("[data-message-author-role='user']"));
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

  function turnOf(node) {
    return node?.closest?.("article[data-testid^='conversation-turn'],[data-testid^='conversation-turn'],article[data-message-id]") || node || null;
  }

  function nodeIdentity(node) {
    const turn = turnOf(node);
    return String(
      turn?.getAttribute?.("data-message-id")
      || turn?.dataset?.messageId
      || turn?.id
      || turn?.getAttribute?.("data-testid")
      || node?.getAttribute?.("data-message-id")
      || ""
    );
  }

  function transientText(value) {
    const text = normalize(value).replace(/[.。…·:：]+$/g, "").trim().toLowerCase();
    return /^(正在)?(思考|分析|推理|生成|处理|搜索|浏览)(中)?$/.test(text)
      || /^(thinking|analyzing|reasoning|generating|working|searching|browsing)( now)?$/.test(text);
  }

  function plainNodeText(node) {
    if (!node) return "";
    const markdown = typeof rich?.extractMarkdown === "function" ? rich.extractMarkdown(node) : "";
    if (markdown && !transientText(markdown)) return markdown;
    for (const selector of ["[data-message-content]", ".markdown", "[class*='markdown']"]) {
      const candidates = [...node.querySelectorAll?.(selector) || []].filter(visible);
      for (let index = candidates.length - 1; index >= 0; index -= 1) {
        const text = normalize(candidates[index].innerText || candidates[index].textContent || "");
        if (text && !transientText(text)) return text;
      }
    }
    const text = normalize(node.innerText || node.textContent || "");
    return transientText(text) ? "" : text;
  }

  function finalNodeText(node) {
    if (typeof rich?.captureFinalMarkdown === "function") return rich.captureFinalMarkdown(node);
    return Promise.resolve({ text: plainNodeText(node), image_inlined_count: 0, image_inlined_bytes: 0 });
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
      diagnostics: {
        request_controller: "request-v6",
        response_epoch_revision: 69,
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

  function turnFollows(anchor, candidate) {
    if (!candidate) return false;
    if (!anchor) return true;
    if (anchor === candidate) return false;
    try {
      return Boolean(anchor.compareDocumentPosition(candidate) & Node.DOCUMENT_POSITION_FOLLOWING);
    } catch (_) {
      return false;
    }
  }

  function promptMatchesTurn(turn, active) {
    if (!turn || !active?.promptNormalized) return false;
    const role = turn.querySelector?.("[data-message-author-role='user']") || turn;
    const text = normalize(role?.innerText || role?.textContent || "");
    if (!text) return false;
    return text === active.promptNormalized
      || (active.promptNormalized.length > 6 && text.includes(active.promptNormalized));
  }

  function currentUserTurn(active) {
    const users = conversationTurns().filter(userTurn);
    for (let index = users.length - 1; index >= 0; index -= 1) {
      if (promptMatchesTurn(users[index], active)) return users[index];
    }
    return null;
  }

  function refreshAssistantBaseline(active) {
    const nodes = assistantNodes();
    const latest = nodes[nodes.length - 1] || null;
    active.baselineCount = nodes.length;
    active.baselineIds = new Set(nodes.map(nodeIdentity).filter(Boolean));
    active.baselineNodes = new Set(nodes);
    active.baselineLatestNode = latest;
    active.baselineLatestTurn = turnOf(latest);
    active.baselineIdentity = nodeIdentity(latest);
    active.baselineLatestText = latest ? plainNodeText(latest) : "";
    active.baselineCapturedAt = Date.now();
  }

  function currentAssistantState(active) {
    const nodes = assistantNodes();
    if (!nodes.length) return { nodes, latest: null, turn: null, identity: "", text: "", isNew: false, reason: "none" };

    const currentUser = currentUserTurn(active);
    if (currentUser) {
      const afterUser = nodes.filter(node => turnFollows(currentUser, turnOf(node)));
      if (afterUser.length) {
        const latest = afterUser[afterUser.length - 1];
        return {
          nodes,
          latest,
          turn: turnOf(latest),
          identity: nodeIdentity(latest),
          text: plainNodeText(latest),
          isNew: true,
          reason: "after-current-user-turn",
        };
      }
    }

    const latest = nodes[nodes.length - 1];
    const turn = turnOf(latest);
    const identity = nodeIdentity(latest);
    const text = plainNodeText(latest);
    const identityNew = Boolean(identity && !active.baselineIds.has(identity));
    const sameIdentityChanged = Boolean(
      identity && active.baselineIdentity && identity === active.baselineIdentity
      && text && active.baselineLatestText && text !== active.baselineLatestText
      && isGenerating()
    );
    const noIdentityNodeAfterBaseline = Boolean(
      !identity && !active.baselineNodes.has(latest) && turnFollows(active.baselineLatestTurn, turn)
    );
    const noBaseline = !active.baselineLatestNode && Boolean(latest);
    const isNew = noBaseline || identityNew || sameIdentityChanged || noIdentityNodeAfterBaseline;
    return {
      nodes,
      latest,
      turn,
      identity,
      text: isNew ? text : "",
      isNew,
      reason: noBaseline ? "no-baseline" : identityNew ? "new-turn-identity" : sameIdentityChanged ? "same-turn-generation-change" : noIdentityNodeAfterBaseline ? "new-node-after-baseline" : "historical-turn",
    };
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
        await diagnostic(active, "prompt-ready", { prompt_write_attempts: attempt, composer_chars: retained.text.length });
        return retained;
      }
      composer = await ensureComposer(5000);
      await delay(180);
    }
    throw new Error("Prompt insertion could not be confirmed in the current ChatGPT composer");
  }

  function promptStillPresent(active) {
    const text = composerText(findComposer());
    const target = active.promptNormalized;
    return Boolean(text && (text === target || (target.length > 6 && text.includes(target))));
  }

  function classifySubmissionState(active) {
    if (promptStillPresent(active)) return null;
    const current = currentAssistantState(active);
    if (isGenerating()) return { reason: "generating", composerCleared: composerText(findComposer()).length === 0, generating: true, current };
    if (current.isNew) return { reason: current.reason, composerCleared: composerText(findComposer()).length === 0, generating: false, current };
    const user = currentUserTurn(active);
    if (user) return { reason: "current-user-turn-visible", composerCleared: composerText(findComposer()).length === 0, generating: false, current };
    if (!composerText(findComposer())) return { reason: "composer-cleared", composerCleared: true, generating: false, current };
    return null;
  }

  async function waitAfterSend(active, reasonPrefix, timeout = 6000) {
    return waitFor(() => {
      const result = classifySubmissionState(active);
      return result ? { ...result, reason: `${reasonPrefix}-${result.reason}` } : null;
    }, timeout, 100);
  }

  async function settleAfterPromptLeftComposer(active, attempts) {
    await diagnostic(active, "post-click-settling", {
      send_attempts: attempts,
      submission_retry_suppressed: true,
      composer_chars: composerText(findComposer()).length,
      generating_observed: isGenerating(),
    });
    const confirmed = await waitAfterSend(active, "late", 20000);
    if (confirmed) return confirmed;
    throw new Error("ChatGPT prompt left the composer after send, but submission could not be confirmed; duplicate send was suppressed");
  }

  async function submitAndConfirm(active) {
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
        const hasPrompt = text === active.promptNormalized || (active.promptNormalized.length > 6 && text.includes(active.promptNormalized));
        return current && hasPrompt && buttonReady(button) ? { current, button } : null;
      }, attempts === 1 ? readinessBudget : 15000, 120);
      if (!ready) throw new Error("ChatGPT send button did not become ready while this request's prompt remained in the composer");

      refreshAssistantBaseline(active);
      ready.button.scrollIntoView?.({ block: "nearest", inline: "nearest" });
      ready.button.click();
      await diagnostic(active, "clicked", {
        send_attempts: attempts,
        submit_wait_ms: Math.round((performance.now() - readyStarted) * 10) / 10,
        hydration_safe_baseline: true,
        historical_assistant_count: active.baselineCount,
      });

      let confirmed = await waitAfterSend(active, "click", 6000);
      if (!confirmed && promptStillPresent(active)) {
        dispatchEnter(findComposer());
        enterFallbackUsed = true;
        await diagnostic(active, "enter-fallback", { send_attempts: attempts, enter_fallback_used: true });
        confirmed = await waitAfterSend(active, "enter", 6000);
      } else if (!confirmed && !promptStillPresent(active)) {
        confirmed = await settleAfterPromptLeftComposer(active, attempts);
      }
      if (confirmed) {
        await diagnostic(active, "confirmed", {
          send_attempts: attempts,
          submit_total_ms: Math.round((performance.now() - totalStarted) * 10) / 10,
          submission_confirmed: true,
          submission_confirm_reason: confirmed.reason,
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
    active.lastCaptureAt = Date.now();
    active.lastCapturedText = text;
    if (text.startsWith(previous)) {
      const delta = text.slice(previous.length);
      if (delta) await emit({ type: "chat.delta", request_id: active.requestId, delta });
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
      const current = currentAssistantState(active);
      const generating = isGenerating();
      if ((current.isNew || generating) && !responseStarted) {
        responseStarted = true;
        active.responseStarted = true;
        await emit({ type: "chat.started", request_id: active.requestId, diagnostics: { response_epoch_revision: 69 } });
      }
      if (current.isNew && current.text) {
        const previous = lastText;
        lastText = await updateCapturedText(active, current.text, lastText);
        if (lastText !== previous || current.identity !== lastIdentity) {
          stableSince = Date.now();
          lastIdentity = current.identity;
          active.lastCandidateIdentity = current.identity;
          active.lastCandidateReason = current.reason;
        }
      }
      if (responseStarted && lastText && !generating && stableSince && Date.now() - stableSince >= 1500) {
        await delay(300);
        const finalState = currentAssistantState(active);
        if (!isGenerating() && finalState.isNew && finalState.latest) {
          const final = await finalNodeText(finalState.latest);
          const finalText = final.text || finalState.text || lastText;
          lastText = await updateCapturedText(active, finalText, lastText);
          await emit({
            type: "chat.completed",
            request_id: active.requestId,
            text: finalText,
            diagnostics: {
              response_epoch_revision: 69,
              response_epoch_candidate_reason: finalState.reason,
              response_format: "markdown",
              response_image_inlined_count: final.image_inlined_count || 0,
              response_image_inlined_bytes: final.image_inlined_bytes || 0,
            },
          });
          active.completed = true;
          return;
        }
      }
      await delay(120);
    }

    if (active.cancelled) await emit({ type: "chat.cancelled", request_id: active.requestId, reason: "Cancelled by API client" });
    else await emit({ type: "chat.error", request_id: active.requestId, error: "Timed out waiting for ChatGPT response", diagnostics: { response_epoch_revision: 69 } });
  }

  async function runRequest(message) {
    if (state.active) throw new Error("This ChatGPT tab is already handling another request");
    const prompt = String(message.prompt || "").trim();
    if (!prompt) throw new Error("Prompt is empty");
    const active = {
      requestId: String(message.requestId || ""),
      prompt,
      promptNormalized: normalize(prompt),
      options: message.options || {},
      cancelled: false,
      completed: false,
      responseStarted: false,
      baselineCount: 0,
      baselineIdentity: "",
      baselineIds: new Set(),
      baselineNodes: new Set(),
      baselineLatestNode: null,
      baselineLatestTurn: null,
      baselineLatestText: "",
      lastCaptureAt: 0,
      lastCapturedText: "",
    };
    state.active = active;
    if (v5) v5.active = active;

    try {
      await writePrompt(active, prompt);
      await submitAndConfirm(active);
      await monitor(active);
    } catch (error) {
      await emit({
        type: "chat.error",
        request_id: active.requestId,
        error: String(error?.message || error),
        diagnostics: { request_controller: "request-v6", response_epoch_revision: 69 },
      });
    } finally {
      if (state.active === active) state.active = null;
      if (v5?.active === active) v5.active = null;
    }
  }

  state.contract = Object.freeze({
    currentAssistantState,
    currentUserTurn,
    refreshAssistantBaseline,
    turnFollows,
    plainNodeText,
    finalNodeText,
    isGenerating,
  });

  const listener = (message, sender, sendResponse) => {
    if (message.type === "chat2api.request") {
      runRequest(message);
      sendResponse({ ok: true, controller: "request-v6", revision: 69 });
      return false;
    }
    if (message.type === "chat2api.cancel") {
      if (state.active && state.active.requestId === message.requestId) state.active.cancelled = true;
      sendResponse({ ok: true, controller: "request-v6", revision: 69 });
      return false;
    }
    if (typeof priorListener === "function") return priorListener(message, sender, sendResponse);
    return false;
  };

  state.listener = listener;
  chrome.runtime.onMessage.addListener(listener);
})();
