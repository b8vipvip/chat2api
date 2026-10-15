(() => {
  const KEY = "__CHAT2API_PAGE_ADAPTER_V22__";
  if (globalThis[KEY]) return;

  const VERSION = "22.2.0";
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
      "[role='dialog']",
      "[class*='popover' i]",
    ],
    reasoningSlider: ["input[type='range']", "[role='slider']"],
  });

  function normalize(value) {
    return String(value || "")
      .replace(/[\u200B-\u200D\uFEFF]/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function normalizeLabel(value) {
    return normalize(value).replace(/[✓✔︎✔√]/g, "").trim();
  }

  function normalizedLower(value) {
    return normalize(value).toLowerCase();
  }

  function normalizedLabelLower(value) {
    return normalizeLabel(value).toLowerCase();
  }

  function visible(element) {
    if (!element || !element.getBoundingClientRect) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function labelOf(element) {
    return normalizeLabel(
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
    const label = normalizedLabelLower(`${button.getAttribute("aria-label") || ""} ${button.innerText || button.textContent || ""}`);
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
    const text = normalizedLabelLower(value);
    // A single concrete model mention is usable evidence; popup wrappers
    // listing several models are not selected-model evidence.
    const pattern = /\bgpt[-\s]?(\d+(?:\.\d+)?)(?:[-\s]+(astra|pro|sol|terra|luna|mini))?\b/g;
    const ids = [...new Set([...text.matchAll(pattern)].map(match =>
      "gpt-" + match[1] + (match[2] ? "-" + match[2] : "")))];
    return ids.length === 1 ? ids[0] : "";
  }

  function reasoningFromText(value) {
    const text = normalizedLabelLower(value);
    for (const [level, aliases] of Object.entries(REASONING_ALIASES)) {
      if (aliases.some(alias => {
        const needle = normalizedLabelLower(alias);
        return text === needle || text.startsWith(`${needle} `) || text.endsWith(` ${needle}`);
      })) return level;
    }
    return "";
  }

  function modelReasoningControls() {
    const root = composerRoot();
    // The current ChatGPT model and reasoning affordances can live in the
    // header, not inside the composer form. Read both surfaces here so
    // the model/effort controllers share one DOM evidence boundary.
    const roots = [root, ...document.querySelectorAll("header")].filter(Boolean);
    const controls = [...new Set(roots.flatMap(scope =>
      [...scope.querySelectorAll("button,[role='button'],[aria-label],[data-value]")]))];
    return controls
      .filter(visible)
      .map(element => {
        const label = labelOf(element);
        return { element, label, family: familyFromText(label), reasoning: reasoningFromText(label) };
      })
      .filter(item => item.family || item.reasoning);
  }

  function modelReasoningControl() {
    const candidates = modelReasoningControls();
    return candidates.find(item => item.family && item.reasoning) ||
      candidates.find(item => item.family) ||
      candidates.find(item => item.reasoning) ||
      { element: null, label: "", family: "", reasoning: "" };
  }

  function modelControl() {
    const candidates = modelReasoningControls();
    return candidates.find(item => item.family && item.reasoning) ||
      candidates.find(item => item.family) ||
      { element: null, label: "", family: "", reasoning: "" };
  }

  function reasoningControl() {
    const candidates = modelReasoningControls();
    return candidates.find(item => item.family && item.reasoning) ||
      candidates.find(item => item.reasoning) ||
      { element: null, label: "", family: "", reasoning: "" };
  }

  function selectedReasoningRows(surfaces = openSurfaces()) {
    // ChatGPT can put aria-checked/data-state on a small icon or a
    // menuitemradio's nested child. The selected *option row*, not that
    // icon's empty aria-label, owns the visible effort label. Read the
    // nearest interactive ancestor; never promote a whole popover wrapper.
    const selectedSelector = [
      "[aria-checked='true']", "[aria-selected='true']",
      "[data-state='checked']", "[data-state='selected']",
      "[data-selected='true']",
    ].join(",");
    const optionSelector = "button,[role='menuitem'],[role='menuitemradio'],[role='option'],[data-radix-collection-item]";
    const byElement = new Set();
    const selected = [];
    for (const surface of surfaces) {
      for (const marker of surface.querySelectorAll(selectedSelector)) {
        if (!visible(marker)) continue;
        const ownLabel = normalize(marker.innerText || marker.textContent || "");
        const isolatedSelectedLabel = ownLabel.length <= 30 && ["instant", "medium", "high"].includes(reasoningFromText(ownLabel));
        // Some variants mark the effort text node itself, without a
        // menuitem role. This is valid only for its own short isolated label.
        const row = marker.closest?.(optionSelector) ||
          (marker.matches?.(optionSelector) || isolatedSelectedLabel ? marker : null);
        if (!row || !visible(row) || byElement.has(row)) continue;
        byElement.add(row);
        // Prefer the visible row text over a generic aria-label like
        // "selected option". Do not treat a sibling's option text as proof.
        const labels = [row.innerText, row.textContent, labelOf(row)].filter(Boolean);
        const levels = [...new Set(labels.map(reasoningFromText).filter(level =>
          ["instant", "medium", "high"].includes(level)))];
        if (levels.length !== 1) continue;
        selected.push({reasoning:levels[0], source:"selected-reasoning-option", label:normalize(labels[0] || "")});
      }
    }
    return selected;
  }

  function reasoningInspection() {
    // Only structural metadata and normalized effort levels; never persist
    // page text, account names, or the request prompt in diagnostics.
    const surfaces = openSurfaces();
    const selected = selectedReasoningRows(surfaces);
    const markerSelector = "[aria-checked='true'],[aria-selected='true'],[data-state='checked'],[data-state='selected'],[data-selected='true']";
    return {
      adapter_version: VERSION,
      open_surface_count: surfaces.length,
      selected_marker_count: surfaces.reduce((sum, surface) => sum + surface.querySelectorAll(markerSelector).length, 0),
      selected_reasoning_levels: [...new Set(selected.map(row => row.reasoning))],
      slider_present: Boolean(reasoningSlider()),
      reasoning_control_present: Boolean(reasoningControl().element),
      // Diagnostic-only structural evidence. No page text, prompts, account
      // labels or selectors are promoted to verification authority.
      composer_present: Boolean(composer()),
      document_ready_state: String(document.readyState || "unknown"),
      model_picker_present: Boolean(globalThis.__CHAT2API_MODEL_LIBRARY_V145__?.pickerControl?.()),
      visible_popup_trigger_count: [...document.querySelectorAll(
        "button[aria-haspopup='menu'],button[aria-haspopup='listbox'],[role='button'][aria-haspopup]"
      )].filter(visible).length,
      visible_reasoning_option_count: surfaces.reduce((sum, surface) =>
        sum + [...surface.querySelectorAll(
          "button,[role='menuitem'],[role='menuitemradio'],[role='option']"
        )].filter(row => visible(row) && ["instant","medium","high"].includes(
          reasoningFromText(labelOf(row))
        )).length, 0),
    };
  }

  function reasoningEvidence() {
    // Only selected, visible UI evidence may certify effort. Never infer
    // success from a requested value, an unchecked option, or stale cache.
    const surfaces = openSurfaces();
    const selected = selectedReasoningRows(surfaces);
    const unique = [...new Set(selected.map(row=>row.reasoning))];
    if (unique.length === 1) return selected.find(row=>row.reasoning===unique[0]);
    if (unique.length > 1) return {reasoning:"", source:"ambiguous-reasoning-selection"};

    const slider = reasoningSlider();
    if (slider && visible(slider)) {
      const label = slider.getAttribute?.("aria-valuetext") || slider.getAttribute?.("data-value") || "";
      const level = reasoningFromText(label);
      if (["instant", "medium", "high"].includes(level))
        return {reasoning:level, source:"reasoning-slider-value", label};
    }
    const candidates = modelReasoningControls().filter(item => ["instant", "medium", "high"].includes(item.reasoning));
    const levels = [...new Set(candidates.map(item=>item.reasoning))];
    if (levels.length !== 1) return {reasoning:"", source:levels.length ? "ambiguous-dom" : "none"};
    const item = candidates.find(row=>row.reasoning===levels[0]);
    return {reasoning:item.reasoning, source:"composer-dom", label:item.label};
  }

  function modelFamilyEvidence() {
    const root = composerRoot() || document;
    const headerRoots = [...document.querySelectorAll("header")];
    const attributeNames = ["data-model", "data-model-id", "data-value", "aria-label", "title"];
    const selectedSelectors = [
      "[aria-checked='true']",
      "[aria-selected='true']",
      "[data-state='checked']",
      "[data-state='selected']",
      "[data-selected='true']",
      "button[class*='composer-pill']",
      "button[data-testid*='model' i]",
    ];
    const seen = new Set();
    const rows = [];
    for (const selector of selectedSelectors) {
      for (const element of [root, ...headerRoots].flatMap(scope => [...scope.querySelectorAll(selector)])) {
        if (seen.has(element) || !visible(element)) continue;
        seen.add(element);
        const values = [labelOf(element), ...attributeNames.map(name => element.getAttribute?.(name) || "")];
        const family = values.map(familyFromText).find(Boolean) || "";
        if (family) rows.push({ family, source: "composer-dom", label: values.find(value => familyFromText(value) === family) || "" });
      }
    }
    // ChatGPT often renders a combined composer pill such as "5.5 高" without
    // model-specific attributes. Visible composer controls are passive evidence.
    for (const item of modelReasoningControls()) {
      if (item.family) rows.push({ family: item.family, source: "composer-dom", label: item.label });
    }
    const unique = [...new Set(rows.map(item => item.family))];
    if (unique.length === 1) return rows.find(item => item.family === unique[0]);
    return { family: "", source: unique.length > 1 ? "ambiguous-dom" : "none" };
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
    normalizeLabel,
    normalizedLower,
    normalizedLabelLower,
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
    modelReasoningControls,
    modelReasoningControl,
    modelControl,
    reasoningControl,
    reasoningEvidence,
    reasoningInspection,
    modelFamilyEvidence,
    openSurfaces,
    reasoningSlider,
  });

  globalThis[KEY] = api;
})();
