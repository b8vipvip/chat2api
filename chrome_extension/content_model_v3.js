(() => {
  const ROUTER_VERSION = "0.3.2";
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
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) > 0;
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

  function isInteractive(element) {
    if (!element) return false;
    if (element.matches("button, [role='menuitem'], [role='menuitemradio'], [role='option'], [data-radix-collection-item], a, [tabindex]")) return true;
    const style = getComputedStyle(element);
    return style.cursor === "pointer" || typeof element.onclick === "function";
  }

  function clickableAncestor(element, boundary = null) {
    let current = element;
    for (let depth = 0; current && depth < 7; depth += 1, current = current.parentElement) {
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
        if (rect.width < 80 || rect.height < 40) continue;
        seen.add(element);
        result.push(element);
      }
    }
    return result;
  }

  function knownLabel(label) {
    return Boolean(canonicalFamily(label) || canonicalReasoning(label));
  }

  function collectChoices() {
    const surfaces = menuSurfaces();
    const roots = surfaces.length ? surfaces : [document.body];
    const result = [];
    const seen = new Set();
    const selector = "button, [role='menuitem'], [role='menuitemradio'], [role='option'], [data-radix-collection-item], [tabindex], div, span, p";

    for (const root of roots) {
      for (const raw of root.querySelectorAll(selector)) {
        if (!visible(raw)) continue;
        const rawLabel = labelOf(raw);
        if (!rawLabel || rawLabel.length > 100 || !knownLabel(rawLabel)) continue;
        const element = clickableAncestor(raw, root);
        if (!visible(element) || seen.has(element)) continue;
        const label = labelOf(element);
        if (!knownLabel(label)) continue;
        seen.add(element);
        result.push({ element, label, rawLabel, surface: root !== document.body });
      }
    }
    return result;
  }

  function pickerButton() {
    const candidates = [];
    const seen = new Set();
    const selectors = [
      "button[data-testid*='model']",
      "button[aria-label*='model' i]",
      "button[aria-label*='模型']",
      "button[aria-haspopup='menu']",
      "button[aria-haspopup='listbox']",
      "form button",
    ];
    for (const selector of selectors) {
      for (const element of document.querySelectorAll(selector)) {
        if (seen.has(element) || !visible(element) || element.disabled) continue;
        seen.add(element);
        const label = labelOf(element);
        const rect = element.getBoundingClientRect();
        let score = knownLabel(label) ? 140 : 0;
        if (/model|模型/i.test(element.getAttribute("aria-label") || "")) score += 100;
        if (element.getAttribute("aria-haspopup")) score += 25;
        if (rect.bottom > window.innerHeight * 0.55) score += 25;
        if (rect.left > window.innerWidth * 0.35) score += 10;
        if (score) candidates.push({ element, score });
      }
    }
    candidates.sort((a, b) => b.score - a.score);
    return candidates[0]?.element || null;
  }

  async function openPicker() {
    const picker = await waitFor(pickerButton, 5000, 100);
    if (!picker) throw new Error("ChatGPT model picker was not found");
    picker.click();
    const choices = await waitFor(() => {
      const found = collectChoices().filter(item => item.element !== picker);
      return found.length ? found : null;
    }, 5000, 100);
    if (!choices) throw new Error("ChatGPT model menu did not expose selectable options");
    return { picker, choices };
  }

  function closeMenus() {
    for (let index = 0; index < 2; index += 1) {
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
      const areaPenalty = Math.min(20, Math.log10(Math.max(10, rect.width * rect.height)) * 2);
      const interactiveBonus = isInteractive(item.element) ? 25 : 0;
      const surfaceBonus = item.surface ? 20 : 0;
      scored.push({ ...item, score: match + interactiveBonus + surfaceBonus - areaPenalty });
    }
    scored.sort((a, b) => b.score - a.score);
    return scored[0] || null;
  }

  function visibleFamilies() {
    const map = new Map();
    for (const item of collectChoices()) {
      const family = canonicalFamily(item.label) || canonicalFamily(item.rawLabel);
      if (family && !map.has(family)) map.set(family, item);
    }
    return map;
  }

  async function clickChoice(item) {
    if (!item?.element) return false;
    item.element.scrollIntoView({ block: "nearest", inline: "nearest" });
    item.element.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, cancelable: true, view: window }));
    item.element.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
    item.element.click();
    item.element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
    await delay(350);
    return true;
  }

  async function chooseFamily(family) {
    const aliases = FAMILY_ALIASES[family] || [family];
    const opened = await openPicker();
    let families = visibleFamilies();

    if (families.size >= 2 && families.has(family)) {
      await clickChoice(families.get(family));
      await delay(450);
      return;
    }

    const trigger = bestChoice(
      families.size === 1 ? FAMILY_ALIASES[[...families.keys()][0]] : aliases,
      new Set([opened.picker]),
    );
    if (!trigger) {
      closeMenus();
      throw new Error(`Model family menu entry was not found. Visible choices: ${collectChoices().map(item => item.label).join(" | ") || "none"}`);
    }

    const triggerElement = trigger.element;
    await clickChoice(trigger);
    families = await waitFor(() => {
      const current = visibleFamilies();
      if (current.size >= 2 || (current.has(family) && current.get(family)?.element !== triggerElement)) return current;
      return null;
    }, 5000, 100);

    if (!families || !families.has(family)) {
      closeMenus();
      throw new Error(`Requested model family is not available in ChatGPT: ${family}. Visible choices: ${collectChoices().map(item => item.label).join(" | ") || "none"}`);
    }

    const target = families.get(family);
    if (target.element !== triggerElement) await clickChoice(target);
    await delay(450);
  }

  async function chooseReasoning(reasoning) {
    const aliases = REASONING_ALIASES[reasoning] || [reasoning];
    const opened = await openPicker();
    const choice = bestChoice(aliases, new Set([opened.picker]));
    if (!choice) {
      closeMenus();
      throw new Error(`Requested reasoning level is not available in ChatGPT: ${reasoning}. Visible choices: ${collectChoices().map(item => item.label).join(" | ") || "none"}`);
    }
    await clickChoice(choice);
    await delay(450);
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
        const reasoning = canonicalReasoning(item.label) || canonicalReasoning(item.rawLabel);
        if (reasoning && !reasonings.has(reasoning)) reasonings.set(reasoning, item.label);
      }

      if (families.size < 2 && families.size === 1) {
        const currentFamily = [...families.keys()][0];
        const trigger = bestChoice(FAMILY_ALIASES[currentFamily], new Set([opened.picker]));
        if (trigger) {
          await clickChoice(trigger);
          families = await waitFor(() => {
            const current = visibleFamilies();
            return current.size >= 2 ? current : null;
          }, 2500, 100) || families;
        }
      }

      for (const [family, item] of families) {
        if (!result.has(family)) result.set(family, modelRecord(family, "", item.label, family === requested.family && !requested.reasoning));
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
    if (message.type !== "chat2api.model.prepare.v3") return false;
    prepareModel(message.model)
      .then(data => sendResponse({ ok: true, data }))
      .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  });
})();
