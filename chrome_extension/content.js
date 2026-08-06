(() => {
  const VERSION = "0.1.2";
  const STATE_KEY = "__CHAT2API_CONTENT__";
  const previous = globalThis[STATE_KEY];
  if (previous?.version === VERSION && !previous?.stopped) return;
  try { previous?.stop?.(); } catch (_) {}

  const state = {
    version: VERSION,
    active: null,
    stopped: false,
    listener: null,
  };
  globalThis[STATE_KEY] = state;

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

  function visible(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
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

  function findButton(selectors, textPattern) {
    for (const selector of selectors) {
      const element = [...document.querySelectorAll(selector)].find(button => visible(button) && !button.disabled);
      if (element) return element;
    }
    if (textPattern) {
      return [...document.querySelectorAll("button")].find(button => {
        const label = `${button.getAttribute("aria-label") || ""} ${button.textContent || ""}`;
        return visible(button) && !button.disabled && textPattern.test(label);
      }) || null;
    }
    return null;
  }

  function stopButton() {
    return findButton(
      [
        "button[data-testid='stop-button']",
        "button[aria-label='Stop streaming']",
        "button[aria-label='Stop generating']",
        "button[aria-label*='停止生成']",
      ],
      /stop streaming|stop generating|停止生成|停止回答/i,
    );
  }

  function isGenerating() {
    return Boolean(stopButton());
  }

  async function waitFor(predicate, timeout = 10000, interval = 100) {
    const started = Date.now();
    while (Date.now() - started < timeout) {
      const value = predicate();
      if (value) return value;
      await delay(interval);
    }
    return null;
  }

  async function ensureTextMode() {
    let composer = findComposer();
    if (composer) return composer;
    const endVoice = findButton(
      [
        "button[aria-label*='End voice']",
        "button[aria-label*='end voice']",
        "button[aria-label*='结束语音']",
        "button[data-testid*='voice'][aria-label*='End']",
      ],
      /end voice|exit voice|close voice|结束语音|退出语音|关闭语音/i,
    );
    if (endVoice) {
      endVoice.click();
      await delay(350);
    }
    const closeVoice = findButton(
      [
        "button[aria-label*='Close voice']",
        "button[aria-label*='close voice']",
        "button[aria-label*='关闭语音']",
      ],
      /close voice|关闭语音/i,
    );
    if (closeVoice && !findComposer()) closeVoice.click();
    composer = await waitFor(findComposer, 12000, 150);
    if (!composer) throw new Error("Unable to switch ChatGPT to text mode. Exit Voice manually and retry.");
    return composer;
  }

  function setComposerText(element, text) {
    element.focus();
    if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) {
      const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
      setter?.call(element, text);
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
      const paragraph = document.createElement("p");
      paragraph.textContent = text;
      element.replaceChildren(paragraph);
    }
    element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
  }

  function sendButton() {
    return findButton(
      [
        "button[data-testid='send-button']",
        "button[aria-label='Send prompt']",
        "button[aria-label*='发送']",
        "form button[type='submit']",
      ],
      /send prompt|发送提示|发送消息/i,
    );
  }

  function assistantNodes() {
    const selectors = [
      "[data-message-author-role='assistant']",
      "article[data-testid^='conversation-turn'] [data-message-author-role='assistant']",
    ];
    const result = [];
    const seen = new Set();
    for (const selector of selectors) {
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

  function isTransientLine(value) {
    const text = normalizeText(value)
      .replace(/[.。…·:：]+$/g, "")
      .trim()
      .toLowerCase();
    if (!text) return true;
    return (
      /^(正在)?(思考|分析|推理|生成|处理|搜索|浏览)(中)?$/.test(text) ||
      /^(thinking|analyzing|reasoning|generating|working|searching|browsing)( now)?$/.test(text) ||
      /^(working on it|just a moment|one moment|please wait)$/.test(text) ||
      /^(thought|思考了?)\s*(for\s*)?\d+(\.\d+)?\s*(seconds?|秒)$/.test(text)
    );
  }

  function cleanAssistantText(value) {
    const normalized = normalizeText(value);
    if (!normalized) return "";
    const lines = normalized.split("\n").map(line => line.trim());
    while (lines.length > 1 && isTransientLine(lines[0])) lines.shift();
    while (lines.length > 1 && /^(copy|复制|good response|bad response|重新生成|regenerate)$/i.test(lines[lines.length - 1])) {
      lines.pop();
    }
    return normalizeText(lines.join("\n"));
  }

  function isTransientText(value) {
    const text = normalizeText(value);
    if (!text) return true;
    const lines = text.split("\n").map(line => line.trim()).filter(Boolean);
    return Boolean(lines.length) && lines.every(isTransientLine);
  }

  function nodeText(node) {
    if (!node) return "";
    const preferredSelectors = [
      "[data-message-content]",
      ".markdown",
      "[class*='markdown']",
    ];
    for (const selector of preferredSelectors) {
      const candidates = [...node.querySelectorAll(selector)].filter(visible);
      for (let index = candidates.length - 1; index >= 0; index -= 1) {
        const text = cleanAssistantText(candidates[index].innerText || candidates[index].textContent || "");
        if (text && !isTransientText(text)) return text;
      }
    }

    const clone = node.cloneNode(true);
    clone.querySelectorAll([
      "button",
      "svg",
      "nav",
      "footer",
      "[aria-hidden='true']",
      "[data-testid*='copy']",
      "[data-testid*='feedback']",
      "[data-testid*='action']",
    ].join(",")).forEach(element => element.remove());
    const fallback = cleanAssistantText(clone.innerText || clone.textContent || "");
    return isTransientText(fallback) ? "" : fallback;
  }

  function rawNodeText(node) {
    return cleanAssistantText(node?.innerText || node?.textContent || "");
  }

  function nodeIdentity(node) {
    const turn = node?.closest("[data-message-id], article[id], article[data-testid]");
    return node?.getAttribute("data-message-id") || turn?.getAttribute("data-message-id") || turn?.id || turn?.getAttribute("data-testid") || "";
  }

  async function emit(event) {
    try {
      await chrome.runtime.sendMessage({ type: "chat2api.event", event });
    } catch (error) {
      console.warn("chat2api event failed", error);
    }
  }

  async function updateCapturedText(active, text, lastText) {
    if (!text || text === lastText) return lastText;
    if (text.startsWith(lastText)) {
      const delta = text.slice(lastText.length);
      if (delta) await emit({ type: "chat.delta", request_id: active.requestId, delta });
    } else {
      await emit({ type: "chat.snapshot", request_id: active.requestId, text });
    }
    return text;
  }

  async function monitor(active) {
    const timeoutMs = Math.max(5000, Number(active.options.timeout_seconds || 300) * 1000);
    const startedAt = Date.now();
    const completionQuietMs = 1800;
    const finalVerificationMs = 500;
    let responseStarted = false;
    let lastText = "";
    let lastIdentity = "";
    let stableSince = 0;
    let substantiveSince = 0;

    while (!active.cancelled && Date.now() - startedAt < timeoutMs) {
      const nodes = assistantNodes();
      const latest = nodes[nodes.length - 1];
      const identity = nodeIdentity(latest);
      const isNewNode = nodes.length > active.baselineCount || (latest && identity && identity !== active.baselineIdentity);
      const rawText = isNewNode ? rawNodeText(latest) : "";
      const transient = Boolean(rawText && isTransientText(rawText));
      const text = isNewNode ? nodeText(latest) : "";
      const generating = isGenerating();

      if (isNewNode || transient || generating) {
        if (!responseStarted) {
          responseStarted = true;
          await emit({ type: "chat.started", request_id: active.requestId });
        }
      }

      if (text) {
        const previousText = lastText;
        lastText = await updateCapturedText(active, text, lastText);
        if (lastText !== previousText || identity !== lastIdentity) {
          stableSince = Date.now();
          if (!substantiveSince) substantiveSince = stableSince;
          lastIdentity = identity;
        }
      }

      if (responseStarted && lastText && !generating && !transient) {
        const quietFor = stableSince ? Date.now() - stableSince : 0;
        const substantiveFor = substantiveSince ? Date.now() - substantiveSince : 0;
        if (quietFor >= completionQuietMs && substantiveFor >= completionQuietMs) {
          await delay(finalVerificationMs);
          const finalNodes = assistantNodes();
          const finalNode = finalNodes[finalNodes.length - 1];
          const finalRaw = rawNodeText(finalNode);
          const finalText = nodeText(finalNode);
          if (!isGenerating() && finalText && !isTransientText(finalRaw)) {
            lastText = await updateCapturedText(active, finalText, lastText);
            await emit({ type: "chat.completed", request_id: active.requestId, text: lastText });
            return;
          }
          stableSince = Date.now();
        }
      }
      await delay(120);
    }

    if (active.cancelled) {
      await emit({ type: "chat.cancelled", request_id: active.requestId, reason: "Cancelled by API client" });
    } else {
      await emit({ type: "chat.error", request_id: active.requestId, error: "Timed out waiting for ChatGPT response" });
    }
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
      await delay(250);
      const button = await waitFor(sendButton, 3000, 100);
      if (button) button.click();
      else {
        composer.dispatchEvent(new KeyboardEvent("keydown", {
          key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true,
        }));
      }
      await monitor(active);
    } finally {
      if (state.active === active) state.active = null;
    }
  }

  async function cancelRequest(requestId) {
    if (!state.active || state.active.requestId !== requestId) return;
    state.active.cancelled = true;
    stopButton()?.click();
  }

  const listener = (message, _sender, sendResponse) => {
    if (message.type === "chat2api.ping") {
      sendResponse({ ok: true, version: VERSION });
      return false;
    }
    if (message.type === "chat2api.request") {
      runRequest(message).catch(error => emit({
        type: "chat.error",
        request_id: message.requestId,
        error: String(error?.message || error),
      }));
      sendResponse({ ok: true });
      return false;
    }
    if (message.type === "chat2api.cancel") {
      cancelRequest(message.requestId).then(() => sendResponse({ ok: true }));
      return true;
    }
    return false;
  };

  state.listener = listener;
  state.stop = () => {
    state.stopped = true;
    try { chrome.runtime.onMessage.removeListener(listener); } catch (_) {}
  };
  chrome.runtime.onMessage.addListener(listener);
})();
