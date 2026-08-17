(() => {
  const KEY = "__CHAT2API_PAGE_ADAPTER_V22__";
  if (globalThis[KEY]) return;

  const VERSION = "22.0.0";
  const FAMILY_ALIASES = {
    "gpt-5.6-sol": ["gpt-5.6 sol", "gpt 5.6 sol", "5.6 sol"],
    "gpt-5.5": ["gpt-5.5", "gpt 5.5", "5.5"],
  };
  const REASONING_ALIASES = {
    instant: ["极速", "instant", "fast", "minimal", "low"],
    medium: ["中", "中等", "medium"],
    high: ["高", "high", "xhigh"],
    auto: ["智能", "自动", "auto", "automatic"],
  };

  const SELECTORS = Object.freeze({
    composerRoot: ["form[data-type='unified-composer']", "form"],
    composer: [
      "#prompt-textarea",
      "textarea[placeholder]",
      "div[contenteditable='true'][data-lexical-editor='true']",
      "div[contenteditable='true'].ProseMirror",
      "form div[contenteditable='true']",
      "[contenteditable='true']",
    ],
    sendButton: [
      "button[data-testid='send-button']",
      "button[aria-label='Send prompt']",
      "button[aria-label*='发送提示']",
      "button[aria-label*='发送消息']",
      "button[type='submit']",
    ],
    stopButton: [
      "button[data-testid='stop-button']",
      "button[aria-label='Stop streaming']",
      "button[aria-label='Stop generating']",
      "button[aria-label*='停止回答']",
      "button[aria-label*='停止生成']",
    ],
    assistant: [
      "[data-message-author-role='assistant']",
      "article[data-testid^='conversation-turn'] [data-message-author-role='assistant']",
    ],
    openSurface: [
      "[role='menu']",
      "[role='listbox']",
      "[data-radix-popper-content-wrapper]",
      "[data-radix-menu-content]",
      "[data-state='open']",
    ],
    reasoningSlider: ["input[type='range']", "[role='slider']"],
  });

  function normalize(value) {
    return String(value || "")
      .replace(/[✓✔︎✔√]/g, "")
      .replace(/[\u200B-\u200D\uFEFF]/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function normalizedLower(value) {
    return normalize(value).toLowerCase();
  }

  function visible(element) {
    if (!element || !element.getBoundingClientRect) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function labelOf(element) {
    return normalize(
      element?.getAttribute?.("aria-label") ||
      element?.getAttribute?.("data-value") ||
      element?.getAttribute?.("title") ||
      element?.innerText ||
      element?.textContent ||
      "",
    );
  }

  function firstVisible(root, selectors, predicate = null) {
    const scope = root || document;
    for (const selector of selectors) {
      const found = [...scope.querySelectorAll(selector)].find(element => visible(element) && (!predicate || predicate(element)));
      if (found) return found;
    }
    return null;
  }

  function composerRoot() {
    for (const selector of SELECTORS.composerRoot) {
      const found = [...document.querySelectorAll(selector)].find(form =>
        visible(form) && SELECTORS.composer.some(composerSelector => form.querySelector(composerSelector)),
      );
      if (found) return found;
    }
    return null;
  }

  function composer() {
    return firstVisible(composerRoot() || document, SELECTORS.composer);
  }

  function composerText(element = composer()) {
    if (!element) return "";
    if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) return normalize(element.value || "");
    return normalize(element.innerText || element.textContent || "");
  }

  function sendButton() {
    return firstVisible(composerRoot() || document, SELECTORS.sendButton);
  }

  function buttonReady(button) {
    return Boolean(button && visible(button) && !button.disabled && button.getAttribute("aria-disabled") !== "true");
  }

  function stopButton() {
    return firstVisible(document, SELECTORS.stopButton, button => !button.disabled && button.getAttribute("aria-disabled") !== "true");
  }

  function isGenerating() {
    return Boolean(stopButton());
  }

  function isSendTarget(target) {
    if (!(target instanceof Element)) return false;
    const button = target.closest("button");
    if (!button || !visible(button)) return false;
    if (button.matches("button[data-testid='send-button'],button[type='submit']")) return true;
    const label = normalizedLower(`${button.getAttribute("aria-label") || ""} ${button.innerText || button.textContent || ""}`);
    return /send prompt|send message|发送提示|发送消息|发送$/.test(label);
  }

  function dispatchEnter(element) {
    if (!element) return false;
    element.focus?.();
    for (const type of ["keydown", "keypress", "keyup"]) {
      element.dispatchEvent(new KeyboardEvent(type, {
        key: "Enter",
        code: "Enter",
        keyCode: 13,
        which: 13,
        bubbles: true,
        cancelable: true,
      }));
    }
    return true;
  }

  function assistantNodes() {
    const result = [];
    const seen = new Set();
    for (const selector of SELECTORS.assistant) {
      for (const node of document.querySelectorAll(selector)) {
        if (!seen.has(node) && visible(node)) {
          seen.add(node);
          result.push(node);
        }
      }
    }
    return result;
  }

  function assistantIdentity(node) {
    const turn = node?.closest?.("[data-message-id], article[id], article[data-testid]");
    return node?.getAttribute?.("data-message-id") || turn?.getAttribute?.("data-message-id") || turn?.id || turn?.getAttribute?.("data-testid") || "";
  }

  function assistantText(node) {
    if (!node) return "";
    for (const selector of ["[data-message-content]", ".markdown", "[class*='markdown']"]) {
      const candidates = [...node.querySelectorAll(selector)].filter(visible);
      for (let index = candidates.length - 1; index >= 0; index -= 1) {
        const text = normalize(candidates[index].innerText || candidates[index].textContent || "");
        if (text) return text;
      }
    }
    const clone = node.cloneNode(true);
    clone.querySelectorAll("button,svg,nav,footer,[aria-hidden='true']").forEach(item => item.remove());
    return normalize(clone.innerText || clone.textContent || "");
  }

  function familyFromText(value) {
    const text = normalizedLower(value);
    for (const [family, aliases] of Object.entries(FAMILY_ALIASES)) {
      if (aliases.some(alias => text === normalizedLower(alias) || text.includes(normalizedLower(alias)))) return family;
    }
    return "";
  }

  function reasoningFromText(value) {
    const text = normalizedLower(value);
    for (const [level, aliases] of Object.entries(REASONING_ALIASES)) {
      if (aliases.some(alias => {
        const needle = normalizedLower(alias);
        return text === needle || text.startsWith(`${needle} `) || text.endsWith(` ${needle}`);
      })) return level;
    }
    return "";
  }

  function modelReasoningControl() {
    const root = composerRoot();
    if (!root) return { element: null, label: "", family: "", reasoning: "" };
    const candidates = [...root.querySelectorAll("button,[role='button'],[aria-label],[data-value]")]
      .filter(visible)
      .map(element => {
        const label = labelOf(element);
        return { element, label, family: familyFromText(label), reasoning: reasoningFromText(label) };
      })
      .filter(item => item.family || item.reasoning);
    return candidates.find(item => item.family && item.reasoning) ||
      candidates.find(item => item.family) ||
      candidates.find(item => item.reasoning) ||
      { element: null, label: "", family: "", reasoning: "" };
  }

  function openSurfaces() {
    const result = [];
    const seen = new Set();
    for (const selector of SELECTORS.openSurface) {
      for (const element of document.querySelectorAll(selector)) {
        if (seen.has(element) || !visible(element)) continue;
        const rect = element.getBoundingClientRect();
        if (rect.width < 60 || rect.height < 30) continue;
        seen.add(element);
        result.push(element);
      }
    }
    return result;
  }

  function reasoningSlider() {
    const sliders = [];
    for (const selector of SELECTORS.reasoningSlider) {
      for (const slider of document.querySelectorAll(selector)) {
        if (visible(slider) && !sliders.includes(slider)) sliders.push(slider);
      }
    }
    const surfaces = openSurfaces();
    return sliders.find(slider => surfaces.some(surface => surface.contains(slider))) || sliders[0] || null;
  }

  const api = Object.freeze({
    version: VERSION,
    selectors: SELECTORS,
    normalize,
    normalizedLower,
    visible,
    labelOf,
    firstVisible,
    composerRoot,
    composer,
    composerText,
    sendButton,
    buttonReady,
    stopButton,
    isGenerating,
    isSendTarget,
    dispatchEnter,
    assistantNodes,
    assistantIdentity,
    assistantText,
    familyFromText,
    reasoningFromText,
    modelReasoningControl,
    openSurfaces,
    reasoningSlider,
  });

  globalThis[KEY] = api;
})();
