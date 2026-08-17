(() => {
  const KEY = "__CHAT2API_BACKGROUND_PAGE_SMOKE_V22__";
  if (globalThis[KEY]) return;
  globalThis[KEY] = true;

  const TEXT_MODELS = new Set(["gpt-5.6-sol", "gpt-5.5"]);
  const REASONING_LEVELS = new Set(["instant", "medium", "high"]);

  function canonicalModel(value) {
    const model = String(value || "").trim().toLowerCase();
    return TEXT_MODELS.has(model) ? model : "";
  }

  function canonicalReasoning(value) {
    const level = String(value || "").trim().toLowerCase();
    if (level === "low") return "instant";
    return REASONING_LEVELS.has(level) ? level : "";
  }

  async function runPageSmokeV22() {
    const tab = await resolveTargetTab();
    await ensureContent(tab.id);
    const settings = await config();
    const expectedModel = canonicalModel(settings.currentModel);
    // Reasoning is only a meaningful stored expectation when the model itself
    // is canonical and probeable. This avoids stale paid-account reasoning
    // state creating a false smoke failure on Free Mini / unknown-model routes.
    const expectedReasoning = expectedModel ? canonicalReasoning(settings.currentReasoning) : "";

    const smokeResponse = await chrome.tabs.sendMessage(tab.id, {
      type: "chat2api.page.smoke.v22",
      model: expectedModel,
      reasoning_level: expectedReasoning,
    });
    if (!smokeResponse?.ok) {
      throw new Error(smokeResponse?.error || "ChatGPT page smoke harness did not respond");
    }

    let finalProbe = null;
    let finalProbeOk = null;
    if (expectedModel) {
      const probeResponse = await chrome.tabs.sendMessage(tab.id, {
        type: "chat2api.model.probe.v7",
        model: expectedModel,
        reasoning_level: expectedReasoning,
      });
      finalProbe = probeResponse?.data || null;
      finalProbeOk = Boolean(
        probeResponse?.ok &&
        finalProbe?.family_match &&
        finalProbe?.family_trusted &&
        (!expectedReasoning || (finalProbe?.reasoning_match && finalProbe?.reasoning_trusted)),
      );
    }

    const smoke = smokeResponse.data || {};
    const ok = Boolean(smoke.ok) && finalProbeOk !== false;
    const code = smoke.ok && finalProbeOk === false ? "final_probe_mismatch" : (smoke.code || (ok ? "ok" : "page_smoke_failed"));
    const result = {
      ...smoke,
      ok,
      code,
      final_probe: finalProbe,
      final_probe_ok: finalProbeOk,
      tab_id: tab.id,
      tab_url: tab.url || tab.pendingUrl || "",
      tab_title: tab.title || "",
      checked_at: new Date().toISOString(),
    };

    await chrome.storage.local.set({
      lastPageSmoke: result,
      lastPageSmokeAt: Date.now(),
    });
    return result;
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "popup.pageSmoke") return false;
    runPageSmokeV22()
      .then(data => sendResponse({ ok: true, data }))
      .catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  });

  globalThis.chat2apiRunPageSmokeV22 = runPageSmokeV22;
})();
