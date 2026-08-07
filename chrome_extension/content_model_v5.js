(() => {
  const ROUTER_VERSION = "0.3.4";
  const READY_TIMEOUT_MS = 30000;
  const MENU_TIMEOUT_MS = 7000;
  const SHORTCUT_TIMEOUT_MS = 1200;
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

  const FAMILY_ALIASES = {
    "gpt-5.6-sol": ["gpt-5.6 sol", "gpt 5.6 sol", "5.6 sol"],
    "gpt-5.5": ["gpt-5.5", "gpt 5.5", "5.5"],
    "gpt-5.3": ["gpt-5.3", "gpt 5.3", "5.3"],
    o3: ["o3"],
  };

  const REASONING_ALIASES = {
    instant: ["极速", "instant", "fast"],
    medium: ["中", "medium"],
    high: ["高", "high"],
    auto: ["智能", "自动", "auto", "automatic"],
  };

  const REASONING_POSITIONS = {
    instant: 0.06,
    medium: 0.50,
    high: 0.94,
  };

  function visible(element) {
    if (!element || !(element instanceof Element)) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 &&
      style.display !== "none" && style.visibility !== "hidden" &&
      Number(style.opacity || 1) > 0;
  }

  function normalize(value) {
    return String(value || "")
      .replace(/[\u200B-\u200D\uFEFF]/g, "")
      .replace(/[✓✔︎✔√]/g, "")
      .replace(/\s+/g, " ")
      .trim()
      .toLowerCase();
  }

  function labelOf(element) {
    return String(
      element?.getAttribute?.("aria-label") ||
      element?.getAttribute?.("data-value") ||
      element?.innerText ||
      element?.textContent ||
      "",
    ).replace(/[\u200B-\u200D\uFEFF]/g, "").replace(/\s+/g, " ").trim();
  }

  function aliasMatch(label, aliases) {
    const value = normalize(label);
    let best = 0;
    for (const alias of aliases || []) {
      const needle = normalize(alias);
      if (!needle) continue;
      if (value === needle) best = Math.max(best, 120);
      else if (value.startsWith(`${needle} `) || value.endsWith(` ${needle}`)) best = Math.max(best, 95);
      else if (value.includes(needle)) best = Math.max(best, 70);
    }
    return best;
  }

  function requestedParts(modelId) {
    const id = normalize(modelId);
    if (!id || id === "default" || id === "chatgpt-web") {
      return { isDefault: true, family: "", reasoning: "" };
    }
    for (const family of Object.keys(FAMILY_ALIASES).sort((a, b) => b.length - a.length)) {
      if (id === family) return { isDefault: false, family, reasoning: "" };
      if (id.startsWith(`${family}-`)) {
        return { isDefault: false, family, reasoning: id.slice(family.length + 1) };
      }
    }
    return { isDefault: false, family: id, reasoning: "" };
  }

  async function waitFor(predicate, timeout = 5000, interval = 100) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      try {
        const value = predicate();
        if (value) return value;
      } catch (_) {}
      await delay(interval);
    }
    return null;
  }

  function composerInput() {
    const selectors = [
      "#prompt-textarea",
      "form[data-type='unified-composer'] [contenteditable='true']",
      "form[data-type='unified-composer'] textarea",
      "textarea[data-id='root']",
    ];
    for (const selector of selectors) {
      const element = document.querySelector(selector);
      if (visible(element)) return element;
    }
    return null;
  }

  function composerRoot() {
    const explicit = document.querySelector("form[data-type='unified-composer']");
    if (visible(explicit)) return explicit;
    const input = composerInput();
    const form = input?.closest?.("form");
    return visible(form) ? form : null;
  }

  function rejectedButton(element) {
    const value = normalize(
      `${labelOf(element)} ${element.getAttribute("aria-label") || ""} ${element.getAttribute("data-testid") || ""}`,
    );
    return /send|submit|voice|microphone|mic|audio|attach|upload|file|tool|添加|附件|上传|语音|麦克风|发送/.test(value);
  }

  function composerPill(root = composerRoot()) {
    if (!root) return null;
    const candidates = [];
    const seen = new Set();
    const selectors = [
      "button[class*='composer-pill'][aria-haspopup='menu']",
      "button[class*='composer-pill'][aria-haspopup='listbox']",
      "button[data-testid*='model' i]",
      "button[aria-label*='model' i]",
      "button[aria-label*='模型']",
      "button[aria-haspopup='menu']",
      "button[aria-haspopup='listbox']",
    ];

    for (const selector of selectors) {
      for (const element of root.querySelectorAll(selector)) {
        if (seen.has(element) || !visible(element) || element.disabled || rejectedButton(element)) continue;
        seen.add(element);
        const text = labelOf(element);
        const cls = String(element.className || "");
        const testId = element.getAttribute("data-testid") || "";
        let score = 0;
        if (/composer-pill/i.test(cls)) score += 250;
        if (/model/i.test(testId)) score += 200;
        if (Object.values(REASONING_ALIASES).some(a => aliasMatch(text, a))) score += 160;
        if (/model|模型/i.test(element.getAttribute("aria-label") || "")) score += 140;
        if (element.getAttribute("aria-haspopup")) score += 60;
        if (score > 0) candidates.push({ element, score });
      }
    }
    candidates.sort((a, b) => b.score - a.score);
    return candidates[0]?.element || null;
  }

  async function waitForComposerReady(timeout = READY_TIMEOUT_MS) {
    const ready = await waitFor(() => {
      if (document.readyState === "loading") return null;
      const root = composerRoot();
      const input = composerInput();
      const pill = composerPill(root);
      if (!root || !input || !pill) return null;
      return { root, input, pill };
    }, timeout, 150);

    if (!ready) {
      throw new Error(
        `ChatGPT composer did not become ready within ${Math.round(timeout / 1000)} seconds ` +
        `(composer=${Boolean(composerRoot())}, input=${Boolean(composerInput())}, model_picker=${Boolean(composerPill())})`,
      );
    }
    return ready;
  }

  function isInteractive(element) {
    if (!element) return false;
    return element.matches("button, [role='menuitem'], [role='menuitemradio'], [role='option'], [data-radix-collection-item], [tabindex], a") ||
      getComputedStyle(element).cursor === "pointer";
  }

  function clickableAncestor(element, boundary = null) {
    let current = element;
    for (let depth = 0; current && depth < 8; depth += 1, current = current.parentElement) {
      if (isInteractive(current)) return current;
      if (boundary && current === boundary) break;
    }
    return element;
  }

  function openSurfaces() {
    const selectors = [
      "[role='menu']",
      "[role='listbox']",
      "[data-radix-popper-content-wrapper]",
      "[data-radix-menu-content]",
      "[data-state='open']",
      "[class*='popover' i]",
      "[class*='menu' i]",
    ];
    const result = [];
    const seen = new Set();
    for (const selector of selectors) {
      for (const element of document.querySelectorAll(selector)) {
        if (seen.has(element) || !visible(element)) continue;
        const rect = element.getBoundingClientRect();
        if (rect.width < 80 || rect.height < 30) continue;
        seen.add(element);
        result.push(element);
      }
    }
    return result;
  }

  function surfaceText() {
    return openSurfaces().map(labelOf).filter(Boolean).join(" | ");
  }

  function findVisibleText(aliases, { exact = false } = {}) {
    const roots = openSurfaces();
    const searchRoots = roots.length ? roots : [document.body];
    const result = [];
    const seen = new Set();
    for (const root of searchRoots) {
      for (const raw of root.querySelectorAll("button, [role='menuitem'], [role='menuitemradio'], [role='option'], div, span, p")) {
        if (!visible(raw)) continue;
        const rawLabel = labelOf(raw);
        if (!rawLabel || rawLabel.length > 120) continue;
        let score = aliasMatch(rawLabel, aliases);
        if (exact && score < 120) continue;
        if (!score) continue;
        const element = clickableAncestor(raw, root);
        if (!visible(element) || seen.has(element)) continue;
        seen.add(element);
        const label = labelOf(element);
        score = Math.max(score, aliasMatch(label, aliases));
        const rect = element.getBoundingClientRect();
        if (isInteractive(element)) score += 20;
        if (root !== document.body) score += 20;
        score -= Math.min(15, Math.log10(Math.max(10, rect.width * rect.height)) * 2);
        result.push({ element, label, rawLabel, score });
      }
    }
    result.sort((a, b) => b.score - a.score);
    return result[0] || null;
  }

  async function clickElement(element) {
    if (!element) return false;
    element.scrollIntoView({ block: "nearest", inline: "nearest" });
    const rect = element.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    for (const type of ["pointerdown", "mousedown", "mouseup", "click"]) {
      element.dispatchEvent(new MouseEvent(type, {
        bubbles: true,
        cancelable: true,
        view: window,
        clientX: x,
        clientY: y,
      }));
    }
    await delay(250);
    return true;
  }

  function closeMenus() {
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true }));
    document.dispatchEvent(new KeyboardEvent("keyup", { key: "Escape", code: "Escape", bubbles: true }));
  }

  async function openComposerMenu() {
    const ready = await waitForComposerReady();
    await clickElement(ready.pill);
    const surface = await waitFor(() => openSurfaces()[0] || null, MENU_TIMEOUT_MS, 100);
    if (!surface) throw new Error("ChatGPT composer menu did not open");
    return { ...ready, surface };
  }

  async function chooseFamily(family) {
    const aliases = FAMILY_ALIASES[family] || [family];
    await openComposerMenu();

    let target = findVisibleText(aliases);
    if (target && aliasMatch(target.rawLabel, aliases) >= 95) {
      await clickElement(target.element);
      await delay(350);
      return;
    }

    const advanced = findVisibleText(["高级", "advanced"], { exact: false });
    if (advanced) {
      await clickElement(advanced.element);
      await delay(250);
    }

    const modelRow = await waitFor(
      () => findVisibleText(["模型", "model"], { exact: false }),
      MENU_TIMEOUT_MS,
      100,
    );
    if (modelRow) {
      await clickElement(modelRow.element);
      await delay(250);
    }

    target = await waitFor(() => {
      const found = findVisibleText(aliases);
      return found && aliasMatch(found.rawLabel, aliases) >= 70 ? found : null;
    }, MENU_TIMEOUT_MS, 100);

    if (!target) {
      closeMenus();
      throw new Error(
        `Requested model family is not available in ChatGPT: ${family}. ` +
        `Visible menu text: ${surfaceText() || "none"}`,
      );
    }

    await clickElement(target.element);
    await delay(350);

    try {
      await openComposerMenu();
      const advanced2 = findVisibleText(["高级", "advanced"]);
      if (advanced2) {
        await clickElement(advanced2.element);
        await delay(180);
      }
      const proof = findVisibleText(aliases);
      if (!proof) throw new Error("Selected family could not be verified");
    } finally {
      closeMenus();
    }
  }

  function dispatchReasoningShortcut() {
    const init = {
      key: "M",
      code: "KeyM",
      ctrlKey: true,
      shiftKey: true,
      bubbles: true,
      cancelable: true,
    };
    for (const target of [document.activeElement, document, window]) {
      if (!target?.dispatchEvent) continue;
      target.dispatchEvent(new KeyboardEvent("keydown", init));
      target.dispatchEvent(new KeyboardEvent("keyup", init));
    }
  }

  function visibleSlider() {
    const candidates = [
      ...document.querySelectorAll("[role='slider'], input[type='range']"),
    ].filter(visible);
    const surfaces = new Set(openSurfaces());
    const withinOpenSurface = candidates.filter(el => {
      let node = el;
      while (node) {
        if (surfaces.has(node)) return true;
        node = node.parentElement;
      }
      return false;
    });
    return withinOpenSurface[0] || candidates[0] || null;
  }

  function setNativeRangeValue(input, value) {
    const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
    descriptor?.set?.call(input, String(value));
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  async function setSliderPosition(slider, ratio) {
    ratio = Math.min(0.98, Math.max(0.02, ratio));
    if (slider instanceof HTMLInputElement && slider.type === "range") {
      const min = Number(slider.min || 0);
      const max = Number(slider.max || 100);
      const step = Number(slider.step || 1);
      let value = min + (max - min) * ratio;
      if (Number.isFinite(step) && step > 0) value = Math.round(value / step) * step;
      setNativeRangeValue(slider, value);
      await delay(250);
      return true;
    }

    const rect = slider.getBoundingClientRect();
    const x = rect.left + rect.width * ratio;
    const y = rect.top + rect.height / 2;
    for (const type of ["pointerdown", "mousedown", "mouseup", "click"]) {
      slider.dispatchEvent(new MouseEvent(type, {
        bubbles: true,
        cancelable: true,
        view: window,
        clientX: x,
        clientY: y,
      }));
    }
    await delay(300);
    return true;
  }

  async function openReasoningControl() {
    dispatchReasoningShortcut();
    let opened = await waitFor(() => visibleSlider() || openSurfaces()[0] || null, SHORTCUT_TIMEOUT_MS, 100);
    if (opened) return opened;

    const ready = await waitForComposerReady();
    await clickElement(ready.pill);
    opened = await waitFor(() => visibleSlider() || openSurfaces()[0] || null, MENU_TIMEOUT_MS, 100);
    if (!opened) throw new Error("ChatGPT reasoning control did not open");
    return opened;
  }

  async function chooseReasoning(reasoning) {
    if (!reasoning || reasoning === "auto") return;
    const aliases = REASONING_ALIASES[reasoning] || [reasoning];

    await openReasoningControl();

    const slider = visibleSlider();
    if (slider && REASONING_POSITIONS[reasoning] != null) {
      await setSliderPosition(slider, REASONING_POSITIONS[reasoning]);
      closeMenus();
      await delay(220);

      const pillLabel = normalize(labelOf(composerPill()));
      if (aliasMatch(pillLabel, aliases)) return;
    }

    let choice = findVisibleText(aliases);
    if (choice) {
      await clickElement(choice.element);
      closeMenus();
      return;
    }

    closeMenus();
    await openComposerMenu();
    const advanced = findVisibleText(["高级", "advanced"]);
    if (advanced) {
      await clickElement(advanced.element);
      await delay(220);
    }

    const reasoningRow = await waitFor(
      () => findVisibleText(["思考强度", "思考程度", "thinking", "reasoning"], { exact: false }),
      MENU_TIMEOUT_MS,
      100,
    );
    if (reasoningRow) {
      await clickElement(reasoningRow.element);
      await delay(220);
    }

    choice = await waitFor(() => findVisibleText(aliases), MENU_TIMEOUT_MS, 100);
    if (!choice) {
      closeMenus();
      throw new Error(
        `Requested reasoning level is not available in ChatGPT: ${reasoning}. ` +
        `Visible menu text: ${surfaceText() || "none"}`,
      );
    }
    await clickElement(choice.element);
    closeMenus();
  }

  function defaultRecord(selected = false) {
    return {
      id: "default",
      label: "ChatGPT default/current model",
      family: null,
      reasoning: null,
      selected,
      capabilities: ["text"],
    };
  }

  function requestedRecord(modelId, selected = true) {
    const parts = requestedParts(modelId);
    return {
      id: modelId,
      label: modelId,
      family: parts.family || null,
      reasoning: parts.reasoning || null,
      selected,
      capabilities: ["text"],
    };
  }

  async function prepareModel(modelId) {
    const raw = String(modelId || "default").trim() || "default";
    const parts = requestedParts(raw);
    await waitForComposerReady();

    if (parts.isDefault) {
      return {
        models: [defaultRecord(true), {
          id: "chatgpt-web",
          label: "Backward-compatible alias for default",
          family: null,
          reasoning: null,
          selected: true,
          capabilities: ["text"],
        }],
        current_model: "default",
        router_version: ROUTER_VERSION,
        selection_strategy: "default-no-ui",
      };
    }

    await chooseFamily(parts.family);
    if (parts.reasoning) await chooseReasoning(parts.reasoning);

    return {
      models: [defaultRecord(false), requestedRecord(raw, true)],
      current_model: raw,
      router_version: ROUTER_VERSION,
      selection_strategy: parts.reasoning ? "family+reasoning-hybrid" : "family-only",
    };
  }

  async function discoverCatalog() {
    await waitForComposerReady();
    const result = new Map();
    result.set("default", defaultRecord(false));
    result.set("chatgpt-web", {
      id: "chatgpt-web",
      label: "Backward-compatible alias for default",
      family: null,
      reasoning: null,
      selected: false,
      capabilities: ["text"],
    });

    try {
      await openComposerMenu();
      const advanced = findVisibleText(["高级", "advanced"]);
      if (advanced) {
        await clickElement(advanced.element);
        await delay(220);
      }
      const modelRow = findVisibleText(["模型", "model"]);
      if (modelRow) {
        await clickElement(modelRow.element);
        await delay(220);
      }

      for (const [family, aliases] of Object.entries(FAMILY_ALIASES)) {
        const item = findVisibleText(aliases);
        if (item) result.set(family, requestedRecord(family, false));
      }
    } catch (_) {
    } finally {
      closeMenus();
    }

    return {
      models: [...result.values()],
      current_model: "default",
      router_version: ROUTER_VERSION,
      selection_strategy: "manual-discovery",
    };
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type === "chat2api.model.prepare.v5") {
      prepareModel(message.model)
        .then(data => sendResponse({ ok: true, data }))
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }
    if (message.type === "chat2api.models.discover.v5") {
      discoverCatalog()
        .then(data => sendResponse({ ok: true, data }))
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }
    return false;
  });
})();