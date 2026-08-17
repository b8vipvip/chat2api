(() => {
  const KEY = "__CHAT2API_PAGE_DRIVER_V22__";
  if (globalThis[KEY]) return;

  const VERSION = "22.3.0";
  const CACHE_KEY = "chat2api:model-state:v2";
  const TEXT_MODELS = new Set(["gpt-5.6-sol", "gpt-5.5"]);
  const REASONING_LEVELS = new Set(["instant", "medium", "high"]);

  const page = () => globalThis.__CHAT2API_PAGE_ADAPTER_V22__ || null;

  function normalizeModel(value) {
    const normalized = String(value || "").trim().toLowerCase().replace(/\s+/g, "-");
    return TEXT_MODELS.has(normalized) ? normalized : "";
  }

  function normalizeReasoning(value) {
    const normalized = String(value || "").trim().toLowerCase();
    if (["low", "minimal", "fast"].includes(normalized)) return "instant";
    return REASONING_LEVELS.has(normalized) ? normalized : "";
  }

  // Phase 7 begins write-side migration with one deliberately low-level,
  // stateless primitive. The Driver never calls this on its own; feature
  // controllers remain responsible for deciding what key to send and when.
  function dispatchKey(target, name, code = name, extra = {}) {
    if (!target?.dispatchEvent) return false;
    const init = { key: name, code, bubbles: true, cancelable: true, ...extra };
    target.dispatchEvent(new KeyboardEvent("keydown", init));
    target.dispatchEvent(new KeyboardEvent("keyup", init));
    return true;
  }

  function readCache() {
    try { return JSON.parse(sessionStorage.getItem(CACHE_KEY) || "null") || {}; }
    catch (_) { return {}; }
  }

  function currentState() {
    const adapter = page();
    const familyEvidence = adapter?.modelFamilyEvidence?.() || { family: "", source: "none" };
    const reasoningEvidence = adapter?.reasoningEvidence?.() || { reasoning: "", source: "none" };
    const cache = readCache();

    const family = familyEvidence.family || (!cache.dirty_family ? String(cache.family || "") : "");
    const reasoning = reasoningEvidence.reasoning || (!cache.dirty_reasoning ? String(cache.reasoning || "") : "");
    const familyTrusted = Boolean(familyEvidence.family || (family && !cache.dirty_family));
    const reasoningTrusted = Boolean(reasoningEvidence.reasoning || (reasoning && !cache.dirty_reasoning));

    return {
      family,
      reasoning,
      family_trusted: familyTrusted,
      reasoning_trusted: reasoningTrusted,
      family_source: familyEvidence.family ? familyEvidence.source : (familyTrusted ? "session-cache" : familyEvidence.source),
      reasoning_source: reasoningEvidence.reasoning ? reasoningEvidence.source : (reasoningTrusted ? "session-cache" : reasoningEvidence.source),
      family_label: familyEvidence.label || "",
      reasoning_label: reasoningEvidence.label || "",
      cache_dirty_family: Boolean(cache.dirty_family),
      cache_dirty_reasoning: Boolean(cache.dirty_reasoning),
    };
  }

  function verifyState(options = {}) {
    const requestedModel = normalizeModel(options.model);
    const requestedReasoning = normalizeReasoning(options.reasoning);
    const current = currentState();
    let code = "ok";

    if (requestedModel && !current.family_trusted) code = "model_state_untrusted";
    else if (requestedModel && current.family !== requestedModel) code = "model_mismatch";
    else if (requestedReasoning && !current.reasoning_trusted) code = "reasoning_state_untrusted";
    else if (requestedReasoning && current.reasoning !== requestedReasoning) code = "reasoning_mismatch";

    return {
      ok: code === "ok",
      code,
      driver_version: VERSION,
      requested_model: requestedModel || null,
      requested_reasoning: requestedReasoning || null,
      actual_model: current.family || null,
      actual_reasoning: current.reasoning || null,
      family_trusted: current.family_trusted,
      reasoning_trusted: current.reasoning_trusted,
      family_source: current.family_source,
      reasoning_source: current.reasoning_source,
      family_label: current.family_label,
      reasoning_label: current.reasoning_label,
      cache_dirty_family: current.cache_dirty_family,
      cache_dirty_reasoning: current.cache_dirty_reasoning,
    };
  }

  function verifyReasoning(level) {
    return verifyState({ reasoning: level });
  }

  function classifyReasoningError(error, level) {
    const message = String(error?.message || error || "Reasoning selection failed");
    const verification = verifyReasoning(level);
    let code = "reasoning_selection_failed";
    if (/not found/i.test(message)) code = "reasoning_control_not_found";
    else if (/did not open/i.test(message)) code = "reasoning_menu_open_failed";
    else if (/not available/i.test(message)) code = "reasoning_level_unavailable";
    else if (/could not be verified|verification/i.test(message)) code = verification.ok ? "reasoning_local_verification_failed" : verification.code;
    return { code, message, verification };
  }

  function attachVerification(data, level) {
    const verification = verifyReasoning(level);
    return {
      ...(data || {}),
      page_driver_version: VERSION,
      verification,
      verification_warning: verification.ok ? null : verification.code,
    };
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "chat2api.page.verify.v22") return false;
    sendResponse({
      ok: true,
      data: verifyState({ model: message.model, reasoning: message.reasoning_level || message.reasoning }),
      controller: "page-driver-v22.3",
    });
    return false;
  });

  globalThis[KEY] = Object.freeze({
    version: VERSION,
    dispatchKey,
    currentState,
    verifyState,
    verifyReasoning,
    classifyReasoningError,
    attachVerification,
  });
})();
