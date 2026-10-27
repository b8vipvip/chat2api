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
    .trim()
    .normalize("NFC");

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

  // A redesigned ChatGPT page can continue its reasoning after removing the
  // Stop button. Re-read the live status immediately before DOM-only terminal
  // recovery; old heartbeat metadata is evidence, never current UI truth.
  function upstreamStillWorking() {
    if (isGenerating()) return true;
    try {
      return globalThis.__CHAT2API_GENERATION_LIVENESS_V49__?.nativeStatus?.()?.kind === "working";
    } catch (_) { return false; }
  }


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

  // The redesigned ChatGPT reply boundary can contain the current USER turn
  // followed by the assistant turn. Verify the echoed input against the exact
  // submitted prompt before removing it; never strip a user-authored transcript.
  // Incomplete matching prefixes are quarantined until the reply boundary arrives.
  function assistantOnlyReplyText(active, value) {
    const raw = String(value || "");
    const heading = /^\s*(?:#{1,6}\s*)?You said\s*:\s*/i.exec(raw);
    if (!heading || !active?.promptNormalized)
      return { text: raw, stripped: false, pending: false };
    const remainder = raw.slice(heading[0].length);
    const boundary = /(?:^|\n)\s*(?:#{1,6}\s*)?ChatGPT said\s*:\s*/gi;
    let found;
    while ((found = boundary.exec(remainder))) {
      if (normalize(remainder.slice(0, found.index)) !== active.promptNormalized) continue;
      const body = remainder.slice(found.index + found[0].length).trimStart();
      return { text: body, stripped: true, pending: !body.trim() };
    }
    // Multi-turn OpenAI clients (including mv3c) send the previous assistant
    // response back inside the next prompt. The roleless ChatGPT DOM may echo
    // that previous exchange and then append this turn's new assistant body.
    // The v212 equality check cannot pass because rendered Markdown differs
    // from the submitted prompt (links are expanded, icons become IMG nodes).
    // Correlate multiple *plain sentences* from the actual previous assistant
    // text before trusting the LAST role boundary as the current answer.
    const matches = [...remainder.matchAll(/(?:^|\n)\s*(?:#{1,6}\s*)?ChatGPT said\s*:\s*/gi)];
    if (matches.length >= 2 && /\[Assistant\]/.test(active.prompt)
        && /\[User\]/.test(active.prompt)) {
      // The current prompt contains a previously generated assistant answer
      // and the LAST [User] is the current question. Prior ChatGPT said:
      // markers inside the echo are not current-request answers.
      const promptHistory = String(active.prompt);
      const lastUserPosition = promptHistory.lastIndexOf("\n[User]");
      const historicalContext = lastUserPosition >= 0
        ? promptHistory.slice(0, lastUserPosition) : "";
      const previousRole = historicalContext.lastIndexOf("ChatGPT said:");
      const fallbackRole = historicalContext.lastIndexOf("[Assistant]");
      const previousStart = previousRole >= 0
        ? previousRole + "ChatGPT said:".length
        : fallbackRole >= 0 ? fallbackRole + "[Assistant]".length : -1;
      const previous = previousStart >= 0
        ? historicalContext.slice(previousStart) : "";
      const plainSentences = previous.split(/\n+/).map(line => normalize(line))
        .filter(line => line.length >= 42 && line.length <= 400
          && !/https?:\/\/|!\[|\]\(/.test(line))
        .slice(0, 40);
      const beforeLast = normalize(remainder.slice(0, matches[matches.length - 1].index));
      const corroborated = plainSentences.filter(line =>
        beforeLast.includes(line.slice(0, Math.min(42, line.length)))).length;
      const latestUser = lastUserPosition >= 0
        ? promptHistory.slice(lastUserPosition + "\n[User]".length).trim() : "";
      const latestUserLead = normalize(latestUser).slice(0, 48);
      const currentUserVerified = latestUserLead.length >= 12
        && beforeLast.includes(latestUserLead);
      if (corroborated >= 2 && currentUserVerified) {
        const last = matches[matches.length - 1];
        const body = remainder.slice(last.index + last[0].length).trimStart();
        return { text: body, stripped: true, pending: !body.trim(),
          multiTurn: true, corroborated };
      }
    }
    // A partly rendered transcript must never leak earlier turns as an API
    // delta: wait for positive evidence of the current assistant boundary.
    if (/\[Assistant\]/.test(active.prompt) && /\[User\]/.test(active.prompt)
        && matches.length && normalize(remainder).length > 160)
      return { text: "", stripped: false, pending: true };
    const seen = normalize(remainder);
    const expected = active.promptNormalized;
    const echoLead = expected.slice(0, Math.min(80, expected.length));
    if (!seen || expected.startsWith(seen) || seen.startsWith(expected)
        || (echoLead.length >= 12 && seen.includes(echoLead))) {
      return { text: "", stripped: false, pending: true };
    }
    return { text: raw, stripped: false, pending: false };
  }

  async function emit(event) {
    // Sanitize the request-correlated user-turn echo at the single Worker
    // egress, before either SSE delivery or terminal evidence capture.
    const active = state.active?.requestId === event?.request_id ? state.active : null;
    if (active && ["chat.completed", "chat.snapshot"].includes(event?.type)) {
      const result = assistantOnlyReplyText(active, event.text);
      if (result.pending && event.type === "chat.snapshot") return;
      if (result.pending && event.type === "chat.completed") {
        event = {
          type: "chat.error", request_id: active.requestId,
          error: "ChatGPT reply boundary was not verified; refusing to return a user-prompt transcript",
          diagnostics: { response_boundary_guard: "request-correlated-prompt-echo-v212",
            response_boundary_echo_unverified: true },
        };
      } else if (result.stripped) {
        event = { ...event, text: result.text,
          diagnostics: { ...(event.diagnostics || {}),
            response_boundary_guard: "request-correlated-prompt-echo-v212",
            response_boundary_prompt_echo_removed: true,
            response_boundary_multiturn_verified: result.multiTurn === true,
            response_boundary_previous_text_matches: Number(result.corroborated || 0),
            response_boundary_current_user_verified: result.multiTurn === true,
            response_boundary_removed_chars: String(event.text || "").length - result.text.length,
          } };
      }
    }
    // Flush read-only evidence before a real request terminal event is sent.
    // This never changes the sole terminal decision of request-v6.
    if (["chat.completed", "chat.error", "chat.cancelled"].includes(event?.type)
        && state.active?.requestId === event.request_id) {
      try {
        await globalThis.__CHAT2API_PROBE_SUPERVISOR_V1__?.capture?.(
          state.active, "before_terminal", {
            completed: event.type === "chat.completed",
            deliveredText: event.type === "chat.completed" ? String(event.text || "") : "",
            captured: Number(state.active.mediaCaptureLastInlined || 0)
              + Number(state.active.mediaCaptureLastEmbedded || 0),
          });
      } catch (_) {}
    }
    // A before_terminal probe proves intent, not successful socket delivery.
    // Do not ignore background ok:false: retry only the identical terminal
    // from the one request-v6 owner, without giving probes any terminal power.
    const terminal = ["chat.completed","chat.error","chat.cancelled",
      "image.completed","image.error","image.cancelled"].includes(event?.type);
    const tries = terminal ? 3 : 1;
    for(let attempt=1;attempt<=tries;attempt++) {
      try {
        const outgoing = terminal
          ? {...event,diagnostics:{...(event.diagnostics||{}),
              terminal_worker_send_attempt:attempt,
              terminal_worker_ack_type:"background-websocket-enqueue-v215"}}
          : event;
        const result = await chrome.runtime.sendMessage({type:"chat2api.event",event:outgoing});
        if(result?.ok === true) return;
        if(!terminal) return;
      } catch (_) {
        if(!terminal) return;
      }
      if(attempt<tries) await new Promise(resolve=>setTimeout(resolve,450*attempt));
    }
    if(terminal) throw new Error("Worker terminal event was not queued by background WebSocket");
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
    // Only a user turn created after this request's pre-submit baseline can
    // anchor image-only responses when ChatGPT collapses long user prompts.
    const afterBaseline = users.filter(turn => !active.baselineUserTurns?.has(turn));
    return afterBaseline[afterBaseline.length - 1] || null;
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
    // The visible user prompt may be collapsed behind a "Show more" control,
    // so its text cannot always match the full prompt sent to the composer.
    active.baselineUserTurns = new Set(conversationTurns().filter(userTurn));
    active.baselinePageMedia = rich?.snapshotPageMedia?.() || null;
    captureRenderedBaseline(active);
    active.baselineCapturedAt = Date.now();
  }

  const RENDERED_ASSISTANT_SELECTORS = [
    "main [data-message-content]", "main .markdown", "main [class*='markdown']",
    "main [class*='prose']", "main [data-testid*='assistant']",
    "main article", "main section", "main div",
  ].join(",");

  const COMPLETED_RESPONSE_ACTIONS = [
    "main button[data-testid*='copy']",
    "main button[data-testid*='thumb']",
    "main button[aria-label*='Copy']",
    "main button[aria-label*='复制']",
    "main button[aria-label*='Good response']",
    "main button[aria-label*='Bad response']",
    "main button[aria-label*='Regenerate']",
    "main button[aria-label*='重新生成']",
    "main button[aria-label*='Try again']",
  ].join(",");

  function responseFinishActions() {
    return [...document.querySelectorAll(COMPLETED_RESPONSE_ACTIONS)].filter(node =>
      visible(node) && !node.closest?.("nav,aside,header,footer,form,[role='dialog']"));
  }

  function captureRenderedBaseline(active) {
    const candidates = [...document.querySelectorAll(RENDERED_ASSISTANT_SELECTORS)].slice(-220);
    active.baselineRenderedText = new Map(candidates
      .filter(visible)
      .map(node => [node, normalize(node.innerText || node.textContent || "").slice(-5000)]));
    active.baselineFinishActions = new Set(responseFinishActions());
  }

  // Bind each completion action to the smallest current reply container.
  // A page-wide main scan includes unrelated after-toolbar widgets; conversely
  // a code-cell Copy button cannot prove an entire answer completed.
  function replyScopeForFinishAction(candidate, button) {
    if (!candidate || !button || !turnFollows(candidate, button)) return null;
    const label = String(button.getAttribute?.("aria-label") || "") + " " +
      String(button.getAttribute?.("data-testid") || "");
    if (/copy[\s_-]*code|复制代码/i.test(label) || button.closest?.(
      "pre,code,[data-testid*='code-block'],[class*='code-block'],[class*='codeBlock']")) return null;
    let common = candidate;
    // At most a bounded climb, and never promote a per-answer action to the
    // whole chat main or conversation list. The button and answer must truly
    // share one non-page ancestor, without an identified user message.
    for (let depth = 0; common && depth < 24; depth++, common = common.parentElement) {
      if (!common.contains?.(button)) continue;
      if (common.matches?.("main,[role='main'],#thread,[data-testid='conversation-turns']")
          || common.querySelector?.("[data-message-author-role='user'],[data-testid='user-message']"))
        return null;
      return common;
    }
    return null;
  }

  // A redesigned reply can render one Markdown/code/table component per
  // sibling. A stable code cell is not a stable *answer*. Audit all independently
  // changed structural content before accepting any partial DOM selection.
  function renderedResponseBoundaryEvidence(active, candidate, replyScope = null) {
    const scope = replyScope || candidate?.closest?.("main,[role='main'],#thread");
    const baseline = active?.baselineRenderedText;
    if (!scope || !baseline || !candidate) return {
      independent_count: 0, independent_chars: 0, max_independent_chars: 0,
      candidate_chars: 0, complete_scope: false,
    };
    const promptStart = String(active.promptNormalized || "").slice(0, 100);
    const promptEnd = String(active.promptNormalized || "").slice(-100);
    const selectors = [
      "[data-message-content]", ".markdown", "[class*='markdown']",
      "[class*='prose']", "pre", "table", "article", "section",
      "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "math", ".katex-display",
    ].join(",");
    // Never silently take only the tail of a long response. The previous
    // slice(-420) discarded opening sections of lengthy rich-text answers.
    const structuralNodes = [...scope.querySelectorAll(selectors)];
    const scanOverflow = structuralNodes.length > 1600;
    const changed = (scanOverflow ? [] : structuralNodes)
      .filter(node => {
        if (!visible(node) || candidate.contains(node) || node.contains(candidate)
            || node.closest?.("nav,aside,header,footer,form,[role='dialog'],[contenteditable='true']"))
          return false;
        const text = normalize(node.innerText || node.textContent || "");
        const tag = String(node.tagName || "").toLowerCase();
        // Short headings, list items, paragraphs and rendered formulas are
        // complete semantic blocks, not incidental fragments. They were
        // previously discarded by the universal 120-character threshold.
        const semanticShort = /^(?:p|h[1-6]|li|math|blockquote)$/.test(tag)
          || Boolean(node.classList?.contains("katex-display"));
        const minimum = semanticShort ? 2 : 120;
        if (text.length < minimum || text.length > 85000 || transientText(text)
            || (promptStart && text.includes(promptStart))
            || (promptEnd && text.includes(promptEnd))
            || node.closest?.("[data-message-author-role='user'],[data-testid='user-message']"))
          return false;
        const old = baseline.get(node);
        return old === undefined || old !== text.slice(-5000);
      });
    // Do not count ancestor and descendant wrappers twice.
    const independent = changed.filter(node => !changed.some(other => other !== node && other.contains(node)));
    const sizes = independent.map(node => normalize(node.innerText || node.textContent || "").length);
    const candidateChars = normalize(candidate.innerText || candidate.textContent || "").length;
    const all = [candidate, ...independent].filter(node => ![candidate,...independent]
      .some(other => other !== node && other.contains(node)));
    all.sort((left,right) => left === right ? 0
      : left.compareDocumentPosition(right) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);
    return {
      nodes: scanOverflow ? [] : all,
      scan_overflow: scanOverflow,
      scan_structural_node_count: structuralNodes.length,
      independent_count: independent.length,
      independent_chars: sizes.reduce((sum, n) => sum + n, 0),
      max_independent_chars: Math.max(0, ...sizes),
      candidate_chars: candidateChars,
      complete_scope: independent.length === 0,
    };
  }

  // Only request-v6 consumes the evidence below. The SSE observer and DOM
  // selector never emit terminal events or decide status. A fresh answer toolbar
  // in the original routed tab is positive UI completion proof, unlike simply
  // losing a Stop button or pausing between paragraphs.
  function verifiedRenderedAbortCandidate(active, current) {
    if (!active.networkStreamSeen || !active.submitStartedAt
        || !active.networkObservedAt || active.networkObservedAt < active.submitStartedAt
        || !Number(active.networkTerminalTransportErrorAt || 0)
        || !Number(active.networkStreamEndedWithoutTerminalAt || 0)
        || !active.baselineRenderedText || !active.baselineFinishActions
        || !current?.isNew || !current.latest
        || !["verified-rendered-network-overlap-v189",
              "verified-rendered-dom-baseline-v191",
              "after-current-user-turn"].includes(current.reason)
        || String(current.text || "").length < 240
        || internalToolPayload(current.text) || promptStillPresent(active)) return null;
    const fresh = responseFinishActions().filter(button =>
      !active.baselineFinishActions.has(button) && turnFollows(current.latest, button));
    if (!fresh.length) return null;
    const text = String(current.text || "");
    const start = String(active.promptNormalized || "").slice(0, 110);
    const end = String(active.promptNormalized || "").slice(-120);
    if ((start && text.includes(start)) || (end && text.includes(end))) return null;
    let best = null;
    // A current reply may have headings, tables and math before its toolbar,
    // while another assistant card or page widget follows it in main. Audit
    // the exact shared reply ancestor instead of assigning all of main to it.
    for (const button of fresh) {
      const scope = replyScopeForFinishAction(current.latest, button);
      if (!scope) continue;
      const coverage = renderedResponseBoundaryEvidence(active, current.latest, scope);
      if (coverage.scan_overflow || !coverage.nodes?.length
          || !coverage.nodes.every(node => turnFollows(node, button))) continue;
      const chars = coverage.nodes.reduce((total, node) =>
        total + normalize(node.innerText || node.textContent || "").length, 0);
      if (!best || chars > best.chars) best = {button, scope, coverage, chars};
    }
    if (!best) return null;
    return {node: current.latest, text, reason: current.reason,
      finishActionCount: fresh.length, finishAction: best.button,
      replyScope: best.scope, coverage: best.coverage};
  }

  // A short real answer (<240 chars) on redesigned pages has no role-labelled
  // assistant node. The long-form DOM recovery cannot detect it. Correlate a
  // fresh, exact reply text fragment from THIS request's SSE with a fresh
  // same-answer copy toolbar; never promote a mere pause or snapshot to final.
  function verifiedShortAbortCandidate(active) {
    const source = normalize(String(active?.networkObservedText || ""));
    if (!active?.networkStreamSeen || !active?.networkTerminalTransportErrorAt
        || !active?.networkStreamEndedWithoutTerminalAt
        || source.length < 8 || source.length > 180
        || !active?.baselineRenderedText || !active?.baselineFinishActions
        || promptStillPresent(active) || upstreamStillWorking()
        || internalToolPayload(source)) return null;
    const freshActions = responseFinishActions()
      .filter(button => !active.baselineFinishActions.has(button));
    if (!freshActions.length) return null;
    const candidates = [...document.querySelectorAll(
      "main [data-message-content],main .markdown,main [class*='markdown'],main p,main [class*='prose']"
    )].filter(node => visible(node)
      && !node.closest?.("form,[contenteditable='true'],nav,aside,header,footer,[role='dialog']"));
    for (const node of candidates.reverse()) {
      const raw = normalize(node.innerText || node.textContent || "");
      const baseline = active.baselineRenderedText.get(node);
      const text = plainNodeText(node);
      if (raw.length < source.length || raw.length > 220
          || !raw.includes(source.slice(0, Math.min(12, source.length)))
          || normalize(text).length < 8
          || (baseline !== undefined && baseline === raw.slice(-5000))
          || (active.promptNormalized && raw.includes(active.promptNormalized.slice(0, 120)))
          || transientText(raw)) continue;
      for (const button of freshActions) {
        if (!turnFollows(node, button)) continue;
        const scope = replyScopeForFinishAction(node, button);
        if (!scope) continue;
        const evidence = renderedResponseBoundaryEvidence(active, node, scope);
        if (evidence.scan_overflow || evidence.independent_count !== 0) continue;
        return {
          node, text, latest: node, reason: "verified-short-sse-rendered-answer-v207",
          shortTextVerified: true, finishAction: button, finishActionCount: freshActions.length,
          replyScope: scope, coverage: {nodes: [node], candidate_chars: raw.length,
            independent_count: 0, scan_structural_node_count: evidence.scan_structural_node_count},
        };
      }
    }
    return null;
  }

  function renderedNetworkCorrelatedTurn(active) {
    // A redesigned ChatGPT page may no longer expose the role/testid attributes.
    // Never guess from raw page position alone: bind a *changed* rendered node
    // to a substantial fragment from this exact request's SSE observation.
    const source = String(active?.networkObservedText || "");
    if (source.length < 120 || internalToolPayload(source) || !active?.networkStreamSeen)
      return null;
    const normalizedSource = normalize(source);
    const matches = [...document.querySelectorAll(RENDERED_ASSISTANT_SELECTORS)].slice(-220);
    const baseline = active.baselineRenderedText || new Map();
    for (let index = matches.length - 1; index >= 0; index -= 1) {
      const node = matches[index];
      if (!visible(node) || node.closest?.("form,[contenteditable='true'],nav,aside,footer"))
        continue;
      const text = normalize(node.innerText || node.textContent || "");
      if (text.length < 100 || text.length > 40000 || baseline.get(node) === text.slice(-5000))
        continue;
      // A source fragment must be present, not merely a generic heading.
      const fragments = source.split(/\r?\n/)
        .map(line => normalize(line.replace(/^#{1,6}\s*/, "")
          .replace(/[*_`|]/g, "").replace(/\[(.*?)\]\([^)]+\)/g, "$1")))
        .filter(line => line.length >= 65);
      if (!fragments.some(line => text.includes(line.slice(0, 65)))) continue;
      if (active.promptNormalized && text.includes(active.promptNormalized.slice(0, 120)))
        continue;
      return {
        nodes: [node], latest: node, turn: node,
        identity: nodeIdentity(node) || "rendered-network-correlated",
        text: plainNodeText(node), isNew: true,
        reason: "verified-rendered-network-overlap-v189",
      };
    }
    return null;
  }

  function renderedCurrentRequestCandidate(active) {
    // The current ChatGPT canvas/document renderer exposes neither assistant
    // roles nor a long assistant SSE fragment. Anchor instead to the *exact
    // pre-submit node snapshot* of this routed tab and the subsequent network
    // response. This is candidate evidence only: request-v6 exclusively owns
    // the stable final decision.
    if (!active?.networkStreamSeen || !active?.submitStartedAt
        || !active?.networkObservedAt || active.networkObservedAt < active.submitStartedAt
        || !active.baselineRenderedText) return null;
    const nodes = [...document.querySelectorAll(RENDERED_ASSISTANT_SELECTORS)].slice(-220);
    const baseline = active.baselineRenderedText;
    const promptStart = active.promptNormalized?.slice(0, 110) || "";
    const promptEnd = active.promptNormalized?.slice(-120) || "";
    const candidates = [];
    for (const node of nodes) {
      if (!visible(node) || node.closest?.("form,[contenteditable='true'],aside,nav,footer,header,[role='dialog']"))
        continue;
      const text = normalize(node.innerText || node.textContent || "");
      // No historical node, prompt echo, transient status, or page wrapper.
      if (text.length < 240 || text.length > 70000
          || baseline.get(node) === text.slice(-5000)
          || (promptStart && text.includes(promptStart))
          || (promptEnd && text.includes(promptEnd))) continue;
      // Content must exist inside the live chat main surface, not an overlay.
      if (!node.closest?.("main,[role='main'],#thread")) continue;
      candidates.push({node, text});
    }
    if (!candidates.length) return null;
    // Prefer the complete response document (tables and paragraphs together).
    // A page-wide parent containing the user's prompt is already excluded.
    candidates.sort((a,b) => b.text.length - a.text.length);
    const chosen = candidates[0];
    return {
      nodes:[chosen.node],latest:chosen.node,turn:chosen.node,
      identity:nodeIdentity(chosen.node) || "changed-content-baseline",
      text:plainNodeText(chosen.node),isNew:true,
      reason:"verified-rendered-dom-baseline-v191",
    };
  }

  function currentAssistantState(active) {
    const nodes = assistantNodes();
    if (!nodes.length) return renderedNetworkCorrelatedTurn(active)
      || renderedCurrentRequestCandidate(active)
      || { nodes, latest: null, turn: null, identity: "", text: "", isNew: false, reason: "none" };

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

  // The ChatGPT rich editor can add a layout separator while hydrating a
  // multiline draft. This is not an arbitrary fuzzy match: every non-space
  // codepoint must remain in precisely the same order, and only 1-2 newly
  // inserted spaces are accepted in a sufficiently long multiline draft.
  // Protect identifiers, links, numbers and ASCII words against changes.
  function editorLayoutWhitespaceEvidence(observed, target, source) {
    if (!/[\r\n]/.test(String(source || "")) || target.length < 100) return null;
    const extra = observed.length - target.length;
    if (extra < 1 || extra > 2) return null;
    if (observed.replace(/\s/g, "") !== target.replace(/\s/g, "")) return null;
    let expected = 0;
    let cursor = 0;
    const locations = [];
    while (cursor < observed.length) {
      if (expected < target.length && observed[cursor] === target[expected]) {
        cursor += 1;
        expected += 1;
        continue;
      }
      if (observed[cursor] !== " ") return null;
      const before = target[expected - 1] || "";
      const after = target[expected] || "";
      // A layout separator may not split a URL, number, English word,
      // account identifier or literal control sequence.
      const identifier = /[A-Za-z0-9_/:.?&=%#@-]/;
      if (identifier.test(before) && identifier.test(after)) return null;
      locations.push(expected);
      cursor += 1;
    }
    return expected === target.length && locations.length === extra ? locations : null;
  }

  // Non-content diagnostics only: never serialize the prompt, nearby text,
  // secret token or any matching substring into runtime logs.
  function promptDriftSummary(text, active) {
    const observed = normalize(text);
    const target = String(active?.promptNormalized || "");
    let offset = 0;
    while (offset < observed.length && offset < target.length && observed[offset] === target[offset]) offset += 1;
    let kind = "content_changed";
    if (observed === target) kind = "exact";
    else if (observed.replace(/\s/g, "") === target.replace(/\s/g, "")) {
      kind = observed.length > target.length ? "whitespace_inserted"
        : observed.length < target.length ? "whitespace_removed" : "whitespace_relocated";
    }
    return {
      send_prompt_drift_kind: kind,
      send_prompt_drift_delta: observed.length - target.length,
      send_prompt_drift_first_offset: offset,
      send_prompt_nonwhitespace_equal: observed.replace(/\s/g, "") === target.replace(/\s/g, ""),
      send_prompt_layout_allowed: Boolean(editorLayoutWhitespaceEvidence(observed, target, active?.prompt)),
    };
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
    // The historical 2048-character threshold excluded ordinary (~800-char)
    // multiline API prompts, causing false send-readiness failures after
    // Lexical inserted a single layout separator. Use explicit bounded proof.
    if (editorLayoutWhitespaceEvidence(observed, target, active?.prompt)) {
      return "rich-editor-layout-whitespace";
    }
    return "none";
  }

  // A rich editor can normalize combining Unicode characters during
  // hydration. NFC is semantically identical and safe to compare; an actual
  // changed/missing character is never silently accepted as a valid draft.
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

  async function rewriteDriftedDraft(active, editor) {
    if (!editor || editor.isConnected === false || !composerCandidates(active).includes(editor)) return false;
    // Inserting over a Lexical selection again can reproduce the same stale
    // native-editor state. Clear through the editor input path first, then type
    // exactly once into the original editor and require fresh DOM verification.
    setComposerText(editor, "");
    const cleared = await waitFor(() => composerText(editor) === "" && editor.isConnected !== false, 1200, 100);
    if (!cleared) return false;
    setComposerText(editor, active.prompt);
    return Boolean(await waitFor(() => promptMatchesComposer(editor, active), 2400, 100));
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
        if (!current || !promptMatchesComposer(current, active) || !buttonReady(button)) return null;
        const renderedMatch = promptMatchEvidence(composerText(current), active);
        // Lexical may expose correct draft text only through textContent.
        // Preserve the exact pre-click verification source for diagnostics;
        // click() may synchronously clear the editor.
        const contentMatch = renderedMatch !== "none" ? "none"
          : promptMatchEvidence(normalize(current.textContent || ""), active);
        return {
          current, button,
          promptMatch: renderedMatch !== "none" ? renderedMatch : contentMatch,
          promptMatchSource: renderedMatch !== "none" ? "rendered-composer" : "editor-text-content",
        };
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
          const verified = await rewriteDriftedDraft(active, current);
          await diagnostic(active, "editor-native-rewrite", {
            native_rewrite_verified: verified,
            prompt_repair_same_editor: true,
          });
          if (verified) ready = probe();
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
          ...promptDriftSummary(composerText(current), active),
          send_composer_unicode_nfc_matches: normalize(composerText(current)) === active.promptNormalized,
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
        send_prompt_match: ready.promptMatch,
        send_prompt_match_source: ready.promptMatchSource,
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
    // Quarantine a partial ChatGPT "You said" surface rather than streaming
    // the prompt echo before the real assistant heading has arrived.
    const bounded = assistantOnlyReplyText(active, text);
    if (bounded.pending) return previous;
    text = bounded.text;
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

  function completeResponsesBridgeDocument(text) {
    const raw = String(text || "").trim();
    if (!raw.startsWith("{") || !raw.endsWith("}")) return false;
    try {
      const value = JSON.parse(raw);
      if (!value || Array.isArray(value) || typeof value !== "object"
          || value.bridge !== "chat2api-responses-tool-v159") return false;
      const keys = Object.keys(value).sort().join(",");
      if (value.kind === "final") {
        return keys === "bridge,kind,text" && typeof value.text === "string";
      }
      if (value.kind === "tool_calls") {
        return keys === "bridge,calls,kind" && Array.isArray(value.calls)
          && value.calls.length > 0 && value.calls.every(call =>
            call && typeof call === "object" && !Array.isArray(call));
      }
    } catch (_) {}
    return false;
  }

  function isInternalMediaHandoff(value) {
    // ChatGPT sometimes concatenates two identical handoff objects in a
    // streaming snapshot. They are control markers, not user-visible text.
    const raw = String(value || "").trim();
    return raw.length > 0
      && /^(?:\{\s*"skipped_mainline"\s*:\s*true\s*\}\s*)+$/.test(raw);
  }

  // Raw image-tool invocation parameters are never a user-facing assistant reply.
  // Keep request-v6 as the sole terminal owner while waiting for verified media.
  function isImageToolArguments(value) {
    const raw = String(value || "").trim();
    if (!raw.startsWith("{") || raw.length > 2000) return false;
    try {
      const args = JSON.parse(raw);
      if (!args || Array.isArray(args) || typeof args !== "object") return false;
      const keys = Object.keys(args);
      if (!keys.includes("size") || !keys.includes("n")) return false;
      if (!keys.every(key => ["size", "n", "transparent_background", "quality",
          "background", "output_format", "style"].includes(key))) return false;
      return /^\d{2,5}x\d{2,5}$/.test(String(args.size || ""))
        && Number.isInteger(args.n) && args.n >= 1 && args.n <= 20
        && (args.transparent_background === undefined
          || typeof args.transparent_background === "boolean");
    } catch (_) { return false; }
  }


  function hasTransferableFile(value) {
    return /!\[📎 [^\]\n]{1,180}\]\(data:[a-z0-9.+-]+\/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]+\)/i
      .test(String(value || ""));
  }

  function requestedUserText(prompt) {
    const raw = String(prompt || "");
    return (raw.includes("[User]") ? raw.slice(raw.lastIndexOf("[User]") + 6) : raw).slice(0, 4000);
  }

  function explicitCreationIntent(prompt, subjects, stopAtClauseBoundary = false) {
    const action = /(?:生成|创建|导出|制作|交付|绘制|画出|输出|提供下载|create|export|generate|deliver|make|draw|design)/i;
    const negation = /(?:不需要|不用|不要求|不应该|无需|不必|不要|不得|禁止|避免|不再|别|无需重新|仅回顾|只回顾|don't|do not|without|no need)/i;
    for (const raw of requestedUserText(prompt).split(/\r?\n/)) {
      const line = raw.replace(/^\s*(?:[-*]|\d+[.、])\s*/, "").trim();
      if (!line || /^(?:如何|怎么|解释|分析|什么是|请说明如何|how to|explain|why)/i.test(line)) continue;
      const match = action.exec(line);
      if (!match) continue;
      // Negation within the 18 characters immediately before the verb
      // overrides incidental creation words in prohibition/recap prompts.
      const before = line.slice(Math.max(0, match.index - 18), match.index);
      if (negation.test(before) || /(?:解释|如何|怎么|分析|说明做法|explain|how to)/i.test(before)) continue;
      const rest = line.slice(match.index + match[0].length, match.index + match[0].length + 95);
      // File output is a direct object of the creation verb, not a detail
      // mentioned in the next clause (e.g. "生成交接上下文，包含关键文件").
      // This boundary is purpose-specific: image requests retain their existing
      // looser detection, and only the v6 request owner decides captureMedia.
      const subjectScope = stopAtClauseBoundary
        ? rest.split(/(?:包含|包括|涵盖|涉及|列出|列明|重点|such as|including|covering)|[，,。；;]/i, 1)[0]
        : rest;
      if (subjects.test(subjectScope)) return true;
    }
    return false;
  }

  function isFileCreationRequest(prompt) {
    // An explicit output-channel instruction overrides incidental "export a
    // CSV file" verbs inside runnable code examples. A chat-only document is
    // not a request for a separately transferable attachment.
    const userText = requestedUserText(prompt);
    const inlineOnly = /(?:所有|全部)[^\r\n。]{0,140}(?:直接在|直接于)[^\r\n。]{0,40}(?:聊天|对话|本次回复)[^\r\n。]{0,30}(?:呈现|展示|输出)/i.test(userText);
    const noDownloads = /(?:不需要|无需|不要|不用|不必|禁止|请勿)[^\r\n。]{0,80}(?:下载附件|下载文件|可下载附件|可下载文件)/i.test(userText);
    if (inlineOnly && noDownloads) return false;
    return explicitCreationIntent(prompt,
      /(?:\.pdf|\.docx|\.xlsx|\.pptx|\.zip|\.csv|\.py|文件(?!清单|列表|目录|分析|架构|说明|路径|名称|内容|结构|摘要)|附件|downloadable file|spreadsheet)/i,
      true);
  }

  function internalToolPayload(value) {
    const raw = String(value || "").trim();
    if (!raw || raw.length > 12000 || raw.charCodeAt(0) === 96 || raw.startsWith("#")) return false;
    if (isInternalMediaHandoff(raw) || isImageToolArguments(raw) || isLocalImageBuildCommand(raw))
      return true;
    if (/^(?:bash\s+-lc\s+|python3?\s+-c\s+|sh\s+-c\s+)/i.test(raw)
        && !/[\r\n]{2,}/.test(raw)) return true;
    if (raw.startsWith("{") && raw.endsWith("}")) {
      try {
        const parsed = JSON.parse(raw);
        const keys = Object.keys(parsed || {});
        if (keys.length === 1 && keys[0] === "query" && typeof parsed.query === "string")
          return true;
        if (keys.includes("command") && keys.every(key =>
          ["command", "timeout", "cwd", "workdir", "shell"].includes(key))) return true;
      } catch (_) {}
    }
    return false;
  }

  function hasTransferableImage(value) {
    return /!\[[^\]]{0,400}\]\((?:data:image\/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+|https:\/\/[^)]+)\)/i.test(String(value || ""));
  }


  function isImageCreationRequest(prompt) {
    // An explicit "do not generate" instruction must not block text-only replies.
    return explicitCreationIntent(prompt,
      /(?:图片|图像|头像|插画|插图|参考图|图标|image|avatar|icon|illustration|picture)/i);
  }

  function isLocalImageBuildCommand(value) {
    // Raw PIL shell instructions are a recipe, not downloadable image bytes.
    // Fenced examples and explanatory text are not image generation results.
    const raw = String(value || "").trim();
    return raw.length > 0 && raw.length <= 6000
      && /^(?:bash\s+-lc\s+)?python(?:3)?\s+-\s+<<['"]?PY['"]?/i.test(raw)
      && /\bfrom\s+PIL\s+import\s+Image\b/.test(raw)
      && /\/mnt\/data\/[^\s"'()]+\.(?:png|jpe?g|webp|gif)\b/i.test(raw)
      && /\.(?:save|resize)\s*\(/.test(raw);
  }

  function combinePageMediaText(prose, mediaText) {
    const candidate = String(prose || "").trim();
    const imageMarkdown = String(mediaText || "");
    // The prose is read only from the correlated current assistant turn.
    // Reject internal tool control, duplicate images and browser-only blobs.
    if (!candidate || isInternalMediaHandoff(candidate)
        || isImageToolArguments(candidate) || isLocalImageBuildCommand(candidate))
      return imageMarkdown;
    // A separately rendered generated image can also appear as a preview
    // inside the same assistant turn. Remove only those image Markdown links,
    // not surrounding prose, tables, code files or generated-file handoffs.
    // The verified detached images are appended once after the surviving text.
    const preservedProse = candidate.replace(
      /!\[(?!📎 )[^\]\n]{0,400}\]\((?:blob:|https:\/\/|data:image\/)[^)\s]*\)/gi, "",
    ).trim();
    return [preservedProse, imageMarkdown].filter(Boolean).join("\n\n");
  }

  function confirmedTransportTerminal(active) {
    // request-v6 owns the ONLY outward completion. A provisional SSE end-turn
    // or generic protocol-complete event alone may be followed by thousands
    // of streamed bytes or a clone-reader abort. A clean EOF for that exact
    // stream is the minimum network proof of terminal delivery.
    return Boolean(Number(active.networkTerminalAt || 0) > 0
      && Number(active.networkTerminalTransportDoneAt || 0) >= Number(active.networkTerminalAt || 0)
      && String(active.networkTerminalTransportStreamId || "")
        === String(active.networkTerminalStreamId || "")
      && !Number(active.networkTerminalTransportErrorAt || 0));
  }

  async function reconcileResponsesTerminal(active, networkText) {
    const raw = String(networkText || "");
    // A completed short assistant preamble can precede an extensive rendered
    // document in the same response. The 2026-10-24 TX03 case emitted "以下是一"
    // (4 characters) after 131KB of SSE while the actual document was visible.
    // Neither that preamble nor the SSE protocol marker is proof that the
    // rich response has been captured. Preserve a single terminal owner:
    // request-v6 must cross-check the current request's rendered DOM first.
    if (raw.length < 64 && Number(active.networkTerminalBytes || 0) > 32768
        && String(active.options?.response_protocol || "") !== "responses-v108") {
      const deadline = Date.now() + 4200;
      let lastRendered = "", stableAt = 0;
      while (!active.cancelled && Date.now() < deadline) {
        const candidate = currentAssistantState(active);
        if (candidate.isNew && candidate.latest && !isGenerating()
            && ["verified-rendered-network-overlap-v189",
                "verified-rendered-dom-baseline-v191",
                "after-current-user-turn"].includes(candidate.reason)) {
          try {
            const captured = await finalNodeText(candidate.latest);
            const rendered = String(captured?.text || "").trim();
            if (rendered.length >= 240 && rendered.length >= raw.length * 8
                && !internalToolPayload(rendered) && !promptStillPresent(active)) {
              if (rendered !== lastRendered) {
                lastRendered = rendered;
                stableAt = Date.now();
              } else if (Date.now() - stableAt >= 1100) {
                return {
                  text: rendered,
                  source: "verified-short-sse-rich-dom-v192",
                };
              }
            }
          } catch (_) { /* A failed DOM read cannot prove a final answer. */ }
        }
        await delay(180);
      }
      // A large SSE response includes protocol metadata, not just answer text.
      // Many real customer-service answers are intentionally 10-60 characters:
      // the absence of a matching long-form DOM node cannot turn an explicit
      // completed assistant message into an error. Only POSITIVE evidence of a
      // larger same-request DOM answer may override the terminal SSE body.
      // Log this weaker evidence separately for rich-reply diagnostics.
      return { text: raw, source: "network-sse-short-unreconciled-v192" };
    }
    if (String(active.options?.response_protocol || "") !== "responses-v108") {
      // The network can expose the source markup of a ChatGPT rich answer.
      // It is not the same thing as the visible, rendered assistant reply.
      // A matching same-turn DOM capture can replace *source component tags*
      // with semantic Markdown; ordinary prose/code and Responses envelopes
      // still retain the stronger SSE body.
      const componentMarkup = /<\/?(?:box|row|col|grid|text|button|card|icon|svg|flow|carousel|WritingBlock|AppBlock|Chart)\b/i;
      if (componentMarkup.test(raw) && raw.length >= 150) {
        const until = Date.now() + 1800;
        while (!active.cancelled && Date.now() < until) {
          const candidateState = currentAssistantState(active);
          if (candidateState.isNew && candidateState.latest && !isGenerating()) {
            try {
              const captured = await finalNodeText(candidateState.latest);
              const markdown = String(captured?.text || "").trim();
              // This is a same-request turn, and it must share an actual
              // visible text fragment. Length is not proof of identity.
              const overlaps = markdown.split(/\n+/).map(line => normalize(line))
                .filter(line => line.length >= 18)
                .some(line => normalize(raw).includes(line.slice(0, 18)));
              if (overlaps && markdown.length >= 30
                  && !componentMarkup.test(markdown)
                  && !markdown.includes("![📎 ")) {
                return { text: markdown, source: "validated-rendered-same-turn-dom-v196" };
              }
            } catch (_) { /* untrusted or unavailable DOM never defeats SSE */ }
          }
          await delay(180);
        }
      }
      return { text: raw, source: "network-sse-terminal-v57" };
    }
    if (completeResponsesBridgeDocument(raw)) {
      return { text: raw, source: "network-sse-terminal-v57" };
    }
    // The request-v6 controller remains the only terminal authority. Accept
    // a same-turn DOM candidate only when it is a complete bridge document
    // containing the captured network fragment, never by length alone.
    const deadline = Date.now() + 2400;
    while (!active.cancelled && Date.now() < deadline) {
      const candidateState = currentAssistantState(active);
      if (candidateState.isNew && candidateState.latest) {
        try {
          const captured = await finalNodeText(candidateState.latest);
          const candidate = String(captured?.text || "").trim();
          if (candidate.length > raw.length && raw.trim().length >= 32
              && candidate.includes(raw.trim()) && completeResponsesBridgeDocument(candidate)) {
            return { text: candidate, source: "validated-same-turn-dom-v163" };
          }
        } catch (_) {
          // An unavailable DOM capture must never discard the verified SSE fragment.
        }
      }
      await delay(180);
    }
    return { text: raw, source: "network-sse-terminal-v57" };
  }

  async function monitor(active) {
    const timeoutMs = Math.max(5000, Number(active.options.timeout_seconds || 300) * 1000);
    const startedAt = Date.now();
    let responseStarted = false;
    let lastText = "";
    let lastIdentity = "";
    let stableSince = 0;
    let imageCandidate = "";
    let imageCandidateSince = 0;
    let imageCaptureAt = 0;
    let imageHandoffObserved = false;
    let imageBuildArtifactObserved = false;
    let verifiedRenderedCandidate = "";
    let verifiedRenderedCandidateSince = 0;
    let abortedRenderedCandidate = "";
    let abortedRenderedCandidateSince = 0;
    let lastAbortedRenderedCaptureAt = 0;
    const imageCreationRequested = isImageCreationRequest(active.prompt);
    const fileCreationRequested = isFileCreationRequest(active.prompt);

    while (!active.cancelled && Date.now() - startedAt < timeoutMs) {
      const upstreamLimit = upstreamRateLimitFailure(active);
      if (upstreamLimit) throw upstreamLimit;
      const error = pageError();
      if (error && responseStarted) throw new Error(`ChatGPT response UI error: ${error}`);
      const current = currentAssistantState(active);
      active.lastCandidateNode = current.isNew ? current.latest : null;
      active.lastCandidateTextLength = current.isNew ? current.text.length : 0;
      const generating = isGenerating();
      const networkText = String(active.networkObservedText || "");
      const networkTerminalText = String(active.networkTerminalText || "");
      const networkTerminalAt = Number(active.networkTerminalAt || 0);
      const imageHandoff = isInternalMediaHandoff(networkText)
        || isInternalMediaHandoff(networkTerminalText);
      const imageToolArguments = imageCreationRequested
        && (isImageToolArguments(networkText) || isImageToolArguments(networkTerminalText));
      // Only request-v6 decides terminal state. A generated-image request
      // that received a raw local PIL command must try same-request media
      // capture before completing; such instructions are not an image.
      const imageBuildArtifact = imageCreationRequested
        && (isLocalImageBuildCommand(networkTerminalText)
          || isLocalImageBuildCommand(networkText));
      const sameTurnRoot = current.isNew && current.latest ? (current.turn || turnOf(current.latest)) : null;
      // New ChatGPT Office cards are often siblings of the text candidate,
      // not children of its .markdown node. Only the request controller may
      // widen capture to a verified fresh reply + finish-toolbar scope.
      // Reuse the existing positively finished same-request reply scope. The
      // native byte observer has no right to widen it or decide completion.
      const fileReplyBoundary = fileCreationRequested && !generating
        && !upstreamStillWorking()
        && Number(active.networkTerminalTransportErrorAt || 0) > 0
        ? verifiedRenderedAbortCandidate(active, current) : null;
      const mediaReplyRoot = fileReplyBoundary?.replyScope || sameTurnRoot;
      const visibleTurnMedia = Boolean(mediaReplyRoot && rich?.hasCandidateImage?.(mediaReplyRoot));
      const visibleTurnFiles = Boolean(mediaReplyRoot && rich?.hasCandidateFile?.(mediaReplyRoot));
      const visibleTurnPreviews = Boolean(mediaReplyRoot && rich?.hasPreviewFile?.(mediaReplyRoot));
      // Image-only assistant turns can lack a data-message-author-role node.
      // Compare visible chat-main images against the snapshot taken before
      // submitting this exact request; never use arbitrary document images.
      const userAnchor = currentUserTurn(active);
      const freshUserAnchor = userAnchor && !active.baselineUserTurns?.has(userAnchor)
        ? userAnchor : null;
      // Only this request's authenticated media-tool handoff enables
      // intrinsic-size-only image cards; generic page images stay excluded.
      const intrinsicHandoff = Boolean(imageHandoff) || imageBuildArtifact || imageToolArguments;
      const discovered = rich?.locateNewPageMedia?.(
        active.baselinePageMedia, freshUserAnchor, intrinsicHandoff,
      ) || { images: [], candidate_count_document: 0, new_image_candidate_count: 0,
        after_user_count: 0, baseline_image_count: 0, anchor: "none" };
      const pageMediaFound = discovered.images.length > 0;
      const captureMedia = imageHandoff || imageBuildArtifact || imageToolArguments
        || visibleTurnMedia || visibleTurnFiles || visibleTurnPreviews || fileCreationRequested || pageMediaFound;
      const captureOrigin = pageMediaFound && !visibleTurnMedia ? "new-page-media" : "assistant-turn";
      const sourceMedia = pageMediaFound && !visibleTurnMedia ? discovered.images : [];
      const sourceFingerprint = sourceMedia.map(img =>
        String(img.currentSrc || img.getAttribute("src") || "")).join("|");
      if (sourceFingerprint !== active.mediaSourceFingerprint) {
        active.mediaSourceFingerprint = sourceFingerprint;
        active.mediaSourceStableAt = Date.now();
        imageCandidate = "";
        imageCandidateSince = 0;
      }
      const sourceStable = !sourceMedia.length || (
        active.mediaSourceStableAt && Date.now() - active.mediaSourceStableAt >= 1600
      );
      if (imageHandoff || imageToolArguments || visibleTurnMedia || visibleTurnFiles
          || fileCreationRequested || pageMediaFound) imageHandoffObserved = true;
      if (imageBuildArtifact) imageBuildArtifactObserved = true;
      try { globalThis.__CHAT2API_PROBE_SUPERVISOR_V1__?.observe?.(active, {
        handoff: imageHandoff, generating, newCandidates: discovered.new_image_candidate_count,
        captured: Number(active.mediaCaptureLastInlined || 0) + Number(active.mediaCaptureLastEmbedded || 0),
      }); } catch (_) {}
      active.mediaLastScanCandidates = discovered.candidate_count_document;
      active.mediaLastScanNewCandidates = discovered.new_image_candidate_count;
      active.mediaLastTrustedNewSources = Number(discovered.trusted_fresh_sources || 0);
      active.mediaLastBaselineExcluded = Number(discovered.excluded_by_baseline || 0);
      active.mediaLastEligibilityExcluded = Number(discovered.excluded_by_media_gate || 0);
      if ((imageHandoff || imageBuildArtifact || imageToolArguments) && !sameTurnRoot && !pageMediaFound
          && (!active.mediaScanReportedAt || Date.now() - active.mediaScanReportedAt >= 12000)) {
        active.mediaScanReportedAt = Date.now();
        await diagnostic(active, "media-awaiting-image-anchor", {
          media_local_build_command_observed: imageBuildArtifact,
          media_image_creation_requested: imageCreationRequested,
          media_baseline_key_count: Number(active.baselinePageMedia?.keys?.size || 0),
          media_assistant_nodes_found: current.nodes.length,
          media_assistant_current_found: current.isNew,
          media_assistant_reason: current.reason,
          media_baseline_images: discovered.baseline_image_count,
          media_document_candidates: discovered.candidate_count_document,
          media_scoped_image_nodes: discovered.scoped_image_nodes,
          media_trusted_scoped_images: discovered.trusted_scoped_images,
          media_trusted_fresh_sources: discovered.trusted_fresh_sources,
          media_excluded_by_baseline: discovered.excluded_by_baseline,
          media_excluded_by_gate: discovered.excluded_by_media_gate,
          media_intrinsic_candidate_gate: discovered.intrinsic_candidate_gate,
          media_new_candidates: discovered.new_image_candidate_count,
          media_after_user_candidates: discovered.after_user_count,
          media_source_diagnostics_version: 176,
        });
      }
      if (pageMediaFound && !active.mediaPageCandidateDiagnosed) {
        active.mediaPageCandidateDiagnosed = true;
        await diagnostic(active, "media-new-image-card-found", {
          media_capture_origin: captureOrigin,
          media_assistant_reason: current.reason,
          media_new_candidates: discovered.new_image_candidate_count,
          media_after_user_candidates: discovered.after_user_count,
          media_baseline_images: discovered.baseline_image_count,
          media_source_diagnostics_version: 176,
        });
      }
      if (captureMedia && !active.mediaHandoffDiagnosed) {
        active.mediaHandoffDiagnosed = true;
        await diagnostic(active, "media-handoff-detected", {
          media_handoff_marker: imageHandoff,
          media_local_build_command_observed: imageBuildArtifact,
          media_turn_visible_image: visibleTurnMedia,
          media_turn_downloadable_files: visibleTurnFiles,
          media_turn_file_previews: visibleTurnPreviews,
          media_reply_scope_verified: Boolean(fileReplyBoundary),
          media_file_creation_requested: fileCreationRequested,
          media_same_turn_anchor: current.reason,
          media_capture_origin: captureOrigin,
          media_document_candidates: discovered.candidate_count_document,
          media_new_candidates: discovered.new_image_candidate_count,
          media_response_epoch_revision: 176,
        });
      }

      // ChatGPT's image-generation tool can end the main text stream with
      // {"skipped_mainline":true} while the actual image is rendered later.
      // Only request-v6 can complete this request; harvest same-turn media,
      // never send the internal marker as the user-visible answer.
      if (captureMedia && (mediaReplyRoot || sourceMedia.length) && sourceStable && !generating
          && Date.now() - imageCaptureAt >= 1000) {
        imageCaptureAt = Date.now();
        // Prefer the validated current assistant turn, but permit a strictly
        // post-baseline new-image card when the page supplies no assistant role.
        const finalMedia = sourceMedia.length
          ? await rich.capturePageImages(sourceMedia, {allowIntrinsic: intrinsicHandoff})
          : await finalNodeText(mediaReplyRoot);
        // On redesigned pages image cards may be siblings of the assistant
        // Markdown. Preserve same-request prose alongside those fresh images.
        const assistantRich = sourceMedia.length && current.isNew && sameTurnRoot
          ? await finalNodeText(sameTurnRoot) : null;
        const assistantProse = String(assistantRich?.text || "").trim();
        // A click-only ChatGPT download button has no usable href. In the
        // verified current reply only, trigger it once and capture the actual
        // browser-produced bytes. Never infer success from a preview or button.
        const native = fileCreationRequested && fileReplyBoundary
          && !visibleTurnFiles && !upstreamStillWorking()
          && Date.now() - Number(active.networkObservedAt || 0) >= 2500
          ? await globalThis.__CHAT2API_NATIVE_FILE_V210__?.capture?.(
              fileReplyBoundary.replyScope, active.requestId) : null;
        const serialized = combinePageMediaText(assistantProse, finalMedia?.text);
        const captured = [serialized, native?.text || ""].filter(Boolean).join("\n\n");
        active.mediaCaptureAttempts = Number(active.mediaCaptureAttempts || 0) + 1;
        active.mediaCaptureLastChars = captured.length;
        active.mediaCaptureLastInlined = Number(finalMedia?.image_inlined_count || 0);
        active.mediaCaptureLastEmbedded = Number(finalMedia?.image_embedded_count || 0);
        active.mediaCaptureTransferStats = finalMedia?.image_transfer_stats || null;
        active.mediaCaptureOrigin = sourceMedia.length ? captureOrigin : "assistant-turn";
        // Never commit a browser-only blob URL or an inaccessible CDN preview.
        // The rich serializer must actually transport an image to the API.
        const deliveredImages = Number(finalMedia?.image_inlined_count || 0)
          + Number(finalMedia?.image_embedded_count || 0);
        const deliveredFiles = Number(finalMedia?.file_inlined_count || 0)
          + Number(assistantRich?.file_inlined_count || 0)
          + Number(native?.count || 0);
        const requiredFileExts = [...new Set(
          (requestedUserText(active.prompt).match(/(?:\.(?:xlsx|pptx|json|zip|pdf|docx|csv)\b|\b(?:xlsx|pptx|json|zip|pdf|docx|csv)\b)/gi) || [])
            .map(ext => "." + ext.replace(/^\./, "").toLowerCase())
        )];
        const allRequiredFileTypesDelivered = requiredFileExts.every(ext =>
          captured.match(new RegExp("!\\[📎 [^\\]\\n]{1,180}" +
            ext.replace(".", "\\.") + "\\]\\(data:[^\\s)]+;base64,", "i")));
        const delivered = deliveredImages + deliveredFiles;
        if (delivered > 0 && (hasTransferableImage(captured) || hasTransferableFile(captured))
            && (!fileCreationRequested || (deliveredFiles > 0 && allRequiredFileTypesDelivered))
            && (!imageCreationRequested || deliveredImages > 0)) {
          if (captured !== imageCandidate) {
            imageCandidate = captured;
            imageCandidateSince = Date.now();
          } else if (Date.now() - imageCandidateSince >= 2400) {
            responseStarted = await ensureResponseStarted(active, responseStarted, "verified-media-dom-v176");
            await emit({
              type: "chat.completed",
              request_id: active.requestId,
              text: captured,
              diagnostics: {
                response_terminal_source: "verified-request-media-dom-v176",
                response_media_turn_scope: sourceMedia.length ? "new-since-submit" : "current-assistant-turn",
                response_media_fallback_anchor: sourceMedia.length ? discovered.anchor : "assistant-turn",
                media_new_candidates: discovered.new_image_candidate_count,
                response_media_embedded_count: Number(finalMedia?.image_embedded_count || 0),
                response_format: "markdown",
                response_image_inlined_count: Number(finalMedia?.image_inlined_count || 0),
                response_image_inlined_bytes: Number(finalMedia?.image_inlined_bytes || 0),
                response_generated_file_count: deliveredFiles,
                response_generated_file_bytes: Number(finalMedia?.file_inlined_bytes || 0)
                  + Number(assistantRich?.file_inlined_bytes || 0)
                  + Number(native?.bytes || 0),
                response_native_file_handoff: Boolean(native?.count),
                response_required_file_types_satisfied: allRequiredFileTypesDelivered,
                internal_tool_handoff_filtered: true,
              },
            });
            active.completed = true;
            return;
          }
        } else {
          imageCandidate = "";
          imageCandidateSince = 0;
          if (!active.mediaCaptureLastReportAt || Date.now() - active.mediaCaptureLastReportAt >= 8000) {
            active.mediaCaptureLastReportAt = Date.now();
            await diagnostic(active, "media-not-transferable", {
              media_capture_attempts: active.mediaCaptureAttempts,
              media_capture_candidate_chars: active.mediaCaptureLastChars,
              media_capture_inline_count: active.mediaCaptureLastInlined,
              media_file_transfer_error: native?.error || finalMedia?.file_transfer_error || assistantRich?.file_transfer_error || "",
              media_file_preview_count: Number(finalMedia?.file_preview_count || 0),
              media_native_file_button_candidates: Number(native?.button_candidates || 0),
              media_native_file_capture_count: Number(native?.count || 0),
              media_native_file_captured_types: native?.captured_types || [],
              media_native_file_failed_types: native?.failed_types || [],
              media_native_file_attempt_counts: native?.attempt_counts || {},
              media_native_file_synthesized_zip: native?.synthesized_zip === true,
              media_required_file_types_satisfied: allRequiredFileTypesDelivered,
              media_reply_scope_verified: Boolean(fileReplyBoundary),
              media_capture_embedded_count: active.mediaCaptureLastEmbedded,
              media_transfer_stats: active.mediaCaptureTransferStats,
              media_turn_scope: current.reason,
              media_capture_origin: active.mediaCaptureOrigin,
              media_document_candidates: discovered.candidate_count_document,
              media_new_candidates: discovered.new_image_candidate_count,
              media_response_epoch_revision: 176,
            });
          }
        }
      }

      if ((current.isNew || generating || networkText || visibleTurnMedia || pageMediaFound) && !responseStarted) {
        responseStarted = await ensureResponseStarted(active, responseStarted, networkText ? "network-evidence-v57" : current.isNew ? "dom-assistant" : "generation-control");
      }
      if (current.isNew && current.text && !captureMedia && !internalToolPayload(current.text)
          && !(imageCreationRequested && isLocalImageBuildCommand(current.text))) {
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
      // Media/file generation is committed only after verified archiveable bytes.
      // Raw network snapshots can contain temporary download URLs or tool args.
      if (networkText && !captureMedia && !fileCreationRequested && !imageCreationRequested
          && !internalToolPayload(networkText) && !imageBuildArtifact) {
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
      if (networkTerminalAt && networkTerminalText && !captureMedia
          && confirmedTransportTerminal(active)
          && !internalToolPayload(networkTerminalText)
          && !(imageCreationRequested && isLocalImageBuildCommand(networkTerminalText))
          && !promptStillPresent(active)
          && Date.now() - evidenceSettledAt >= 1250) {
        responseStarted = await ensureResponseStarted(active, responseStarted, "network-terminal-v57");
        // Only this controller can commit the terminal body. Use the verified
        // SSE final for this request; DOM and intermediary observers provide
        // progress, not competing final answers.
        const reconciled = await reconcileResponsesTerminal(active, networkTerminalText);
        if (active.cancelled) break;
        if (String(active.networkTerminalText || "") !== networkTerminalText) continue;
        if (!reconciled.text) {
          await emit({
            type: "chat.error", request_id: active.requestId,
            error: "ChatGPT signaled completion but the full rich reply could not be verified",
            diagnostics: {
              response_integrity_guard: "incomplete-short-sse-rich-reply-v192",
              response_terminal_network_chars: networkTerminalText.length,
              network_stream_bytes: Number(active.networkTerminalBytes || 0),
              response_terminal_source: reconciled.source,
              response_terminal_dom_reconciled: false,
            },
          });
          return;
        }
        lastText = await updateCapturedText(active, reconciled.text, lastText);
        await emit({
          type: "chat.completed",
          request_id: active.requestId,
          text: reconciled.text,
          diagnostics: {
            response_epoch_revision: 69,
            response_terminal_settle_revision: 82,
            response_terminal_source: reconciled.source,
            response_terminal_dom_reconciled: ["validated-same-turn-dom-v163", "verified-short-sse-rich-dom-v192", "validated-rendered-same-turn-dom-v196"].includes(reconciled.source),
            response_terminal_stable_ms: Date.now() - networkTerminalAt,
            response_epoch_candidate_reason: active.lastCandidateReason || "network-assistant-evidence-v57",
            network_stream_id: String(active.networkTerminalStreamId || ""),
            network_stream_sequence: Number(active.networkTerminalSequence || 0),
            network_stream_chunks: Number(active.networkTerminalChunks || 0),
            network_stream_bytes: Number(active.networkTerminalBytes || 0),
            network_terminal_evidence_source: String(active.networkTerminalEvidenceSource || "stream-eof"),
            network_transport_terminal_confirmed: true,
            network_terminal_transport: "clean-same-stream-eof-v199",
            response_terminal_network_chars: networkTerminalText.length,
            response_terminal_final_chars: reconciled.text.length,
            response_format: "markdown",
            response_image_inlined_count: 0,
            response_image_inlined_bytes: 0,
          },
        });
        active.completed = true;
        return;
      }

      // v196: A stable rendered DOM is NEVER sufficient when a network stream
      // has not proved final assistant completion. TX03 req_d12e2aca
      // incorrectly committed a 1338-char partial report at 39s while SSE was
      // still streaming. This fallback must also have networkTerminalAt.
      // The existing network-terminal branch above remains preferred.
      // The redesigned ChatGPT UI sometimes omits assistant role nodes AND the
      // clone reader loses its terminal frame. A current-request SSE fragment
      // independently correlated with a rendered node that remains stable
      // after generation is a bounded, explicit UI terminal fallback.
      // No tool command, search JSON, prompt echo, or media-producing request
      // can satisfy this proof. request-v6 remains the sole terminal owner.
      if (networkTerminalAt && networkTerminalText && !captureMedia
          && confirmedTransportTerminal(active)
          && !fileCreationRequested && !imageCreationRequested
          && ["verified-rendered-network-overlap-v189","verified-rendered-dom-baseline-v191"].includes(current.reason)
          && current.text.length >= 120 && !internalToolPayload(current.text)
          && !generating && !promptStillPresent(active)
          && active.networkObservedAt && Date.now() - active.networkObservedAt >= 4500) {
        if (current.text !== verifiedRenderedCandidate) {
          verifiedRenderedCandidate = current.text;
          verifiedRenderedCandidateSince = Date.now();
        } else if (Date.now() - verifiedRenderedCandidateSince >= 3500) {
          const result = await finalNodeText(current.latest);
          const finalized = String(result?.text || current.text || "").trim();
          if (finalized.length >= 120 && !internalToolPayload(finalized)
              && !isGenerating() && !promptStillPresent(active)) {
            responseStarted = await ensureResponseStarted(active, responseStarted,
              "verified-rendered-network-overlap-v189");
            await emit({
              type: "chat.completed", request_id: active.requestId, text: finalized,
              diagnostics: {
                response_terminal_source: current.reason === "verified-rendered-dom-baseline-v191"
                  ? "same-request-rendered-dom-baseline-v191" : "same-request-network-rendered-dom-v189",
                response_terminal_dom_reconciled: true,
                response_integrity_evidence: current.reason === "verified-rendered-dom-baseline-v191"
                  ? "network-accepted-changed-main-dom-pre-submit-baseline-stable" : "network-text-65-character-overlap-pre-submit-dom-baseline",
                network_terminal_evidence_source: "rendered-dom-stable-after-network-observation",
                response_terminal_final_chars: finalized.length,
                response_format: "markdown",
              },
            });
            active.completed = true;
            return;
          }
        }
      } else {
        verifiedRenderedCandidate = "";
        verifiedRenderedCandidateSince = 0;
      }

      // v200: Chrome can abort an SSE response clone after ChatGPT has rendered
      // the actual final answer. The observer has no completion authority and
      // the aborted transport is NOT a successful network terminal. Permit only
      // a positively completed same-request DOM answer: changed since the
      // pre-submit baseline, new answer toolbar, quiet network, no Stop button,
      // and an independently stable rich-text capture. The request-v6 controller
      // remains the one and only owner of chat.completed/chat.error.
      if (!captureMedia && !fileCreationRequested && !imageCreationRequested
          && !confirmedTransportTerminal(active)
          && active.networkTerminalTransportErrorAt
          && Date.now() - active.networkTerminalTransportErrorAt >= 5000
          && active.networkObservedAt && Date.now() - active.networkObservedAt >= 10000
          && !generating && !upstreamStillWorking()) {
        const rendered = verifiedRenderedAbortCandidate(active, current)
          || verifiedShortAbortCandidate(active);
        if (rendered && Date.now() - lastAbortedRenderedCaptureAt >= 750) {
          lastAbortedRenderedCaptureAt = Date.now();
          try {
            // Serialize the entire changed reply surface, not just the largest
            // child. The v200 TX03 case selected only a Python block while
            // preceding tables, narrative, formulas and task list were siblings.
            const parts = rendered.coverage.nodes;
            const wrapper = document.createElement("div");
            wrapper.setAttribute("class", "markdown");
            for (const part of parts) wrapper.appendChild(part.cloneNode(true));
            // Short replies have one tightly scoped network-correlated node.
            // Do not concatenate duplicate page wrappers into the reply.
            const capture = rendered.shortTextVerified
              ? await finalNodeText(rendered.node)
              : typeof rich?.captureFinalMarkdown === "function"
                ? await rich.captureFinalMarkdown(wrapper) : {text:""};
            // Redesigned ChatGPT sometimes includes empty role labels in the
            // cloned reply container. Remove ONLY the known leading chrome;
            // never strip an ordinary user-authored heading in the body.
            const bounded = assistantOnlyReplyText(active, capture?.text);
            if (bounded.pending) continue;
            const finalText = String(bounded.text || "").trim();
            const completePartSet = parts.every(part => part.isConnected
              && visible(part) && !promptStillPresent(active));
            if (completePartSet
                && (rendered.shortTextVerified
                  ? finalText.length >= 8 && finalText.length <= 220
                    && normalize(finalText).includes(normalize(String(active.networkObservedText || "")).slice(0, 8))
                  : finalText.length >= 240)
                && finalText.length >= rendered.text.length * 0.85
                && !internalToolPayload(finalText)
                && !promptStillPresent(active) && !upstreamStillWorking()) {
              if (finalText !== abortedRenderedCandidate) {
                abortedRenderedCandidate = finalText;
                abortedRenderedCandidateSince = Date.now();
              } else if (Date.now() - abortedRenderedCandidateSince >= 8000
                  && Date.now() - Number(active.networkObservedAt || 0) >= 12000
                  && rendered.finishAction?.isConnected
                  && responseFinishActions().includes(rendered.finishAction)
                  && replyScopeForFinishAction(current.latest, rendered.finishAction)
                    === rendered.replyScope
                  && parts.every(node => turnFollows(node, rendered.finishAction))
                  && !upstreamStillWorking()
                  && (!rendered.shortTextVerified || (
                    normalize(String(active.networkObservedText || "")).length >= 8
                    && normalize(finalText).includes(normalize(String(active.networkObservedText || "")).slice(0, 8))
                  ))) {
                responseStarted = await ensureResponseStarted(active, responseStarted,
                  "verified-aborted-stream-rendered-dom-v200");
                await emit({
                  type: "chat.completed", request_id: active.requestId, text: finalText,
                  diagnostics: {
                    response_terminal_source: rendered.shortTextVerified
                      ? "verified-short-sse-rendered-answer-v207"
                      : "verified-aborted-stream-rendered-dom-v200",
                    response_terminal_dom_reconciled: true,
                    response_integrity_evidence: rendered.shortTextVerified
                      ? "same-request-short-sse+fresh-scope-toolbar+8s-stable+aborted-sse"
                      : "fresh-rendered-baseline+new-finish-action+8s-stable+aborted-sse",
                    response_epoch_candidate_reason: rendered.reason,
                    response_terminal_final_chars: finalText.length,
                    response_terminal_stable_ms: Date.now() - abortedRenderedCandidateSince,
                    response_finish_action_count: rendered.finishActionCount,
                    response_integrity_scope: "same-answer-container-before-toolbar-v206",
                    response_terminal_live_working_guard: true,
                    response_integrity_candidate_chars: rendered.coverage.candidate_chars,
                    response_integrity_uncovered_blocks: rendered.coverage.independent_count,
                    response_integrity_merged_blocks: parts.length,
                    response_integrity_full_block_count: rendered.coverage.nodes.length,
                    response_integrity_structural_scan_nodes: rendered.coverage.scan_structural_node_count,
                    response_integrity_scan_overflow: false,
                    response_integrity_merged_final_chars: finalText.length,
                    network_transport_terminal_confirmed: false,
                    network_transport_error: String(active.networkTerminalTransportErrorReason || ""),
                    response_format: "markdown",
                  },
                });
                active.completed = true;
                return;
              }
            } else { abortedRenderedCandidate = ""; abortedRenderedCandidateSince = 0; }
          } catch (_) {
            abortedRenderedCandidate = "";
            abortedRenderedCandidateSince = 0;
          }
        } else if (!rendered) { abortedRenderedCandidate = ""; abortedRenderedCandidateSince = 0; }
        if (!active.abortRecoveryReportedAt || Date.now() - active.abortRecoveryReportedAt >= 20000) {
          active.abortRecoveryReportedAt = Date.now();
          await diagnostic(active, "aborted-sse-dom-recovery", {
            response_recovery_revision: 200,
            response_recovery_candidate_reason: current.reason,
            response_recovery_candidate_chars: String(current.text || "").length,
            response_recovery_finish_actions: responseFinishActions()
              .filter(button => !active.baselineFinishActions.has(button)).length,
            response_recovery_mergeable_blocks: current.latest
              ? renderedResponseBoundaryEvidence(active, current.latest).nodes.length : null,
            response_recovery_uncovered_blocks: current.latest
              ? renderedResponseBoundaryEvidence(active, current.latest).independent_count : null,
            response_recovery_uncovered_chars: current.latest
              ? renderedResponseBoundaryEvidence(active, current.latest).independent_chars : null,
            response_recovery_scan_overflow: current.latest
              ? renderedResponseBoundaryEvidence(active, current.latest).scan_overflow : null,
            response_recovery_structural_scan_nodes: current.latest
              ? renderedResponseBoundaryEvidence(active, current.latest).scan_structural_node_count : null,
            response_recovery_finish_after_all_blocks: Boolean(
              verifiedRenderedAbortCandidate(active, current)),
            response_recovery_native_working: upstreamStillWorking(),
            response_recovery_scoped_finish_actions: current.latest
              ? responseFinishActions().filter(button =>
                  !active.baselineFinishActions.has(button)
                  && replyScopeForFinishAction(current.latest, button)).length : 0,
            response_recovery_stable_ms: abortedRenderedCandidateSince
              ? Date.now() - abortedRenderedCandidateSince : 0,
            response_recovery_network_quiet_ms: Date.now() - active.networkObservedAt,
            network_stream_error: String(active.networkTerminalTransportErrorReason || ""),
          });
        }
      } else {
        abortedRenderedCandidate = "";
        abortedRenderedCandidateSince = 0;
      }

      // A DOM pause or a missing Stop button cannot override an observed SSE
      // response that has not reached a verified assistant terminal.
      if (active.networkStreamEndedWithoutTerminalAt && !networkTerminalAt && !captureMedia
          && Date.now() - active.networkStreamEndedWithoutTerminalAt >= 3000) {
        await emit({
          type: "chat.error", request_id: active.requestId,
          error: "ChatGPT network stream ended without a verified assistant completion",
          diagnostics: { response_integrity_guard: "verified-network-terminal-v125", network_stream_unverified_end: true },
        });
        return;
      }
      if (responseStarted && lastText && !generating && !captureMedia && !active.networkStreamSeen
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
    else await emit({ type: "chat.error", request_id: active.requestId, error: imageBuildArtifactObserved
      ? "ChatGPT returned an image-building command, but no verified transferable image was captured"
      : imageHandoffObserved ? "ChatGPT generated media or image-tool arguments but no transferable image was captured before the request timeout"
      : Number(active.networkTerminalTransportErrorAt || 0) > 0
        ? "ChatGPT network transport ended without a verifiable final answer: "
          + String(active.networkTerminalTransportErrorReason || "stream-incomplete")
        : active.networkTerminalAt && !confirmedTransportTerminal(active)
          ? "ChatGPT emitted a provisional end-turn, but the network stream never ended cleanly"
          : "Timed out waiting for ChatGPT response", diagnostics: {
        response_epoch_revision: 69,
        network_terminal_completion_guard: "clean-same-stream-eof-v199",
        network_terminal_provisional: Boolean(active.networkTerminalAt),
        network_transport_terminal_confirmed: confirmedTransportTerminal(active),
        network_terminal_transport_error: String(active.networkTerminalTransportErrorReason || ""),
        network_terminal_transport_error_at: Number(active.networkTerminalTransportErrorAt || 0),
        media_local_build_command_observed: imageBuildArtifactObserved,
        media_image_creation_requested: imageCreationRequested,
        media_baseline_key_count: Number(active.baselinePageMedia?.keys?.size || 0),
        media_capture_attempts: Number(active.mediaCaptureAttempts || 0),
        media_transfer_stats: active.mediaCaptureTransferStats || null,
        media_last_scan_candidates: Number(active.mediaLastScanCandidates || 0),
        media_last_scan_new_candidates: Number(active.mediaLastScanNewCandidates || 0),
        media_last_trusted_fresh_sources: Number(active.mediaLastTrustedNewSources || 0),
        media_last_baseline_excluded: Number(active.mediaLastBaselineExcluded || 0),
        media_last_eligibility_excluded: Number(active.mediaLastEligibilityExcluded || 0),
        ...(imageHandoffObserved && {
          internal_tool_handoff_without_media: true,
          media_capture_attempts: Number(active.mediaCaptureAttempts || 0),
          media_capture_candidate_chars: Number(active.mediaCaptureLastChars || 0),
          media_capture_inline_count: Number(active.mediaCaptureLastInlined || 0),
          media_capture_embedded_count: Number(active.mediaCaptureLastEmbedded || 0),
          media_transfer_stats: active.mediaCaptureTransferStats,
          media_capture_origin: active.mediaCaptureOrigin || "none",
          media_last_scan_candidates: Number(active.mediaLastScanCandidates || 0),
          media_last_scan_new_candidates: Number(active.mediaLastScanNewCandidates || 0),
        }),
      } });
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
      baselineUserTurns: new Set(),
      baselinePageMedia: null,
      mediaSourceFingerprint: "",
      mediaSourceStableAt: 0,
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
      networkTerminalTransportDoneAt: 0,
      networkTerminalTransportStreamId: "",
      networkTerminalTransportErrorAt: 0,
      networkTerminalTransportErrorReason: "",
      baselineFinishActions: null,
      networkStreamSeen: false,
      networkStreamEndedWithoutTerminalAt: 0,
    };
    state.active = active;
    if (v5) v5.active = active;
    const probeSupervisor = globalThis.__CHAT2API_PROBE_SUPERVISOR_V1__;
    try { probeSupervisor?.begin?.(active); } catch (_) {}

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
    promptDriftSummary,
    editorLayoutWhitespaceEvidence,
    turnFollows,
    plainNodeText,
    finalNodeText,
    isGenerating,
    isImageCreationRequest,
    isLocalImageBuildCommand,
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