(() => {
  const KEY = "__CHAT2API_CHATGPT_CHOICE_POLICY_V143__";
  if (globalThis[KEY]) return;

  const state = {
    revision: 143,
    observer: null,
    hiddenReply2: new Map(),
    lastWorkModeDismissAt: 0,
    lastDualReplyAt: 0,
  };
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

  function activeRequest() {
    return globalThis.__CHAT2API_REQUEST_CONTENT_V6__?.active
      || globalThis.__CHAT2API_REQUEST_CONTENT_V5__?.active
      || null;
  }

  function textOf(element) {
    return normalize(`${element?.getAttribute?.("aria-label") || ""} ${element?.title || ""} ${element?.innerText || element?.textContent || ""}`);
  }

  async function diagnostic(active, extra) {
    if (!active?.requestId) return;
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "chat.diagnostics",
          request_id: active.requestId,
          diagnostics: {
            chatgpt_choice_policy_revision: 143,
            ...extra,
          },
        },
      });
    } catch (_) {}
  }

  function stayInChatButton() {
    return [...document.querySelectorAll("button")].find(button => {
      if (!visible(button) || button.disabled || button.getAttribute("aria-disabled") === "true") return false;
      const text = normalize(button.innerText || button.textContent || button.getAttribute("aria-label") || button.title || "").toLowerCase();
      return /^(留在聊天模式|继续聊天|stay in chat mode|stay in chat|keep chatting)(?:\s+\d{1,3})?$/.test(text);
    }) || null;
  }

  function dismissWorkModeOffer(active) {
    const button = stayInChatButton();
    if (!button || Date.now() - state.lastWorkModeDismissAt < 1000) return false;
    button.click();
    state.lastWorkModeDismissAt = Date.now();
    void diagnostic(active, {
      work_mode_offer_detected: true,
      work_mode_offer_action: "stay-in-chat",
    });
    return true;
  }

  function assistantNodesAfterCurrentUser(active) {
    const requestState = globalThis.__CHAT2API_REQUEST_CONTENT_V6__;
    const contract = requestState?.contract;
    if (!contract?.currentUserTurn || !contract?.turnFollows) return [];
    const currentUser = contract.currentUserTurn(active);
    if (!currentUser) return [];
    const seen = new Set();
    const nodes = [];
    for (const node of document.querySelectorAll("[data-message-author-role='assistant']")) {
      if (seen.has(node) || !visible(node)) continue;
      const turn = node.closest?.("article[data-testid^='conversation-turn'],[data-testid^='conversation-turn'],article[data-message-id]") || node;
      if (!contract.turnFollows(currentUser, turn)) continue;
      seen.add(node);
      nodes.push(node);
    }
    return nodes;
  }

  function ancestorWithReplyLabel(node, index) {
    const pattern = index === 1 ? /(?:回复|response|reply)\s*1\b/i : /(?:回复|response|reply)\s*2\b/i;
    let current = node;
    for (let depth = 0; current && depth < 7; depth += 1, current = current.parentElement) {
      const text = textOf(current);
      if (pattern.test(text)) return current;
    }
    return null;
  }

  function dualReplyPair(active) {
    const nodes = assistantNodesAfterCurrentUser(active);
    if (nodes.length < 2) return null;
    for (let firstIndex = 0; firstIndex < nodes.length - 1; firstIndex += 1) {
      const first = nodes[firstIndex];
      const firstCard = ancestorWithReplyLabel(first, 1);
      if (!firstCard) continue;
      for (let secondIndex = firstIndex + 1; secondIndex < nodes.length; secondIndex += 1) {
        const second = nodes[secondIndex];
        const secondCard = ancestorWithReplyLabel(second, 2);
        if (!secondCard || firstCard === secondCard) continue;
        if (firstCard.contains(secondCard) || secondCard.contains(firstCard)) continue;
        return { reply1: first, reply1Card: firstCard, reply2: second, reply2Card: secondCard };
      }
    }
    return null;
  }

  function restoreHiddenReplies() {
    for (const [element, previous] of state.hiddenReply2.entries()) {
      if (!element?.isConnected) continue;
      if (previous.ariaHidden == null) element.removeAttribute("aria-hidden");
      else element.setAttribute("aria-hidden", previous.ariaHidden);
      if (previous.visibility == null) element.style.removeProperty("visibility");
      else element.style.visibility = previous.visibility;
      if (previous.position == null) element.style.removeProperty("position");
      else element.style.position = previous.position;
      if (previous.pointerEvents == null) element.style.removeProperty("pointer-events");
      else element.style.pointerEvents = previous.pointerEvents;
      if (previous.width == null) element.style.removeProperty("width");
      else element.style.width = previous.width;
      if (previous.height == null) element.style.removeProperty("height");
      else element.style.height = previous.height;
      if (previous.overflow == null) element.style.removeProperty("overflow");
      else element.style.overflow = previous.overflow;
    }
    state.hiddenReply2.clear();
  }

  function hideReply2FromWorker(pair, active) {
    const target = pair.reply2Card || pair.reply2;
    if (!target || state.hiddenReply2.has(target)) return false;
    state.hiddenReply2.set(target, {
      ariaHidden: target.getAttribute("aria-hidden"),
      visibility: target.style.visibility || null,
      position: target.style.position || null,
      pointerEvents: target.style.pointerEvents || null,
      width: target.style.width || null,
      height: target.style.height || null,
      overflow: target.style.overflow || null,
    });
    target.setAttribute("aria-hidden", "true");
    target.style.visibility = "hidden";
    target.style.position = "absolute";
    target.style.pointerEvents = "none";
    target.style.width = "0px";
    target.style.height = "0px";
    target.style.overflow = "hidden";
    state.lastDualReplyAt = Date.now();
    void diagnostic(active, {
      dual_reply_detected: true,
      dual_reply_policy: "reply1",
      dual_reply_feedback_clicked: false,
    });
    return true;
  }

  function enforce() {
    const active = activeRequest();
    if (!active) {
      if (state.hiddenReply2.size) restoreHiddenReplies();
      return;
    }
    dismissWorkModeOffer(active);
    const pair = dualReplyPair(active);
    if (pair) hideReply2FromWorker(pair, active);
  }

  let scheduled = false;
  function scheduleEnforce() {
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(() => {
      scheduled = false;
      enforce();
    });
  }

  state.observer = new MutationObserver(scheduleEnforce);
  state.observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ["aria-label", "aria-disabled", "class"] });
  setInterval(enforce, 250);
  enforce();
})();
