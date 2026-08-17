(() => {
  const KEY = "__CHAT2API_MODEL_STATE_V7__";
  if (globalThis[KEY]) return;

  const VERSION = "0.4.1";
  const CACHE_KEY = "chat2api:model-state:v2";
  const AUTO_KEY = "__CHAT2API_MODEL_AUTOMATION_V7__";
  const FAMILIES = ["gpt-5.6-sol", "gpt-5.5"];
  const FAMILY_ALIASES = {
    "gpt-5.6-sol": ["gpt-5.6 sol", "gpt 5.6 sol", "5.6 sol"],
    "gpt-5.5": ["gpt-5.5", "gpt 5.5", "5.5"],
  };
  const REASONING_ALIASES = {
    instant: ["极速", "instant", "fast", "minimal", "low"],
    medium: ["中", "medium"],
    high: ["高", "high", "xhigh"],
  };

  const state = { observer: null };
  globalThis[KEY] = state;

  const page = () => globalThis.__CHAT2API_PAGE_ADAPTER_V22__ || null;
  const normalizeFallback = value => String(value || "")
    .replace(/[✓✔︎✔√]/g, "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
  const normalize = value => page()?.normalizedLabelLower?.(value) ?? normalizeFallback(value);

  function visible(element) {
    const adapter = page();
    if (adapter?.visible) return adapter.visible(element);
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function labelOf(element) {
    const adapter = page();
    if (adapter?.labelOf) return adapter.labelOf(element);
    return String(
      element?.getAttribute?.("aria-label") ||
      element?.getAttribute?.("data-value") ||
      element?.getAttribute?.("title") ||
      element?.innerText ||
      element?.textContent ||
      "",
    ).replace(/\s+/g, " ").trim();
  }

  function familyFromText(value) {
    const adapter = page();
    if (adapter?.familyFromText) return adapter.familyFromText(value);
    const text = normalize(value);
    for (const family of FAMILIES) {
      if ((FAMILY_ALIASES[family] || []).some(alias => text === normalize(alias) || text.includes(normalize(alias)))) return family;
    }
    return "";
  }

  function reasoningFromText(value) {
    const adapter = page();
    if (adapter?.reasoningFromText) {
      const level = adapter.reasoningFromText(value);
      return ["instant", "medium", "high"].includes(level) ? level : "";
    }
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
    const adapter = page();
    if (adapter?.composerRoot) return adapter.composerRoot();
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || null;
  }

  function readCache() {
    try { return JSON.parse(sessionStorage.getItem(CACHE_KEY) || "null") || {}; }
    catch (_) { return {}; }
  }

  function writeCache(value) {
    try { sessionStorage.setItem(CACHE_KEY, JSON.stringify({ ...value, updated_at: Date.now() })); }
    catch (_) {}
  }

  function passiveReasoning() {
    const adapter = page();
    if (adapter?.reasoningEvidence) return adapter.reasoningEvidence();
    const root = composerRoot() || document;
    const candidates = [...root.querySelectorAll("button,[role='button'],[aria-label],[data-value]")]
      .filter(visible)
      .map(element => ({ element, text: labelOf(element), level: reasoningFromText(labelOf(element)) }))
      .filter(item => item.level);
    if (!candidates.length) return { reasoning: "", source: "none" };
    const exact = candidates.find(item => normalize(item.text) === normalize((REASONING_ALIASES[item.level] || [])[0]));
    const combined = candidates.find(item => familyFromText(item.text));
    const item = exact || combined || candidates[0];
    return { reasoning: item.level, source: "composer-dom", label: item.text };
  }

  function passiveFamily() {
    const adapter = page();
    if (adapter?.modelFamilyEvidence) return adapter.modelFamilyEvidence();
    const root = composerRoot() || document;
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
      for (const element of root.querySelectorAll(selector)) {
        if (seen.has(element)) continue;
        seen.add(element);
        const values = [labelOf(element), ...attributeNames.map(name => element.getAttribute?.(name) || "")];
        const family = values.map(familyFromText).find(Boolean) || "";
        if (family) rows.push({ family, source: "composer-dom", label: values.find(value => familyFromText(value) === family) || "" });
      }
    }
    // ChatGPT currently often renders a single combined composer pill such as
    // "5.5 高". It may not expose model-specific attributes, so scan visible
    // composer controls as passive evidence too.
    for (const element of root.querySelectorAll("button,[role='button'],[aria-label],[data-value]")) {
      if (!visible(element)) continue;
      const text = labelOf(element);
      const family = familyFromText(text);
      if (family) rows.push({ family, source: "composer-dom", label: text });
    }
    const unique = [...new Set(rows.map(item => item.family))];
    if (unique.length === 1) return rows.find(item => item.family === unique[0]);
    return { family: "", source: unique.length > 1 ? "ambiguous-dom" : "none" };
  }

  function requestedParts(model, reasoningLevel) {
    const family = normalize(model);
    const reasoning = normalize(reasoningLevel);
    return {
      family: FAMILIES.includes(family) ? family : "",
      reasoning: ["instant", "medium", "high"].includes(reasoning) ? reasoning : "",
    };
  }

  function currentState() {
    const cache = readCache();
    const domFamily = passiveFamily();
    const domReasoning = passiveReasoning();
    const family = domFamily.family || (!cache.dirty_family ? String(cache.family || "") : "");
    const reasoning = domReasoning.reasoning || String(cache.reasoning || "");
    const familyTrusted = Boolean(domFamily.family || (family && !cache.dirty_family));
    const reasoningTrusted = Boolean(domReasoning.reasoning || (reasoning && !cache.dirty_reasoning));
    return {
      family,
      reasoning,
      family_trusted: familyTrusted,
      reasoning_trusted: reasoningTrusted,
      family_source: domFamily.family ? domFamily.source : (familyTrusted ? "session-cache" : domFamily.source),
      reasoning_source: domReasoning.reasoning ? domReasoning.source : (reasoningTrusted ? "session-cache" : domReasoning.source),
      family_label: domFamily.label || "",
      reasoning_label: domReasoning.label || "",
      cache,
    };
  }

  function probe(model, reasoningLevel) {
    const started = performance.now();
    const requested = requestedParts(model, reasoningLevel);
    const current = currentState();
    const familyMatch = Boolean(requested.family && current.family_trusted && current.family === requested.family);
    const reasoningMatch = !requested.reasoning || Boolean(current.reasoning_trusted && current.reasoning === requested.reasoning);
    return {
      router_version: VERSION,
      requested_model: requested.family || String(model || ""),
      requested_family: requested.family || null,
      requested_reasoning: requested.reasoning || null,
      actual_family: current.family || null,
      actual_reasoning: current.reasoning || null,
      actual_model: current.family || null,
      family_match: familyMatch,
      reasoning_match: reasoningMatch,
      family_trusted: current.family_trusted,
      reasoning_trusted: current.reasoning_trusted,
      state_trusted: Boolean(current.family_trusted && (!requested.reasoning || current.reasoning_trusted)),
      zero_op: Boolean(familyMatch && reasoningMatch),
      state_source: `${current.family_source}+${current.reasoning_source}`,
      family_source: current.family_source,
      reasoning_source: current.reasoning_source,
      family_label: current.family_label,
      reasoning_label: current.reasoning_label,
      state_detect_ms: Math.round((performance.now() - started) * 10) / 10,
    };
  }

  function commit(model, reasoningLevel) {
    const requested = requestedParts(model, reasoningLevel);
    const current = currentState();
    writeCache({
      family: requested.family || current.family || "",
      reasoning: requested.reasoning || current.reasoning || "",
      dirty_family: false,
      dirty_reasoning: false,
      source: "automation-commit-v7",
    });
    return probe(model, reasoningLevel);
  }

  function staticCatalog() {
    const current = currentState();
    return {
      models: FAMILIES.map(id => ({
        id,
        label: id === "gpt-5.6-sol" ? "GPT-5.6 Sol" : "GPT-5.5",
        family: id,
        reasoning: null,
        selected: current.family_trusted && current.family === id,
        capabilities: ["text", "vision", "file-understanding"],
        reasoning_efforts: ["low", "medium", "high"],
      })),
      current_model: current.family_trusted ? current.family : null,
      current_reasoning: current.reasoning_trusted ? current.reasoning : null,
      router_version: VERSION,
      selection_strategy: "passive-dom+trusted-session-cache",
    };
  }

  document.addEventListener("click", event => {
    if (globalThis[AUTO_KEY]) return;
    const target = event.target?.closest?.("button,[role='menuitem'],[role='menuitemradio'],[role='option'],[data-radix-collection-item]");
    if (!target) return;
    const text = labelOf(target);
    const family = familyFromText(text);
    const reasoning = reasoningFromText(text);
    if (family) {
      const cache = readCache();
      writeCache({ ...cache, family, dirty_family: false, source: "manual-family-choice" });
    }
    if (reasoning) {
      const cache = readCache();
      writeCache({ ...cache, reasoning, dirty_reasoning: false, source: "manual-reasoning-choice" });
    }
  }, true);

  state.observer = new MutationObserver(() => {
    if (globalThis[AUTO_KEY]) return;
    const current = currentState();
    const cache = readCache();
    const patch = { ...cache };
    let changed = false;
    if (current.reasoning_source === "composer-dom" && current.reasoning && current.reasoning !== cache.reasoning) {
      patch.reasoning = current.reasoning;
      patch.dirty_reasoning = false;
      changed = true;
    }
    if (current.family_source === "composer-dom" && current.family && current.family !== cache.family) {
      patch.family = current.family;
      patch.dirty_family = false;
      changed = true;
    }
    if (changed) writeCache(patch);
  });
  state.observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-label", "aria-checked", "aria-selected", "data-state", "data-value", "data-model", "data-model-id"] });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type === "chat2api.model.probe.v7") {
      sendResponse({ ok: true, data: probe(message.model, message.reasoning_level) });
      return false;
    }
    if (message.type === "chat2api.model.commit.v7") {
      sendResponse({ ok: true, data: commit(message.model, message.reasoning_level) });
      return false;
    }
    if (message.type === "chat2api.models.discover.v7") {
      sendResponse({ ok: true, data: staticCatalog() });
      return false;
    }
    if (message.type === "chat2api.model.automation.v7") {
      globalThis[AUTO_KEY] = Boolean(message.active);
      sendResponse({ ok: true });
      return false;
    }
    if (message.type === "chat2api.model.invalidate.v7") {
      const cache = readCache();
      writeCache({ ...cache, dirty_family: true, dirty_reasoning: true, dirty_reason: message.reason || "invalidated" });
      sendResponse({ ok: true });
      return false;
    }
    return false;
  });
})();
