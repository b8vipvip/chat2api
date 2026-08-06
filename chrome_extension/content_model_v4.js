(() => {
  const ROUTER_VERSION = "0.3.3";
  const READY_TIMEOUT_MS = 30000;
  const MENU_TIMEOUT_MS = 7000;
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

  const FAMILY_ALIASES = {
    "gpt-5.6-sol": ["gpt-5.6 sol", "gpt 5.6 sol", "5.6 sol"],
    "gpt-5.5": ["gpt-5.5", "gpt 5.5", "5.5"],
    "gpt-5.3": ["gpt-5.3", "gpt 5.3", "5.3"],
    o3: ["o3"],
  };

  const REASONING_ALIASES = {
    auto: ["智能", "自动", "auto", "automatic"],
    instant: ["极速 5.5", "极速", "instant", "fast"],
    medium: ["中", "medium"],
    high: ["高", "high"],
    xhigh: ["极高", "extra high", "xhigh"],
    pro: ["pro"],
  };

  function visible(element) {
    if (!element || !(element instanceof Element)) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" &&
      style.visibility !== "hidden" && Number(style.opacity || 1) > 0;
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

  async function waitFor(predicate, timeout = 5000, interval = 100) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const value = predicate();
      if (value) return value;
      await delay(interval);
    }
    return null;
  }

  function requestedParts(modelId) {
    const id = normalize(modelId);
    if (!id || id === "chatgpt-web") return { family: "", reasoning: "" };
    for (const family of Object.keys(FAMILY_ALIASES).sort((a, b) => b.length - a.length)) {
      if (id === family) return { family, reasoning: "" };
      if (id.startsWith(`${family}-`)) return { family, reasoning: id.slice(family.length + 1) };
    }
    return { family: id, reasoning: "" };
  }

  function aliasMatch(label, aliases) {
    const value = normalize(label);
    let best = 0;
    for (const alias of aliases) {
      const needle = normalize(alias);
      if (!needle) continue;
      if (value === needle) best = Math.max(best, 120);
      else if (value.startsWith(`${needle} `) || value.endsWith(` ${needle}`)) best = Math.max(best, 95);
      else if (value.includes(needle)) best = Math.max(best, 70);
    }
    return best;
  }

  function canonicalFamily(label) {
    for (const [family, aliases] of Object.entries(FAMILY_ALIASES)) {
      if (aliasMatch(label, aliases)) return family;
    }
    return "";
  }

  function canonicalReasoning(label) {
    for (const [reasoning, aliases] of Object.entries(REASONING_ALIASES)) {
      if (aliasMatch(label, aliases)) return reasoning;
    }
    return "";
  }

  function knownLabel(label) {
    return Boolean(canonicalFamily(label) || canonicalReasoning(label));
  }

  function composerInput() {
    const selectors = [
      "#prompt-textarea",
      "form[data-type='unified-composer'] [contenteditable='true']",
      "form[data-type='unified-composer'] textarea",
      "textarea[data-id='root']",
      "[contenteditable='true'][data-placeholder]",
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
    if (visible(form)) return form;
    return null;
  }

  function isRejectedComposerButton(element) {
    const value = normalize(`${labelOf(element)} ${element.getAttribute("aria-label") || ""} ${element.getAttribute("data-testid") || ""}`);
    return /send|submit|voice|microphone|mic|audio|attach|upload|file|tool|添加|附件|上传|语音|麦克风|发送/.test(value);
  }

  function pickerButtonWithinComposer(root = composerRoot()) {
    if (!root) return null;
    const candidates = [];
    const seen = new Set();
    const selectors = [
      "button[data-testid*='model' i]",
      "button[class*='composer-pill'][aria-haspopup='menu']",
      "button[class*='composer-pill'][aria-haspopup='listbox']",
      "button[aria-label*='model' i]",
      "button[aria-label*='模型']",
      "button[aria-haspopup='menu']",
      "button[aria-haspopup='listbox']",
    ];

    for (const selector of selectors) {
      for (const element of root.querySelectorAll(selector)) {
        if (seen.has(element) || !visible(element) || element.disabled || isRejectedComposerButton(element)) continue;
        seen.add(element);
        const label = labelOf(element);
        const className = String(element.className || "");
        const testId = element.getAttribute("data-testid") || "";
        let score = 0;
        if (/composer-pill/i.test(className)) score += 220;
        if (/model/i.test(testId)) score += 210;
        if (knownLabel(label)) score += 180;
        if (/model|模型/i.test(element.getAttribute("aria-label") || "")) score += 160;
        if (element.getAttribute("aria-haspopup")) score += 80;
        if (element.querySelector("svg")) score += 15;
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
      const picker = pickerButtonWithinComposer(root);
      if (!root || !input || !picker) return null;
      return { root, input, picker };
    }, timeout, 150);

    if (!ready) {
      const root = composerRoot();
      const input = composerInput();
      throw new Error(
        `ChatGPT composer did not become ready within ${Math.round(timeout / 1000)} seconds ` +
        `(composer=${Boolean(root)}, input=${Boolean(input)}, model_picker=${Boolean(pickerButtonWithinComposer(root))})`,
      );
    }
    return ready;
  }

  function isInteractive(element) {
    if (!element) return false;
    if (element.matches("button, [role='menuitem'], [role='menuitemradio'], [role='option'], [data-radix-collection-item], a, [tabindex]")) return true;
    const style = getComputedStyle(element);
    return style.cursor === "pointer" || typeof element.onclick === "function";
  }

  function clickableAncestor(element, boundary = null) {
    let current = element;
    for (let depth = 0; current && depth < 8; depth += 1, current = current.parentElement) {
      if (isInteractive(current)) return current;
      if (boundary && current === boundary) break;
    }
    return element;
  }

  function menuSurfaces() {
    const selectors = [
      "[role='menu']",
      "[role='listbox']",
      "[data-radix-menu-content]",
      "[data-radix-popper-content-wrapper]",
      "[data-headlessui-state='open']",
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
        if (rect.width < 80 || rect.height < 35) continue;
        const text = labelOf(element);
        if (!knownLabel(text) && ![...element.querySelectorAll("*")].some(child => knownLabel(labelOf(child)))) continue;
        seen.add(element);
        result.push(element);
      }
    }
    return result;
  }

  function collectChoices() {
    const surfaces = menuSurfaces();
    if (!surfaces.length) return [];
    const result = [];
    const seen = new Set();
    const selector = "button, [role='menuitem'], [role='menuitemradio'], [role='option'], [data-radix-collection-item], [tabindex], div, span, p";

    for (const root of surfaces) {
      for (const raw of root.querySelectorAll(selector)) {
        if (!visible(raw)) continue;
        const rawLabel = labelOf(raw);
        if (!rawLabel || rawLabel.length > 100 || !knownLabel(rawLabel)) continue;
        const element = clickableAncestor(raw, root);
        if (!visible(element) || seen.has(element)) continue;
        const label = labelOf(element);
        if (!knownLabel(label) && !knownLabel(rawLabel)) continue;
        seen.add(element);
        result.push({ element, label, rawLabel, surface: root });
      }
    }
    return result;
  }

  function dispatchClick(element) {
    const pointerInit = { bubbles: true, cancelable: true, composed: true, view: window, pointerType: "mouse" };
    const mouseInit = { bubbles: true, cancelable: true, composed: true, view: window };
    if (typeof PointerEvent === "function") {
      element.dispatchEvent(new PointerEvent("pointerdown", pointerInit));
    }
    element.dispatchEvent(new MouseEvent("mousedown", mouseInit));
    if (typeof PointerEvent === "function") {
      element.dispatchEvent(new PointerEvent("pointerup", pointerInit));
    }
    element.dispatchEvent(new MouseEvent("mouseup", mouseInit));
    element.click();
  }

  async function openPicker() {
    const { picker } = await waitForComposerReady();
    const before = menuSurfaces();
    dispatchClick(picker);
    const choices = await waitFor(() => {
      const found = collectChoices().filter(item => item.element !== picker);
      if (!found.length) return null;
      const currentSurfaces = menuSurfaces();
      const openedNewSurface = currentSurfaces.some(surface => !before.includes(surface));
      const expanded = picker.getAttribute("aria-expanded") === "true" || picker.getAttribute("data-state") === "open";
      return openedNewSurface || expanded || found.length >= 2 ? found : null;
    }, MENU_TIMEOUT_MS, 100);
    if (!choices) {
      throw new Error(`ChatGPT model menu did not open from the composer model button (${labelOf(picker) || "unlabelled"})`);
    }
    return { picker, choices };
  }

  function closeMenus() {
    for (let index = 0; index < 3; index += 1) {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true }));
    }
  }

  function bestChoice(aliases, excluded = new Set()) {
    const scored = [];
    for (const item of collectChoices()) {
      if (excluded.has(item.element)) continue;
      const match = Math.max(aliasMatch(item.label, aliases), aliasMatch(item.rawLabel, aliases));
      if (!match) continue;
      const rect = item.element.getBoundingClientRect();
      const interactiveBonus = isInteractive(item.element) ? 35 : 0;
      const exactRawBonus = aliases.some(alias => normalize(item.rawLabel) === normalize(alias)) ? 35 : 0;
      const smallElementBonus = rect.width < 500 && rect.height < 120 ? 15 : 0;
      scored.push({ ...item, score: match + interactiveBonus + exactRawBonus + smallElementBonus });
    }
    scored.sort((a, b) => b.score - a.score);
    return scored[0] || null;
  }

  function visibleFamilies() {
    const map = new Map();
    for (const item of collectChoices()) {
      const family = canonicalFamily(item.rawLabel) || canonicalFamily(item.label);
      if (family && !map.has(family)) map.set(family, item);
    }
    return map;
  }

  async function clickChoice(item) {
    if (!item?.element) return false;
    item.element.scrollIntoView({ block: "nearest", inline: "nearest" });
    dispatchClick(item.element);
    await delay(400);
    return true;
  }

  function visibleChoiceSummary() {
    return [...new Set(collectChoices().flatMap(item => [item.rawLabel, item.label]).filter(Boolean))].slice(0, 20).join(" | ") || "none";
  }

  async function chooseFamily(family) {
    const aliases = FAMILY_ALIASES[family] || [family];
    const opened = await openPicker();
    let families = visibleFamilies();

    if (families.size >= 2 && families.has(family)) {
      await clickChoice(families.get(family));
      return;
    }

    const trigger = bestChoice(
      families.size === 1 ? FAMILY_ALIASES[[...families.keys()][0]] : aliases,
      new Set([opened.picker]),
    );
    if (!trigger) {
      const summary = visibleChoiceSummary();
      closeMenus();
      throw new Error(`Model family submenu trigger was not found. Visible choices: ${summary}`);
    }

    const triggerElement = trigger.element;
    await clickChoice(trigger);
    families = await waitFor(() => {
      const current = visibleFamilies();
      if (current.size >= 2 || (current.has(family) && current.get(family)?.element !== triggerElement)) return current;
      return null;
    }, MENU_TIMEOUT_MS, 100);

    if (!families || !families.has(family)) {
      const summary = visibleChoiceSummary();
      closeMenus();
      throw new Error(`Requested model family is not available in ChatGPT: ${family}. Visible choices: ${summary}`);
    }

    const target = families.get(family);
    if (target.element !== triggerElement) await clickChoice(target);
  }

  async function chooseReasoning(reasoning) {
    const aliases = REASONING_ALIASES[reasoning] || [reasoning];
    const opened = await openPicker();
    const choice = bestChoice(aliases, new Set([opened.picker]));
    if (!choice) {
      const summary = visibleChoiceSummary();
      closeMenus();
      throw new Error(`Requested reasoning level is not available in ChatGPT: ${reasoning}. Visible choices: ${summary}`);
    }
    await clickChoice(choice);
  }

  function modelRecord(family, reasoning, label, selected = false) {
    return {
      id: reasoning ? `${family}-${reasoning}` : family,
      label,
      family,
      reasoning: reasoning || null,
      selected,
      capabilities: ["text"],
    };
  }

  async function discoverCatalog(requestedModel) {
    const result = new Map();
    result.set("chatgpt-web", {
      id: "chatgpt-web",
      label: "ChatGPT current/default model",
      family: null,
      reasoning: null,
      selected: requestedModel === "chatgpt-web",
      capabilities: ["text"],
    });

    const requested = requestedParts(requestedModel);
    if (requested.family) {
      const record = modelRecord(requested.family, requested.reasoning, requestedModel, true);
      result.set(record.id, record);
    }

    try {
      const opened = await openPicker();
      let families = visibleFamilies();
      const reasonings = new Map();
      for (const item of collectChoices()) {
        const reasoning = canonicalReasoning(item.rawLabel) || canonicalReasoning(item.label);
        if (reasoning && !reasonings.has(reasoning)) reasonings.set(reasoning, item.rawLabel || item.label);
      }

      if (families.size === 1) {
        const currentFamily = [...families.keys()][0];
        const trigger = bestChoice(FAMILY_ALIASES[currentFamily], new Set([opened.picker]));
        if (trigger) {
          await clickChoice(trigger);
          families = await waitFor(() => {
            const current = visibleFamilies();
            return current.size >= 2 ? current : null;
          }, 3000, 100) || families;
        }
      }

      for (const [family, item] of families) {
        if (!result.has(family)) result.set(family, modelRecord(family, "", item.rawLabel || item.label, family === requested.family && !requested.reasoning));
      }
      if (requested.family) {
        for (const [reasoning, label] of reasonings) {
          const id = `${requested.family}-${reasoning}`;
          if (!result.has(id)) result.set(id, modelRecord(requested.family, reasoning, `${requested.family} / ${label}`, reasoning === requested.reasoning));
        }
      }
    } catch (_) {
    } finally {
      closeMenus();
    }

    return {
      models: [...result.values()],
      current_model: requestedModel || "chatgpt-web",
      router_version: ROUTER_VERSION,
    };
  }

  async function prepareModel(modelId) {
    await waitForComposerReady();
    const model = normalize(modelId) || "chatgpt-web";
    if (model === "chatgpt-web") return discoverCatalog(model);

    if (model === "gpt-5.5-instant") {
      await chooseReasoning("instant");
    } else {
      const { family, reasoning } = requestedParts(model);
      await chooseFamily(family);
      if (reasoning) await chooseReasoning(reasoning);
    }

    await delay(500);
    return discoverCatalog(model);
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type !== "chat2api.model.prepare.v4") return false;
    prepareModel(message.model)
      .then(data => sendResponse({ ok: true, data }))
      .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  });
})();
