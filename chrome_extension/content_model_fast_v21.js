(() => {
  const KEY = "__CHAT2API_MODEL_FAST_V21__";
  if (globalThis[KEY]) return;
  globalThis[KEY] = { version: "v21" };

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const FAMILY_ALIASES = {
    "gpt-5.6-sol": ["gpt-5.6 sol", "gpt 5.6 sol", "5.6 sol"],
    "gpt-5.5": ["gpt-5.5", "gpt 5.5", "5.5"],
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

  function aliasScore(label, aliases) {
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

  async function waitFor(predicate, timeout = 1800, interval = 80) {
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
    for (const selector of [
      "#prompt-textarea",
      "form[data-type='unified-composer'] [contenteditable='true']",
      "form[data-type='unified-composer'] textarea",
      "textarea[data-id='root']",
    ]) {
      const element = [...document.querySelectorAll(selector)].find(visible);
      if (element) return element;
    }
    return null;
  }

  function composerRoot() {
    const explicit = [...document.querySelectorAll("form[data-type='unified-composer']")].find(visible);
    if (explicit) return explicit;
    const input = composerInput();
    const form = input?.closest?.("form");
    return visible(form) ? form : null;
  }

  function rejectedButton(element) {
    const value = normalize(`${labelOf(element)} ${element?.getAttribute?.("aria-label") || ""} ${element?.getAttribute?.("data-testid") || ""}`);
    return /send|submit|voice|microphone|mic|audio|attach|upload|file|tool|添加|附件|上传|语音|麦克风|发送/.test(value);
  }

  function composerPill(root = composerRoot()) {
    if (!root) return null;
    const candidates = [];
    const seen = new Set();
    for (const selector of [
      "button[class*='composer-pill'][aria-haspopup='menu']",
      "button[class*='composer-pill'][aria-haspopup='listbox']",
      "button[data-testid*='model' i]",
      "button[aria-label*='model' i]",
      "button[aria-label*='模型']",
      "button[aria-haspopup='menu']",
      "button[aria-haspopup='listbox']",
    ]) {
      for (const element of root.querySelectorAll(selector)) {
        if (seen.has(element) || !visible(element) || element.disabled || rejectedButton(element)) continue;
        seen.add(element);
        let score = 0;
        const text = labelOf(element);
        if (/composer-pill/i.test(String(element.className || ""))) score += 250;
        if (/model/i.test(element.getAttribute("data-testid") || "")) score += 200;
        if (Object.values(FAMILY_ALIASES).some(aliases => aliasScore(text, aliases))) score += 170;
        if (/model|模型/i.test(element.getAttribute("aria-label") || "")) score += 140;
        if (element.getAttribute("aria-haspopup")) score += 60;
        if (score) candidates.push({ element, score });
      }
    }
    candidates.sort((a, b) => b.score - a.score);
    return candidates[0]?.element || null;
  }

  function openSurfaces() {
    const result = [];
    const seen = new Set();
    for (const selector of [
      "[role='menu']",
      "[role='listbox']",
      "[data-radix-popper-content-wrapper]",
      "[data-radix-menu-content]",
      "[data-state='open']",
      "[class*='popover' i]",
      "[class*='menu' i]",
    ]) {
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

  function interactive(element) {
    return Boolean(element && (element.matches("button,[role='menuitem'],[role='menuitemradio'],[role='option'],[data-radix-collection-item],[tabindex],a") || getComputedStyle(element).cursor === "pointer"));
  }

  function clickableAncestor(element, boundary) {
    let current = element;
    for (let depth = 0; current && depth < 8; depth += 1, current = current.parentElement) {
      if (interactive(current)) return current;
      if (boundary && current === boundary) break;
    }
    return element;
  }

  function findVisibleText(aliases) {
    const surfaces = openSurfaces();
    const roots = surfaces.length ? surfaces : [document.body];
    const found = [];
    const seen = new Set();
    for (const root of roots) {
      for (const raw of root.querySelectorAll("button,[role='menuitem'],[role='menuitemradio'],[role='option'],div,span,p")) {
        if (!visible(raw)) continue;
        const rawLabel = labelOf(raw);
        if (!rawLabel || rawLabel.length > 120) continue;
        let score = aliasScore(rawLabel, aliases);
        if (!score) continue;
        const element = clickableAncestor(raw, root);
        if (!visible(element) || seen.has(element)) continue;
        seen.add(element);
        score = Math.max(score, aliasScore(labelOf(element), aliases));
        if (interactive(element)) score += 20;
        if (root !== document.body) score += 20;
        found.push({ element, rawLabel, score });
      }
    }
    found.sort((a, b) => b.score - a.score);
    return found[0] || null;
  }

  async function clickElement(element) {
    if (!element) return false;
    element.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    const rect = element.getBoundingClientRect();
    const init = {
      bubbles: true,
      cancelable: true,
      view: window,
      clientX: rect.left + rect.width / 2,
      clientY: rect.top + rect.height / 2,
    };
    for (const type of ["pointerdown", "mousedown", "mouseup", "click"]) element.dispatchEvent(new MouseEvent(type, init));
    await delay(140);
    return true;
  }

  function closeMenus() {
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true }));
    document.dispatchEvent(new KeyboardEvent("keyup", { key: "Escape", code: "Escape", bubbles: true }));
  }

  async function openComposerMenu() {
    const pill = await waitFor(() => composerPill(), 1800, 80);
    if (!pill) throw new Error("Fast model preselection: model picker is not ready");
    await clickElement(pill);
    const surface = await waitFor(() => openSurfaces()[0] || null, 1800, 80);
    if (!surface) throw new Error("Fast model preselection: model menu did not open");
    return { pill, surface };
  }

  async function selectFamily(model) {
    const family = String(model || "").trim().toLowerCase();
    const aliases = FAMILY_ALIASES[family];
    if (!aliases) throw new Error(`Fast model preselection does not support ${family}`);

    const started = performance.now();
    const existingPill = composerPill();
    if (existingPill && aliasScore(labelOf(existingPill), aliases) >= 70) {
      return { selected: false, already_visible: true, elapsed_ms: Math.round((performance.now() - started) * 10) / 10 };
    }

    await openComposerMenu();
    let target = findVisibleText(aliases);
    if (!target || target.score < 70) {
      const advanced = findVisibleText(["高级", "advanced"]);
      if (advanced) {
        await clickElement(advanced.element);
        await delay(80);
      }
      const modelRow = await waitFor(() => findVisibleText(["模型", "model"]), 900, 70);
      if (modelRow) {
        await clickElement(modelRow.element);
        await delay(80);
      }
      target = await waitFor(() => {
        const item = findVisibleText(aliases);
        return item && item.score >= 70 ? item : null;
      }, 1500, 80);
    }

    if (!target) {
      closeMenus();
      throw new Error(`Fast model preselection could not find ${family}`);
    }

    await clickElement(target.element);
    closeMenus();
    await delay(160);
    return {
      selected: true,
      already_visible: false,
      elapsed_ms: Math.round((performance.now() - started) * 10) / 10,
      strategy: "fast-family-click-passive-verify-later",
    };
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "chat2api.model.prepare.fast.v21") return false;
    selectFamily(message.model)
      .then(data => sendResponse({ ok: true, data }))
      .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  });
})();
