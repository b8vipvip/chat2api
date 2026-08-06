(() => {
  const MODEL_ROUTER_VERSION = "0.3.1";
  const STATE_KEY = "__CHAT2API_MODEL_ROUTER__";
  const previous = globalThis[STATE_KEY];
  if (previous?.version === MODEL_ROUTER_VERSION) return;
  try {
    if (previous?.listener) chrome.runtime.onMessage.removeListener(previous.listener);
  } catch (_) {}
  const routerState = { version: MODEL_ROUTER_VERSION, listener: null };
  globalThis[STATE_KEY] = routerState;

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

  function visible(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
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
      const result = predicate();
      if (result) return result;
      await delay(interval);
    }
    return null;
  }

  const FAMILY_ALIASES = {
    "gpt-5.6-sol": ["gpt-5.6 sol", "gpt 5.6 sol", "5.6 sol"],
    "gpt-5.5": ["gpt-5.5", "gpt 5.5", "5.5"],
    "gpt-5.3": ["gpt-5.3", "gpt 5.3", "5.3"],
    o3: ["o3"],
  };

  const REASONING_ALIASES = {
    auto: ["智能", "自动", "auto", "automatic"],
    instant: ["极速", "即时", "instant", "fast"],
    medium: ["中", "medium"],
    high: ["高", "high"],
    xhigh: ["极高", "extra high", "xhigh"],
    pro: ["pro"],
  };

  function requestedParts(modelId) {
    const id = normalize(modelId);
    if (!id || id === "chatgpt-web") return { family: "", reasoning: "" };
    const families = Object.keys(FAMILY_ALIASES).sort((a, b) => b.length - a.length);
    for (const family of families) {
      if (id === family) return { family, reasoning: "" };
      if (id.startsWith(`${family}-`)) return { family, reasoning: id.slice(family.length + 1) };
    }
    return { family: id, reasoning: "" };
  }

  function matchesAliases(label, aliases) {
    const value = normalize(label);
    return aliases.some(alias => {
      const normalizedAlias = normalize(alias);
      return value === normalizedAlias || value.includes(normalizedAlias);
    });
  }

  function modelPickerButton() {
    const candidates = [];
    const seen = new Set();
    const selectors = [
      "button[data-testid*='model']",
      "button[aria-label*='model' i]",
      "button[aria-label*='模型']",
      "button[aria-haspopup='menu']",
      "button[aria-haspopup='listbox']",
    ];
    for (const selector of selectors) {
      for (const element of document.querySelectorAll(selector)) {
        if (seen.has(element) || !visible(element) || element.disabled) continue;
        seen.add(element);
        const label = normalize(labelOf(element));
        const rect = element.getBoundingClientRect();
        let score = 0;
        if (/gpt|o3|模型|model|智能|极速|medium|high|^中$|^高$/.test(label)) score += 100;
        if (/model|模型/i.test(element.getAttribute("aria-label") || "")) score += 80;
        if (rect.bottom > window.innerHeight * 0.5) score += 20;
        candidates.push({ element, score });
      }
    }
    candidates.sort((a, b) => b.score - a.score);
    return candidates[0]?.score > 0 ? candidates[0].element : null;
  }

  function interactiveMenuItems() {
    const selectors = [
      "[role='menuitem']",
      "[role='menuitemradio']",
      "[role='option']",
      "[data-radix-collection-item]",
      "[data-headlessui-state]",
      "[role='menu'] button",
      "[role='listbox'] button",
    ];
    const items = [];
    const seen = new Set();
    for (const selector of selectors) {
      for (const element of document.querySelectorAll(selector)) {
        if (seen.has(element) || !visible(element) || element.disabled) continue;
        const label = labelOf(element);
        if (!label) continue;
        seen.add(element);
        items.push(element);
      }
    }
    return items;
  }

  async function openPicker() {
    const picker = await waitFor(modelPickerButton, 5000, 100);
    if (!picker) throw new Error("ChatGPT model picker was not found");
    picker.click();
    const items = await waitFor(() => {
      const result = interactiveMenuItems();
      return result.length ? result : null;
    }, 5000, 100);
    if (!items) throw new Error("ChatGPT model menu did not open");
    return { picker, items };
  }

  function escapeMenus() {
    document.dispatchEvent(new KeyboardEvent("keydown", {
      key: "Escape",
      code: "Escape",
      bubbles: true,
    }));
  }

  function looksLikeSubmenuTrigger(element) {
    const aria = String(element?.getAttribute?.("aria-haspopup") || "").toLowerCase();
    const chevron = element?.querySelector?.(
      "[data-icon='chevron-right'], [data-testid*='chevron'], svg[aria-label*='right' i], svg[aria-label*='chevron' i]",
    );
    return Boolean(aria === "menu" || aria === "listbox" || chevron || /[>›»→]$/.test(labelOf(element)));
  }

  function findChoice(items, aliases, excluded = null) {
    return items.find(item => item !== excluded && matchesAliases(labelOf(item), aliases)) || null;
  }

  function familyItems(items) {
    return items.filter(item =>
      Object.values(FAMILY_ALIASES).some(aliases => matchesAliases(labelOf(item), aliases)),
    );
  }

  function findFamilySubmenuTrigger(items, targetFamily) {
    const targetAliases = FAMILY_ALIASES[targetFamily] || [targetFamily];
    const directTarget = findChoice(items, targetAliases);
    if (directTarget && looksLikeSubmenuTrigger(directTarget)) return directTarget;
    const candidates = familyItems(items);
    const explicit = candidates.filter(looksLikeSubmenuTrigger);
    return explicit[explicit.length - 1] || candidates[candidates.length - 1] || null;
  }

  async function chooseFamily(family) {
    if (!family) return;
    const aliases = FAMILY_ALIASES[family] || [family];
    const opened = await openPicker();
    const direct = findChoice(opened.items, aliases);

    if (direct) {
      direct.click();
      await delay(250);
      const nested = await waitFor(() => {
        const current = interactiveMenuItems();
        return findChoice(current, aliases, direct);
      }, 1200, 100);
      if (nested) {
        nested.click();
        await delay(450);
      }
      return;
    }

    const submenuTrigger = findFamilySubmenuTrigger(opened.items, family);
    if (!submenuTrigger) {
      escapeMenus();
      throw new Error(`Requested model family is not available in ChatGPT: ${family}`);
    }

    submenuTrigger.click();
    const nested = await waitFor(() => {
      const current = interactiveMenuItems();
      return findChoice(current, aliases, submenuTrigger);
    }, 4000, 100);
    if (!nested) {
      escapeMenus();
      throw new Error(`Requested model family is not available in ChatGPT: ${family}`);
    }
    nested.click();
    await delay(500);
  }

  async function chooseReasoning(reasoning) {
    if (!reasoning) return;
    const aliases = REASONING_ALIASES[reasoning] || [reasoning];
    const opened = await openPicker();
    const choice = findChoice(opened.items, aliases);
    if (!choice) {
      escapeMenus();
      throw new Error(`Requested reasoning level is not available in ChatGPT: ${reasoning}`);
    }
    choice.click();
    await delay(450);
  }

  function canonicalFamily(label) {
    for (const [family, aliases] of Object.entries(FAMILY_ALIASES)) {
      if (matchesAliases(label, aliases)) return family;
    }
    return "";
  }

  function canonicalReasoning(label) {
    const value = normalize(label);
    for (const [reasoning, aliases] of Object.entries(REASONING_ALIASES)) {
      if (aliases.some(alias => value === normalize(alias) || value.startsWith(`${normalize(alias)} `))) {
        return reasoning;
      }
    }
    return "";
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

  async function discoverAfterSelection(requestedModel) {
    const result = new Map();
    result.set("chatgpt-web", {
      id: "chatgpt-web",
      label: "ChatGPT current/default model",
      family: null,
      reasoning: null,
      selected: requestedModel === "chatgpt-web",
      capabilities: ["text"],
    });

    const { family: requestedFamily, reasoning: requestedReasoning } = requestedParts(requestedModel);
    if (requestedFamily) {
      const record = modelRecord(requestedFamily, requestedReasoning, requestedModel, true);
      result.set(record.id, record);
    }

    try {
      const opened = await openPicker();
      const topItems = opened.items;
      const families = new Map();
      const reasonings = new Map();
      for (const item of topItems) {
        const label = labelOf(item);
        const family = canonicalFamily(label);
        const reasoning = canonicalReasoning(label);
        if (family) families.set(family, label);
        if (reasoning) reasonings.set(reasoning, label);
      }

      const trigger = findFamilySubmenuTrigger(topItems, requestedFamily || "gpt-5.6-sol");
      if (trigger) {
        trigger.click();
        await delay(250);
        for (const item of interactiveMenuItems()) {
          const label = labelOf(item);
          const family = canonicalFamily(label);
          if (family) families.set(family, label);
        }
      }
      escapeMenus();

      for (const [family, label] of families) {
        if (!result.has(family)) {
          result.set(family, modelRecord(family, "", label, family === requestedFamily && !requestedReasoning));
        }
      }
      if (requestedFamily) {
        for (const [reasoning, label] of reasonings) {
          const id = `${requestedFamily}-${reasoning}`;
          if (!result.has(id)) {
            result.set(id, modelRecord(
              requestedFamily,
              reasoning,
              `${requestedFamily} / ${label}`,
              reasoning === requestedReasoning,
            ));
          }
        }
      }
    } catch (_) {
      escapeMenus();
    }

    return {
      models: [...result.values()],
      current_model: requestedModel || "chatgpt-web",
      router_version: MODEL_ROUTER_VERSION,
    };
  }

  async function prepareModel(modelId) {
    const model = String(modelId || "chatgpt-web").trim().toLowerCase() || "chatgpt-web";
    const { family, reasoning } = requestedParts(model);
    if (model !== "chatgpt-web") {
      await chooseFamily(family);
      await chooseReasoning(reasoning);
    }
    await delay(350);
    return discoverAfterSelection(model);
  }

  const listener = (message, _sender, sendResponse) => {
    if (message.type !== "chat2api.model.prepare.v2") return false;
    prepareModel(message.model)
      .then(data => sendResponse({ ok: true, data }))
      .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  };

  routerState.listener = listener;
  chrome.runtime.onMessage.addListener(listener);
})();
