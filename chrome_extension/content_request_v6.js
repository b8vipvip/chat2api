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

  const COMPOSER_SELECTORS = [
    "#prompt-textarea",
    "textarea[placeholder]",
    "div[contenteditable='true'][data-lexical-editor='true']",
    "div[contenteditable='true'].ProseMirror",
    "[contenteditable='true']",
  ];

  function findComposer() {
    const root = composerRoot() || document;
    for (const selector of COMPOSER_SELECTORS) {
      const found = [...root.querySelectorAll(selector)].find(visible);
      if (found) return found;
    }
    return null;
  }

  function composerCandidates(active = null) {
    const result = [];
    const seen = new Set();
    const add = element => {
      if (!element || seen.has(element) || element.isConnected === false || !visible(element)) return;
      seen.add(element);
      result.push(element);
    };
    add(active?.promptComposer || null);
    for (const selector of COMPOSER_SELECTORS) {
      for (const element of document.querySelectorAll(selector)) add(element);
    }
    return result;
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
    const applied = text
      ? document.execCommand("insertText", false, text)
      : document.execCommand("delete", false);
    // A long prompt may look populated after a DOM-only fallback while
    // the framework still sees an empty draft. Never fake an accepted send.
    if (text.length >= 2048 && !applied) {
      throw new Error("ChatGPT native editor rejected long prompt insertion");
    }
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

  function sendButton(composer = null) {
    const root = composer?.closest?.("form[data-type='unified-composer'], form") || composerRoot() || document;
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
        for (const current of composerCandidates(active)) {
          const text = composerText(current);
          if (text === target || (target.length > 6 && text.includes(target))) return { composer: current, text };
        }
        return null;
      }, 3500, 100);
      if (retained) {
        active.promptComposer = retained.composer;
        await diagnostic(active, "prompt-ready", { prompt_write_attempts: attempt, composer_chars: retained.text.length });
        return retained;
      }
      composer = await ensureComposer(5000);
      await delay(180);
    }
    throw new Error("Prompt insertion could not be confirmed in the current ChatGPT composer");
  }

  // The ChatGPT rich editor can add layout whitespace after accepting a
  // native insertion. Preserve exact non-whitespace content, rather than
  // comparing only innerText string lengths or accepting an arbitrary edit.
  function promptMatchEvidence(text, active) {
    const target = String(active?.promptNormalized || "");
    const observed = normalize(text);
    if (!observed || !target) return "none";
    if (observed === target) return "exact";
    if (target.length > 6 && observed.includes(target)) return "embedded-exact";
    const stripLayout = value => value.replace(/\s/g, "");
    if (target.length >= 2048 && stripLayout(observed) === stripLayout(target)) {
      return "rich-editor-whitespace";
    }
    return "none";
  }

  function promptMatchesText(text, active) {
    return promptMatchEvidence(text, active) !== "none";
  }

  function promptMatchesComposer(element, active) {
    if (!element) return false;
    if (promptMatchesText(composerText(element), active)) return true;
    // innerText and textContent differ when the editor hydrates block nodes.
    if (!(element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement)) {
      return promptMatchesText(normalize(element.textContent || ""), active);
    }
    return false;
  }

  function composerHoldingPrompt(active) {
    return composerCandidates(active).find(element => promptMatchesComposer(element, active)) || null;
  }

  function promptStillPresent(active) {
    return Boolean(composerHoldingPrompt(active));
  }

  function classifySubmissionState(active) {
    if (promptStillPresent(active)) return null;
    const current = currentAssistantState(active);
    const user = currentUserTurn(active);
    if (user) return { reason: "current-user-turn-visible", composerCleared: true, generating: isGenerating(), current };
    if (current.isNew) return { reason: current.reason, composerCleared: true, generating: isGenerating(), current };
    const submitAt = Number(active.submitStartedAt || 0);
    const networkText = String(active.networkObservedText || "");
    const networkAt = Number(active.networkObservedAt || 0);
    const terminalText = String(active.networkTerminalText || "");
    const terminalAt = Number(active.networkTerminalAt || 0);
    // The first send timestamp remains the correlation epoch for the request.
    if (submitAt > 0 && networkText && networkAt >= submitAt) {
      return { reason: "network-response-evidence", composerCleared: true, generating: isGenerating(), current };
    }
    if (submitAt > 0 && terminalText && terminalAt >= submitAt) {
      return { reason: "network-terminal-evidence", composerCleared: true, generating: isGenerating(), current };
    }
    // Stop/Generating UI is not submission proof. A stale generation control from
    // another hydration epoch can coexist with this request's draft in a second
    // composer. Only the current user turn, current response, or this request's
    // network response can confirm that the prompt really left the draft state.
    return null;
  }

  function upstreamRateLimitFailure(active) {
    if (!active.rateLimitTerminalError) return null;
    const error = new Error(active.rateLimitTerminalError);
    error.diagnostics = active.rateLimitEvidence || {};
    return error;
  }

  async function waitAfterSend(active, reasonPrefix, timeout = 6000) {
    const result = await waitFor(() => {
      // A visible upstream quota rejection is decisive even when the editor
      // clears and no accepted user turn is ever created. The controller, not
      // the quota observer, owns the single chat.error terminal.
      if (active.rateLimitTerminalError) return { upstreamRateLimited: true };
      const confirmed = classifySubmissionState(active);
      return confirmed ? { ...confirmed, reason: `${reasonPrefix}-${confirmed.reason}` } : null;
    }, timeout, 100);
    if (result?.upstreamRateLimited) throw upstreamRateLimitFailure(active);
    return result;
  }

  async function settleAfterPromptLeftComposer(active, attempts) {
    await diagnostic(active, "post-click-settling", {
      send_attempts: attempts,
      submission_retry_suppressed: true,
      composer_chars: composerText(findComposer()).length,
      generating_observed: isGenerating(),
    });
    const lateBudget = active.attachmentCount > 0 ? 45000 : 20000;
    const confirmed = await waitAfterSend(active, "late", lateBudget);
    if (confirmed) return confirmed;
    throw new Error("ChatGPT cleared the composer but did not expose this request's accepted user turn or response evidence; duplicate send was suppressed");
  }

  async function submitAndConfirm(active) {
    const totalStarted = performance.now();
    const requestTimeoutMs = Math.max(15000, Number(active.options.timeout_seconds || 300) * 1000);
    const readinessBudget = Math.max(15000, Math.min(20000, requestTimeoutMs - 5000));
    let attempts = 0;
    let enterFallbackUsed = false;
    let promptRepairAttempts = 0;

    while (attempts < 3 && !active.cancelled) {
      attempts += 1;
      const readyStarted = performance.now();
      const probe = () => {
        const current = composerHoldingPrompt(active) || active.promptComposer || findComposer();
        const button = sendButton(current);
        return current && promptMatchesComposer(current, active) && buttonReady(button)
          ? { current, button } : null;
      };
      let ready = await waitFor(probe, 2400, 120);

      // writePrompt verified this *same editor* before submission. If its
      // framework silently alters a few characters while hydrating, recover
      // only that original draft, once, before any click or network evidence.
      if (!ready && attempts === 1 && !promptRepairAttempts && !isGenerating()) {
        const current = active.promptComposer;
        const observed = composerText(current);
        const intended = String(active.promptNormalized || "");
        const delta = Math.abs(observed.length - intended.length);
        const sameEditor = current?.isConnected !== false
          && composerCandidates(active).includes(current);
        if (sameEditor && observed && !promptMatchesComposer(current, active)
            && delta <= Math.max(8, Math.ceil(intended.length * 0.002))) {
          promptRepairAttempts += 1;
          await diagnostic(active, "editor-draft-reconcile", {
            prompt_repair_reason: "verified-draft-hydration-drift",
            prompt_repair_delta_chars: observed.length - intended.length,
            prompt_repair_same_editor: true,
          });
          setComposerText(current, active.prompt);
          await delay(200);
        }
      }
      if (!ready) ready = await waitFor(probe, attempts === 1 ? readinessBudget - 2400 : 12000, 120);
      if (!ready) {
        const current = composerHoldingPrompt(active) || findComposer();
        const button = sendButton(current);
        await diagnostic(active, "send-readiness-failed", {
          send_button_found: Boolean(button),
          send_button_disabled: Boolean(button?.disabled),
          send_button_aria_disabled: button?.getAttribute?.("aria-disabled") === "true",
          send_button_test_id: String(button?.dataset?.testid || "").slice(0, 64),
          send_composer_chars: composerText(current).length,
          send_expected_normalized_chars: String(active.promptNormalized || "").length,
          send_prompt_match: promptMatchEvidence(composerText(current), active),
          send_composer_original: current === active.promptComposer,
          send_button_type: String(button?.type || ""),
          send_button_aria_label: String(button?.getAttribute?.("aria-label") || "").slice(0, 72),
          send_prompt_repair_attempts: promptRepairAttempts,
          send_generating_observed: isGenerating(),
          prompt_native_editor_required: active.prompt.length >= 2048,
          send_readiness_wait_ms: Math.round((performance.now() - readyStarted) * 10) / 10,
        });
        throw new Error("ChatGPT send button did not become ready for the current editor state");
      }

      active.promptComposer = ready.current;
      refreshAssistantBaseline(active);
      active.submitStartedAt = Date.now();
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
        dispatchEnter(composerHoldingPrompt(active) || active.promptComposer || findComposer());
        enterFallbackUsed = true;
        // Do not invalidate response evidence that arrived after the first click.
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
          submission_liveness_revision: 80,
          submission_attachment_count: active.attachmentCount,
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

  function preferredResponseText(current, candidate) {
    const before = String(current || "");
    const next = String(candidate || "");
    if (!next || next === before) return before;
    if (!before || next.startsWith(before)) return next;
    if (before.startsWith(next)) return before;
    return next.length >= before.length ? next : before;
  }

  async function ensureResponseStarted(active, responseStarted, source) {
    if (responseStarted) return true;
    active.responseStarted = true;
    await emit({ type: "chat.started", request_id: active.requestId, diagnostics: { response_epoch_revision: 69, response_start_source: source } });
    return true;
  }

  async function monitor(active) {
    const timeoutMs = Math.max(5000, Number(active.options.timeout_seconds || 300) * 1000);
    const startedAt = Date.now();
    let responseStarted = false;
    let lastText = "";
    let lastIdentity = "";
    let stableSince = 0;

    while (!active.cancelled && Date.now() - startedAt < timeoutMs) {
      const upstreamLimit = upstreamRateLimitFailure(active);
      if (upstreamLimit) throw upstreamLimit;
      const error = pageError();
      if (error && responseStarted) throw new Error(`ChatGPT response UI error: ${error}`);
      const current = currentAssistantState(active);
      const generating = isGenerating();
      const networkText = String(active.networkObservedText || "");
      const networkTerminalText = String(active.networkTerminalText || "");
      const networkTerminalAt = Number(active.networkTerminalAt || 0);

      if ((current.isNew || generating || networkText) && !responseStarted) {
        responseStarted = await ensureResponseStarted(active, responseStarted, networkText ? "network-evidence-v57" : current.isNew ? "dom-assistant" : "generation-control");
      }
      if (current.isNew && current.text) {
        const preferred = preferredResponseText(lastText, current.text);
        const previous = lastText;
        lastText = await updateCapturedText(active, preferred, lastText);
        if (lastText !== previous || current.identity !== lastIdentity) {
          stableSince = Date.now();
          lastIdentity = current.identity;
          active.lastCandidateIdentity = current.identity;
          active.lastCandidateReason = current.reason;
        }
      }
      if (networkText) {
        const preferred = preferredResponseText(lastText, networkText);
        const previous = lastText;
        lastText = await updateCapturedText(active, preferred, lastText);
        if (lastText !== previous) {
          stableSince = Date.now();
          active.lastCandidateReason = "network-assistant-evidence-v57";
        }
      }

      // A correlated assistant-complete/done event is the strongest response
      // terminal evidence available on redesigned pages. The network module does
      // not complete requests itself; it writes this evidence onto the exact
      // active request and request-v6 remains the one terminal authority.
      // Allow late, authoritative snapshots from the same SSE stream to settle
      // after an early end-turn proof. A short terminal fragment is not
      // automatically a final text payload.
      const evidenceSettledAt = Math.max(networkTerminalAt, Number(active.networkObservedAt || 0));
      if (networkTerminalAt && networkTerminalText && !promptStillPresent(active)
          && Date.now() - evidenceSettledAt >= 1250) {
        responseStarted = await ensureResponseStarted(active, responseStarted, "network-terminal-v57");
        // Only this controller can commit the terminal body. Use the verified
        // SSE final for this request; DOM and intermediary observers provide
        // progress, not competing final answers.
        lastText = await updateCapturedText(active, networkTerminalText, lastText);
        await emit({
          type: "chat.completed",
          request_id: active.requestId,
          text: lastText || networkTerminalText,
          diagnostics: {
            response_epoch_revision: 69,
            response_terminal_settle_revision: 82,
            response_terminal_source: "network-sse-terminal-v57",
            response_terminal_stable_ms: Date.now() - networkTerminalAt,
            response_epoch_candidate_reason: active.lastCandidateReason || "network-assistant-evidence-v57",
            network_stream_id: String(active.networkTerminalStreamId || ""),
            network_stream_sequence: Number(active.networkTerminalSequence || 0),
            network_stream_chunks: Number(active.networkTerminalChunks || 0),
            network_stream_bytes: Number(active.networkTerminalBytes || 0),
            network_terminal_evidence_source: String(active.networkTerminalEvidenceSource || "stream-eof"),
            response_terminal_network_chars: networkTerminalText.length,
            response_terminal_final_chars: lastText.length,
            response_format: "markdown",
            response_image_inlined_count: 0,
            response_image_inlined_bytes: 0,
          },
        });
        active.completed = true;
        return;
      }

      // A DOM pause or a missing Stop button cannot override an observed SSE
      // response that has not reached a verified assistant terminal.
      if (active.networkStreamEndedWithoutTerminalAt && !networkTerminalAt
          && Date.now() - active.networkStreamEndedWithoutTerminalAt >= 3000) {
        await emit({
          type: "chat.error", request_id: active.requestId,
          error: "ChatGPT network stream ended without a verified assistant completion",
          diagnostics: { response_integrity_guard: "verified-network-terminal-v125", network_stream_unverified_end: true },
        });
        return;
      }
      if (responseStarted && lastText && !generating && !active.networkStreamSeen
          && stableSince && Date.now() - stableSince >= 1000) {
        let settleStableSince = Date.now();
        let settledText = lastText;
        let settledState = current;
        let settledFinal = { text: lastText, image_inlined_count: 0, image_inlined_bytes: 0 };
        while (!active.cancelled && Date.now() - settleStableSince < 3000) {
          if (isGenerating()) {
            settleStableSince = 0;
            break;
          }
          const candidateState = currentAssistantState(active);
          if (!candidateState.isNew || !candidateState.latest) {
            settleStableSince = 0;
            break;
          }
          const candidateFinal = await finalNodeText(candidateState.latest);
          const candidateText = candidateFinal.text || candidateState.text || settledText;
          if (candidateText && (candidateText !== settledText || candidateState.identity !== settledState.identity)) {
            settledText = candidateText;
            settledState = candidateState;
            settledFinal = candidateFinal;
            lastText = await updateCapturedText(active, candidateText, lastText);
            lastIdentity = candidateState.identity || lastIdentity;
            settleStableSince = Date.now();
          } else {
            settledState = candidateState;
            settledFinal = candidateFinal;
          }
          await delay(180);
        }
        if (settleStableSince && !isGenerating() && Date.now() - settleStableSince >= 3000) {
          const finalText = settledText || lastText;
          await emit({
            type: "chat.completed",
            request_id: active.requestId,
            text: finalText,
            diagnostics: {
              response_epoch_revision: 69,
              response_terminal_settle_revision: 82,
              response_terminal_source: "dom-stable-v6",
              response_terminal_stable_ms: Date.now() - settleStableSince,
              response_epoch_candidate_reason: settledState.reason,
              response_format: "markdown",
              response_image_inlined_count: settledFinal.image_inlined_count || 0,
              response_image_inlined_bytes: settledFinal.image_inlined_bytes || 0,
            },
          });
          active.completed = true;
          return;
        }
        stableSince = Date.now();
      }
      await delay(120);
    }

    if (active.cancelled) await emit({ type: "chat.cancelled", request_id: active.requestId, reason: "Cancelled by API client" });
    else await emit({ type: "chat.error", request_id: active.requestId, error: "Timed out waiting for ChatGPT response", diagnostics: { response_epoch_revision: 69 } });
  }

  async function runRequest(message) {
    // v6 is the only authority for request admission and request terminal
    // delivery in this tab. No outer listener may emit a competing chat.error.
    if (state.active) {
      await emit({type:"chat.error",request_id:String(message.requestId||""),
        error:"This ChatGPT tab is already handling another request",
        diagnostics:{request_controller:"request-v6",reason:"busy"}});
      return;
    }
    const prompt = String(message.prompt || "").trim();
    if (!prompt) {
      await emit({type:"chat.error",request_id:String(message.requestId||""),
        error:"Prompt is empty",diagnostics:{request_controller:"request-v6",reason:"empty-prompt"}});
      return;
    }
    const active = {
      requestId: String(message.requestId || ""),
      prompt,
      promptNormalized: normalize(prompt),
      promptComposer: null,
      submitStartedAt: 0,
      options: message.options || {},
      attachmentCount: Array.isArray(message.attachments) ? message.attachments.length : 0,
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
      networkObservedText: "",
      networkObservedAt: 0,
      networkTerminalText: "",
      networkTerminalAt: 0,
      networkTerminalStreamId: "",
      networkTerminalSequence: 0,
      networkTerminalChunks: 0,
      networkTerminalBytes: 0,
      networkTerminalEvidenceSource: "",
      networkStreamSeen: false,
      networkStreamEndedWithoutTerminalAt: 0,
    };
    state.active = active;
    if (v5) v5.active = active;

    try {
      // UI interruption resolution is readiness work owned by the request
      // controller, not a separate message listener with its own ACK/terminal.
      const interruption = globalThis.__CHAT2API_INTERRUPTION_GUARD_V72__;
      if (typeof interruption?.resolveBlockingInterruption === "function") {
        await interruption.resolveBlockingInterruption({force:true,phase:"before-request"});
      }
      await writePrompt(active, prompt);
      await submitAndConfirm(active);
      await monitor(active);
    } catch (error) {
      await emit({
        type: "chat.error",
        request_id: active.requestId,
        error: String(error?.message || error),
        diagnostics: { request_controller: "request-v6", response_epoch_revision: 69, ...(error?.diagnostics || {}) },
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
    promptMatchesText,
    promptMatchEvidence,
    turnFollows,
    plainNodeText,
    finalNodeText,
    isGenerating,
  });

  const listener = (message, sender, sendResponse) => {
    if (message.type === "chat2api.request") {
      const quota = globalThis.__CHAT2API_CONVERSATION_QUOTA_FAILOVER_V95__;
      const blocked = quota?.preDispatchBlockText?.() || "";
      if (blocked) {
        quota.requestFailover(String(message.requestId || ""), blocked,
          "pre-dispatch-disabled-conversation").catch(() => {});
        sendResponse({ ok: true, controller: "request-v6", revision: 69,
          reroute_pending: true, request_id: String(message.requestId || "") });
        return false;
      }
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
  // Historical lifecycle/hygiene overlays still discover the request owner
  // through the v5 compatibility surface. Point that surface at v6 before those
  // overlays load so they wrap this listener rather than accidentally restoring
  // the stale count-based v5 response classifier.
  if (v5) v5.listener = listener;
  chrome.runtime.onMessage.addListener(listener);
})();