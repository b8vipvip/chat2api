(() => {
  const KEY = "__CHAT2API_REASONING_CONTROL_V7__";
  if (globalThis[KEY]) return;
  globalThis[KEY] = true;

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const ALIASES = {
    instant: ["极速", "instant", "fast", "low", "minimal"],
    medium: ["中", "中等", "medium"],
    high: ["高", "high", "xhigh"],
  };
  const RATIOS = { instant: 0.05, medium: 0.5, high: 0.95 };
  const MAX_SLIDER_STEPS = 32;

  const normalize = value => String(value || "")
    .replace(/[✓✔︎✔√]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();

  function visible(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function labelOf(element) {
    return String(element?.getAttribute?.("aria-label") || element?.getAttribute?.("data-value") || element?.title || element?.innerText || element?.textContent || "")
      .replace(/\s+/g, " ").trim();
  }

  function matches(value, level) {
    const text = normalize(value);
    return (ALIASES[level] || []).some(alias => {
      const needle = normalize(alias);
      return text === needle || text.startsWith(`${needle} `) || text.endsWith(` ${needle}`);
    });
  }

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || null;
  }

  function currentPill() {
    const root = composerRoot();
    if (!root) return null;
    const candidates = [...root.querySelectorAll("button,[role='button']")].filter(visible);
    const combined = candidates.find(element => /(?:gpt|5\.[0-9])/i.test(labelOf(element)) && Object.keys(ALIASES).some(level => matches(labelOf(element), level)));
    return combined || candidates.find(element => Object.keys(ALIASES).some(level => matches(labelOf(element), level))) || null;
  }

  function openSurfaces() {
    return [...document.querySelectorAll("[role='menu'],[role='listbox'],[data-radix-popper-content-wrapper],[data-radix-menu-content],[data-state='open']")]
      .filter(element => {
        if (!visible(element)) return false;
        const rect = element.getBoundingClientRect();
        return rect.width >= 60 && rect.height >= 30;
      });
  }

  function visibleSlider() {
    const sliders = [...document.querySelectorAll("input[type='range'],[role='slider']")].filter(visible);
    const surfaces = openSurfaces();
    return sliders.find(slider => surfaces.some(surface => surface.contains(slider))) || sliders[0] || null;
  }

  function sliderStateText(slider) {
    if (!slider) return "";
    const values = [
      slider.getAttribute?.("aria-valuetext"),
      slider.getAttribute?.("data-value"),
      slider.getAttribute?.("aria-label"),
      slider.getAttribute?.("title"),
      labelOf(slider),
      labelOf(currentPill()),
    ].filter(Boolean);
    return values.join(" ");
  }

  function choiceFor(level) {
    const roots = openSurfaces();
    if (!roots.length) return null;
    for (const root of roots) {
      for (const element of root.querySelectorAll("button,[role='menuitem'],[role='menuitemradio'],[role='option'],[data-radix-collection-item],[tabindex]")) {
        if (visible(element) && matches(labelOf(element), level)) return element;
      }
    }
    return null;
  }

  function reasoningRow() {
    const roots = openSurfaces();
    for (const root of roots) {
      for (const element of root.querySelectorAll("button,[role='menuitem'],[role='menuitemradio'],[role='option'],[data-radix-collection-item],[tabindex],div")) {
        if (!visible(element)) continue;
        const text = normalize(labelOf(element));
        if (!text || text.length > 100) continue;
        if (/思考强度|推理强度|reasoning|thinking/.test(text)) {
          return element.closest("button,[role='menuitem'],[role='menuitemradio'],[role='option'],[data-radix-collection-item],[tabindex]") || element;
        }
      }
    }
    return null;
  }

  function key(target, name, code = name, extra = {}) {
    if (!target?.dispatchEvent) return;
    const init = { key: name, code, bubbles: true, cancelable: true, ...extra };
    target.dispatchEvent(new KeyboardEvent("keydown", init));
    target.dispatchEvent(new KeyboardEvent("keyup", init));
  }

  function openByShortcut() {
    for (const target of [document.activeElement, document, window]) {
      key(target, "M", "KeyM", { ctrlKey: true, shiftKey: true });
    }
  }

  async function waitFor(predicate, timeout = 1800, interval = 80) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const value = predicate();
      if (value) return value;
      await delay(interval);
    }
    return null;
  }

  async function openNoClick() {
    openByShortcut();
    let surface = await waitFor(() => visibleSlider() || openSurfaces()[0] || null, 1200, 80);
    if (surface) return { surface, strategy: "shortcut-no-click" };

    const pill = currentPill();
    if (pill) {
      pill.focus();
      key(pill, "Enter", "Enter");
      surface = await waitFor(() => visibleSlider() || openSurfaces()[0] || null, 1000, 80);
      if (surface) return { surface, strategy: "pill-enter-no-click" };
      key(pill, " ", "Space");
      surface = await waitFor(() => visibleSlider() || openSurfaces()[0] || null, 1000, 80);
      if (surface) return { surface, strategy: "pill-space-no-click" };
    }
    return null;
  }

  function setNativeRange(input, level) {
    const min = Number(input.min || 0);
    const max = Number(input.max || 100);
    const step = Number(input.step || 1);
    let value = min + (max - min) * RATIOS[level];
    if (Number.isFinite(step) && step > 0) value = Math.round((value - min) / step) * step + min;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    setter?.call(input, String(Math.min(max, Math.max(min, value))));
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  async function setSliderNoClick(slider, level) {
    if (slider instanceof HTMLInputElement && slider.type === "range") {
      setNativeRange(slider, level);
      await delay(300);
      return true;
    }

    slider.focus?.();
    if (level === "instant") {
      key(slider, "Home", "Home");
      await delay(180);
      return matches(sliderStateText(slider), level) || matches(labelOf(currentPill()), level);
    }
    if (level === "high") {
      key(slider, "End", "End");
      await delay(180);
      return matches(sliderStateText(slider), level) || matches(labelOf(currentPill()), level);
    }

    // ChatGPT sliders are not guaranteed to have exactly three keyboard stops.
    // Walk from Home until the UI itself reports the requested medium state.
    key(slider, "Home", "Home");
    await delay(100);
    if (matches(sliderStateText(slider), level) || matches(labelOf(currentPill()), level)) return true;
    for (let i = 0; i < MAX_SLIDER_STEPS; i += 1) {
      key(slider, "ArrowRight", "ArrowRight");
      await delay(90);
      if (matches(sliderStateText(slider), level) || matches(labelOf(currentPill()), level)) return true;
      const now = Number(slider.getAttribute?.("aria-valuenow"));
      const max = Number(slider.getAttribute?.("aria-valuemax"));
      if (Number.isFinite(now) && Number.isFinite(max) && now >= max) break;
    }
    return false;
  }

  async function activateNoClick(element) {
    if (!element) return false;
    element.focus?.();
    key(element, "Enter", "Enter");
    await delay(250);
    return true;
  }

  async function chooseNoClick(level) {
    const opened = await openNoClick();
    if (!opened) return null;
    let slider = visibleSlider();
    if (slider) {
      const matched = await setSliderNoClick(slider, level);
      key(document, "Escape", "Escape");
      await delay(120);
      if (matched || matches(labelOf(currentPill()), level)) {
        return { strategy: `${opened.strategy}+slider-keyboard-adaptive`, used_click: false };
      }
      return null;
    }
    let choice = choiceFor(level);
    if (choice) {
      await activateNoClick(choice);
      return { strategy: `${opened.strategy}+choice-enter`, used_click: false };
    }

    const row = reasoningRow();
    if (row) {
      await activateNoClick(row);
      const nested = await waitFor(() => visibleSlider() || choiceFor(level), 1600, 80);
      if (nested) {
        slider = visibleSlider();
        if (slider) {
          const matched = await setSliderNoClick(slider, level);
          key(document, "Escape", "Escape");
          await delay(120);
          if (matched || matches(labelOf(currentPill()), level)) {
            return { strategy: `${opened.strategy}+reasoning-row-enter+slider-keyboard-adaptive`, used_click: false };
          }
          return null;
        }
        choice = choiceFor(level);
        if (choice) {
          await activateNoClick(choice);
          return { strategy: `${opened.strategy}+reasoning-row-enter+choice-enter`, used_click: false };
        }
      }
    }
    key(document, "Escape", "Escape");
    return null;
  }

  async function chooseClickFallback(level) {
    const pill = currentPill();
    if (!pill) throw new Error("ChatGPT reasoning control was not found");
    pill.click();
    const opened = await waitFor(() => visibleSlider() || openSurfaces()[0] || null, 2500, 100);
    if (!opened) throw new Error("ChatGPT reasoning menu did not open");
    let slider = visibleSlider();
    if (slider) {
      const matched = await setSliderNoClick(slider, level);
      if (matched || matches(labelOf(currentPill()), level)) {
        key(document, "Escape", "Escape");
        await delay(180);
        return { strategy: slider instanceof HTMLInputElement ? "click-open+native-range" : "click-open+slider-keyboard-adaptive", used_click: true };
      }
    }
    let choice = choiceFor(level);
    if (!choice) {
      const row = reasoningRow();
      if (row) {
        row.click();
        await delay(250);
        slider = await waitFor(() => visibleSlider(), 1000, 80);
        if (slider) {
          const matched = await setSliderNoClick(slider, level);
          if (matched || matches(labelOf(currentPill()), level)) {
            key(document, "Escape", "Escape");
            await delay(180);
            return { strategy: "click-open+reasoning-row+slider-keyboard-adaptive", used_click: true };
          }
        }
        choice = await waitFor(() => choiceFor(level), 1800, 100);
      }
    }
    if (!choice) throw new Error(`Requested reasoning level is not available: ${level}`);
    choice.click();
    await delay(300);
    return { strategy: "click-fallback+nested-choice", used_click: true };
  }

  async function prepare(level) {
    const requested = normalize(level);
    if (!["instant", "medium", "high"].includes(requested)) throw new Error(`Unsupported reasoning level: ${level}`);
    if (matches(labelOf(currentPill()), requested)) {
      return { reasoning: requested, zero_op: true, reasoning_switched: false, selection_strategy: "reasoning-dom-zero-op", used_click: false };
    }

    let result = await chooseNoClick(requested);
    if (matches(labelOf(currentPill()), requested)) {
      return { reasoning: requested, zero_op: false, reasoning_switched: true, selection_strategy: result?.strategy || "no-click", used_click: false };
    }

    result = await chooseClickFallback(requested);
    if (!matches(labelOf(currentPill()), requested)) {
      throw new Error(`ChatGPT reasoning level could not be verified after selection: ${requested}`);
    }
    return { reasoning: requested, zero_op: false, reasoning_switched: true, selection_strategy: result.strategy, used_click: true };
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type === "chat2api.reasoning.prepare.v7") {
      prepare(message.reasoning_level)
        .then(data => sendResponse({ ok: true, data, controller: "reasoning-v7.2" }))
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error), controller: "reasoning-v7.2" }));
      return true;
    }
    return false;
  });
})();
