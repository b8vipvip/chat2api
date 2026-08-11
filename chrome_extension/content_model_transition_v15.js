(() => {
  const KEY = "__CHAT2API_MODEL_TRANSITION_V15__";
  if (globalThis[KEY]) return;

  const CACHE_KEY = "chat2api:model-state:v2";
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
  const state = { pending: null, observer: null };
  globalThis[KEY] = state;

  const normalize = value => String(value || "")
    .replace(/[✓✔︎✔√]/g, "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
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

  function familyFromText(value) {
    const text = normalize(value);
    for (const [family, aliases] of Object.entries(FAMILY_ALIASES)) {
      if (aliases.some(alias => text === normalize(alias) || text.includes(normalize(alias)))) return family;
    }
    return "";
  }

  function reasoningFromText(value) {
    const text = normalize(value);
    for (const [level, aliases] of Object.entries(REASONING_ALIASES)) {
      if (aliases.some(alias => {
        const needle = normalize(alias);
        return text === needle || text.startsWith(`${needle} `) || text.endsWith(` ${needle}`);
      })) return level;
    }
    return "";
  }

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || null;
  }

  function stateControl() {
    const root = composerRoot();
    if (!root) return { label: "", family: "", reasoning: "" };
    const candidates = [...root.querySelectorAll("button,[role='button'],[aria-label],[data-value]")]
      .filter(visible)
      .map(element => {
        const label = labelOf(element);
        return { label, family: familyFromText(label), reasoning: reasoningFromText(label) };
      })
      .filter(item => item.family || item.reasoning);
    const combined = candidates.find(item => item.family && item.reasoning);
    const family = candidates.find(item => item.family);
    const reasoning = candidates.find(item => item.reasoning);
    return combined || family || reasoning || { label: "", family: "", reasoning: "" };
  }

  function readCache() {
    try { return JSON.parse(sessionStorage.getItem(CACHE_KEY) || "null") || {}; }
    catch (_) { return {}; }
  }

  function writeCache(value) {
    try { sessionStorage.setItem(CACHE_KEY, JSON.stringify({ ...value, updated_at: Date.now() })); }
    catch (_) {}
  }

  function targetFamily(model) {
    const value = normalize(model);
    return Object.keys(FAMILY_ALIASES).find(family => value === family || value.startsWith(`${family}-`)) || "";
  }

  function familyFromClickedNode(node) {
    let current = node instanceof Element ? node : null;
    for (let depth = 0; current && depth < 7; depth += 1, current = current.parentElement) {
      const family = familyFromText(labelOf(current));
      if (family) return family;
    }
    return "";
  }

  function commitFamily(family, reasoning, source) {
    const cache = readCache();
    writeCache({
      ...cache,
      family,
      reasoning: reasoning || cache.reasoning || "",
      dirty_family: false,
      dirty_reasoning: reasoning ? false : Boolean(cache.dirty_reasoning),
      source,
    });
  }

  function maybeCaptureTransition() {
    const pending = state.pending;
    if (!pending) return;
    if (Date.now() - pending.started_at > 15000) {
      state.pending = null;
      return;
    }

    const current = stateControl();
    if (current.family === pending.target && pending.target_clicked) {
      commitFamily(pending.target, current.reasoning, "family-dom-confirmed-v15");
      state.pending = null;
      return;
    }

    // GPT-5.6 Sol may collapse the combined control from e.g. "5.5 极速"
    // to only a reasoning label after a successful family selection. That
    // reasoning-only label can be 极速/中/高 or the default 智能/自动 state.
    // Treat the transition as trusted evidence only when we knew the previous
    // family, captured the exact target-family click, and the composer changed.
    // A failed click that leaves the old composer control unchanged is rejected.
    const changed = normalize(current.label) && normalize(current.label) !== normalize(pending.before_label);
    const reasoningOnly = Boolean(current.reasoning && !current.family);
    if (
      pending.target_clicked &&
      pending.before_family &&
      pending.before_family !== pending.target &&
      changed &&
      reasoningOnly
    ) {
      commitFamily(pending.target, current.reasoning, "family-transition-inference-v15");
      state.pending = null;
    }
  }

  document.addEventListener("click", event => {
    const pending = state.pending;
    if (!pending) return;
    const clicked = familyFromClickedNode(event.target);
    if (clicked !== pending.target) return;
    pending.target_clicked = true;
    pending.target_clicked_at = Date.now();
    queueMicrotask(maybeCaptureTransition);
  }, true);

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type !== "chat2api.model.prepare.v5") return false;
    const target = targetFamily(message.model);
    if (!target) return false;
    const current = stateControl();
    const cache = readCache();
    state.pending = {
      target,
      before_family: current.family || (!cache.dirty_family ? String(cache.family || "") : ""),
      before_label: current.label || "",
      target_clicked: false,
      target_clicked_at: null,
      started_at: Date.now(),
    };
    return false;
  });

  state.observer = new MutationObserver(() => maybeCaptureTransition());
  state.observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["aria-label", "aria-checked", "aria-selected", "data-state", "data-value", "data-model", "data-model-id"],
  });
})();
