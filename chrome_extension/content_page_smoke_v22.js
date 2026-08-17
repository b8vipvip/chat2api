(() => {
  const KEY = "__CHAT2API_PAGE_SMOKE_V22__";
  if (globalThis[KEY]) return;

  const VERSION = "22.0.0";
  const TEXT_MODELS = new Set(["gpt-5.6-sol", "gpt-5.5"]);
  const REASONING_LEVELS = new Set(["instant", "medium", "high"]);

  const page = () => globalThis.__CHAT2API_PAGE_ADAPTER_V22__ || null;
  const driver = () => globalThis.__CHAT2API_PAGE_DRIVER_V22__ || null;

  function normalizeModel(value) {
    const model = String(value || "").trim().toLowerCase().replace(/\s+/g, "-");
    return TEXT_MODELS.has(model) ? model : "";
  }

  function normalizeReasoning(value) {
    const level = String(value || "").trim().toLowerCase();
    if (["low", "minimal", "fast"].includes(level)) return "instant";
    return REASONING_LEVELS.has(level) ? level : "";
  }

  function evidenceSnapshot(evidence, key) {
    return {
      [key]: evidence?.[key] || null,
      source: evidence?.source || "none",
      label: evidence?.label || null,
    };
  }

  function snapshot(options = {}) {
    const adapter = page();
    const pageDriver = driver();
    const expectedModel = normalizeModel(options.model);
    const expectedReasoning = normalizeReasoning(options.reasoning);
    const composer = adapter?.composer?.() || null;
    const familyEvidence = adapter?.modelFamilyEvidence?.() || { family: "", source: "none" };
    const reasoningEvidence = adapter?.reasoningEvidence?.() || { reasoning: "", source: "none" };
    const currentState = pageDriver?.currentState?.() || null;
    const hasExpectation = Boolean(expectedModel || expectedReasoning);
    const verification = hasExpectation && pageDriver?.verifyState
      ? pageDriver.verifyState({ model: expectedModel, reasoning: expectedReasoning })
      : null;

    const checks = {
      adapter_loaded: Boolean(adapter),
      adapter_version: adapter?.version || null,
      driver_loaded: Boolean(pageDriver),
      driver_version: pageDriver?.version || null,
      model_controller_loaded: Boolean(globalThis.__CHAT2API_MODEL_STATE_V7__),
      reasoning_controller_loaded: Boolean(globalThis.__CHAT2API_REASONING_CONTROL_V7__),
      composer_found: Boolean(composer),
      composer_visible: Boolean(composer && (adapter?.visible ? adapter.visible(composer) : true)),
    };

    let code = "ok";
    if (!checks.adapter_loaded) code = "page_adapter_missing";
    else if (!checks.driver_loaded) code = "page_driver_missing";
    else if (!checks.model_controller_loaded) code = "model_controller_missing";
    else if (!checks.reasoning_controller_loaded) code = "reasoning_controller_missing";
    else if (!checks.composer_found || !checks.composer_visible) code = "composer_not_ready";
    else if (hasExpectation && !verification?.ok) code = verification?.code || "page_state_verification_failed";

    return {
      ok: code === "ok",
      code,
      harness_version: VERSION,
      checks,
      expected_model: expectedModel || null,
      expected_reasoning: expectedReasoning || null,
      current_state: currentState,
      verification,
      family_evidence: evidenceSnapshot(familyEvidence, "family"),
      reasoning_evidence: evidenceSnapshot(reasoningEvidence, "reasoning"),
    };
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "chat2api.page.smoke.v22") return false;
    try {
      const data = snapshot({
        model: message.model,
        reasoning: message.reasoning_level || message.reasoning,
      });
      sendResponse({ ok: true, data, controller: "page-smoke-v22" });
    } catch (error) {
      sendResponse({
        ok: false,
        code: "page_smoke_exception",
        error: String(error?.message || error),
        controller: "page-smoke-v22",
      });
    }
    return false;
  });

  globalThis[KEY] = Object.freeze({ version: VERSION, snapshot });
})();
