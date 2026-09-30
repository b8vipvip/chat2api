(() => {
  const KEY = "__CHAT2API_MODEL_LIBRARY_V145__";
  if (globalThis[KEY]) return;

  const REVISION = 145;
  const OPEN_TIMEOUT_MS = 6000;
  const VERIFY_TIMEOUT_MS = 4000;
  const state = { running: null };
  globalThis[KEY] = state;

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

  function visible(element) {
    if (!element || !(element instanceof Element)) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function labelOf(element) {
    return String(
      element?.getAttribute?.("aria-label") ||
      element?.getAttribute?.("data-value") ||
      element?.getAttribute?.("title") ||
      element?.innerText ||
      element?.textContent ||
      "",
    ).replace(/[✓✔︎✔√]/g, "").replace(/[\u200B-\u200D\uFEFF]/g, "").replace(/\s+/g, " ").trim();
  }

  function normalize(value) {
    return String(value || "")
      .replace(/[✓✔︎✔√]/g, "")
      .replace(/[\u200B-\u200D\uFEFF]/g, "")
      .replace(/[–—]/g, "-")
      .replace(/\s+/g, " ")
      .trim()
      .toLowerCase();
  }

  function canonicalModelId(value) {
    let text = normalize(value);
    if (!text) return "";
    text = text.replace(/_/g, "-");
    if (/gpt[-\s]?(?:image|live)\b/.test(text)) return "";

    let match = text.match(/\bgpt[-\s]?(\d+(?:\.\d+)?)(?:[-\s]+(astra|pro|sol|terra|luna|mini))?\b/i);
    if (match) return `gpt-${match[1]}${match[2] ? `-${match[2].toLowerCase()}` : ""}`;

    match = text.match(/\b(\d+\.\d+)(?:[-\s]+(astra|pro|sol|terra|luna|mini))?\b/i);
    if (match) return `gpt-${match[1]}${match[2] ? `-${match[2].toLowerCase()}` : ""}`;

    match = text.match(/\b(o\d+(?:[-.][a-z0-9]+)*)\b/i);
    return match ? match[1].toLowerCase() : "";
  }

  function modelIdOf(element) {
    if (!element) return "";
    const attrs = ["data-model-id", "data-model", "data-value", "value", "aria-label", "title"];
    for (const name of attrs) {
      const model = canonicalModelId(element.getAttribute?.(name) || "");
      if (model) return model;
    }
    return canonicalModelId(labelOf(element));
  }

  function rejectedControl(element) {
    const text = normalize(`${labelOf(element)} ${element?.getAttribute?.("data-testid") || ""}`);
    return /send|submit|voice|microphone|attach|upload|file|tool|发送|语音|附件|上传/.test(text);
  }

  function modelPicker() {
    const root = [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || document;
    const selectors = [
      "button[data-testid*='model' i]",
      "button[aria-label*='model' i]",
      "button[aria-label*='模型']",
      "button[class*='composer-pill']",
      "button[aria-haspopup='menu']",
      "button[aria-haspopup='listbox']",
    ];
    const candidates = [];
    const seen = new Set();
    for (const selector of selectors) {
      for (const element of root.querySelectorAll(selector)) {
        if (seen.has(element) || !visible(element) || element.disabled || rejectedControl(element)) continue;
        seen.add(element);
        const model = modelIdOf(element);
        const cls = String(element.className || "");
        const testId = String(element.getAttribute("data-testid") || "");
        const aria = String(element.getAttribute("aria-label") || "");
        let score = 0;
        if (model) score += 250;
        if (/model/i.test(testId) || /model|模型/i.test(aria)) score += 180;
        if (/composer-pill/i.test(cls)) score += 120;
        if (element.getAttribute("aria-haspopup")) score += 40;
        if (score) candidates.push({ element, score });
      }
    }
    candidates.sort((a, b) => b.score - a.score);
    return candidates[0]?.element || null;
  }

  function menuRoots() {
    const selectors = [
      "[role='menu']",
      "[role='listbox']",
      "[data-radix-popper-content-wrapper]",
      "[data-radix-menu-content]",
      "[data-state='open']",
      "[class*='popover' i]",
    ];
    const rows = [];
    const seen = new Set();
    for (const selector of selectors) {
      for (const element of document.querySelectorAll(selector)) {
        if (seen.has(element) || !visible(element)) continue;
        const rect = element.getBoundingClientRect();
        if (rect.width < 70 || rect.height < 25) continue;
        seen.add(element);
        rows.push(element);
      }
    }
    return rows;
  }

  function interactiveAncestor(element, boundary) {
    let current = element;
    for (let i = 0; current && i < 7; i += 1, current = current.parentElement) {
      if (current.matches?.("button,[role='menuitem'],[role='menuitemradio'],[role='option'],[data-radix-collection-item],[tabindex]")) return current;
      if (current === boundary) break;
    }
    return element;
  }

  function modelChoices() {
    const roots = menuRoots();
    const result = [];
    const seen = new Set();
    for (const root of roots) {
      for (const raw of root.querySelectorAll("button,[role='menuitem'],[role='menuitemradio'],[role='option'],[data-radix-collection-item],[data-model],[data-model-id],[data-value],div,span")) {
        if (!visible(raw)) continue;
        const candidate = interactiveAncestor(raw, root);
        if (!candidate || seen.has(candidate) || !visible(candidate)) continue;
        const model = modelIdOf(candidate) || modelIdOf(raw);
        if (!model) continue;
        seen.add(candidate);
        result.push({
          element: candidate,
          id: model,
          label: labelOf(candidate) || labelOf(raw) || model,
          selected: candidate.getAttribute("aria-checked") === "true" ||
            candidate.getAttribute("aria-selected") === "true" ||
            /selected|checked/i.test(candidate.getAttribute("data-state") || ""),
        });
      }
    }
    return result;
  }

  async function waitFor(predicate, timeout = OPEN_TIMEOUT_MS, interval = 100) {
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

  function closeMenus() {
    for (const type of ["keydown", "keyup"]) {
      document.dispatchEvent(new KeyboardEvent(type, { key: "Escape", code: "Escape", bubbles: true }));
    }
  }

  async function openModelMenu() {
    closeMenus();
    const picker = await waitFor(() => modelPicker(), OPEN_TIMEOUT_MS, 120);
    if (!picker) throw new Error("ChatGPT model picker was not found");
    picker.click();
    const choices = await waitFor(() => {
      const found = modelChoices();
      return found.length ? found : null;
    }, OPEN_TIMEOUT_MS, 100);
    if (!choices) throw new Error("ChatGPT model menu did not expose model choices");
    return choices;
  }

  async function currentModel() {
    const direct = modelIdOf(modelPicker());
    if (direct) return direct;
    try {
      const choices = await openModelMenu();
      const selected = choices.find(item => item.selected)?.id || "";
      closeMenus();
      return selected;
    } catch (_) {
      closeMenus();
      return "";
    }
  }

  async function selectAndVerify(modelId) {
    const choices = await openModelMenu();
    const choice = choices.find(item => item.id === modelId);
    if (!choice) {
      closeMenus();
      return false;
    }
    choice.element.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    choice.element.click();
    await delay(450);
    const verified = await waitFor(async () => {
      const direct = modelIdOf(modelPicker());
      if (direct === modelId) return true;
      try {
        const reopened = await openModelMenu();
        const selected = reopened.find(item => item.id === modelId && item.selected);
        closeMenus();
        return Boolean(selected);
      } catch (_) {
        closeMenus();
        return false;
      }
    }, VERIFY_TIMEOUT_MS, 180);
    closeMenus();
    return Boolean(verified);
  }

  async function validateModels() {
    if (state.running) return state.running;
    state.running = (async () => {
      const startedAt = new Date().toISOString();
      const original = await currentModel();
      const initialChoices = await openModelMenu();
      const candidates = [];
      const labels = new Map();
      for (const choice of initialChoices) {
        if (!choice.id || labels.has(choice.id)) continue;
        labels.set(choice.id, choice.label || choice.id);
        candidates.push(choice.id);
      }
      closeMenus();

      const validated = [];
      for (const modelId of candidates) {
        let ok = false;
        try { ok = await selectAndVerify(modelId); }
        catch (error) { console.debug("chat2api model validation failed", modelId, error); }
        if (!ok) continue;
        validated.push({
          id: modelId,
          label: labels.get(modelId) || modelId,
          family: modelId,
          reasoning: null,
          selected: false,
          capabilities: ["text", "vision", "file-understanding"],
          reasoning_efforts: ["low", "medium", "high"],
          validated: true,
          validation_revision: REVISION,
          validated_at: new Date().toISOString(),
          validation_method: "chatgpt-model-picker-select-and-verify",
        });
      }

      if (original && validated.some(item => item.id === original)) {
        try { await selectAndVerify(original); } catch (_) {}
      }
      const selected = await currentModel();
      for (const item of validated) item.selected = item.id === selected;
      return {
        models: validated,
        current_model: selected || original || null,
        validation_state: "validated",
        validation_revision: REVISION,
        validation_started_at: startedAt,
        validated_at: new Date().toISOString(),
        candidate_count: candidates.length,
        validated_count: validated.length,
      };
    })().finally(() => { state.running = null; });
    return state.running;
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "chat2api.models.validate.v145") return false;
    validateModels()
      .then(data => sendResponse({ ok: true, data }))
      .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  });
})();
