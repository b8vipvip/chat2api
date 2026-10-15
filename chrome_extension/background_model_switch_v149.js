(() => {
  "use strict";
  const KEY = "__CHAT2API_MODEL_SWITCH_V149__";
  if (globalThis[KEY]) return;

  const SCRIPT = "content_model_library_v145.js";
  const MAX_CONFIRM_MS = 16000;
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

  function failure(code, stage, message, evidence = [], after = []) {
    const error = new Error(message);
    error.code = code; error.stage = stage;
    error.model_evidence = evidence;
    error.model_after = after;
    return error;
  }

  async function send(tabId, payload, allowInjection = true) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, payload);
      if (response) return response;
    } catch (error) {
      if (!allowInjection) throw error;
    }
    if (!allowInjection) throw new Error("No content-script model selector response");
    await chrome.scripting.executeScript({target: {tabId}, files: [SCRIPT]});
    await delay(100);
    return chrome.tabs.sendMessage(tabId, payload);
  }

  // The content model library is the only DOM model-state parser. Read its
  // passive picker snapshot with scripting.executeScript: a reloaded ChatGPT
  // page can strand an asynchronous tabs.sendMessage inspection for 5+ seconds.
  // Never re-open the menu or replay a click during confirmation.
  async function inspectFreshSelection(tabId) {
    const read = async () => {
      const values = await chrome.scripting.executeScript({target:{tabId},
        func: () => globalThis.__CHAT2API_MODEL_LIBRARY_V145__?.inspectPassiveSelection?.() || null});
      return Array.isArray(values) ? values[0]?.result || null : null;
    };
    let result = await read();
    if (!result) {
      await chrome.scripting.executeScript({target:{tabId},files:[SCRIPT]});
      result = await read();
    }
    if (!result) throw new Error("Live model inspector not ready in ChatGPT document");
    return result;
  }

  // Used by BOTH the admin model task and API routing. Never trust a click
  // acknowledgement as proof that ChatGPT actually selected the new model.
  async function select(tabId, requestedModel) {
    const model = String(requestedModel || "").trim().toLowerCase();
    const start = await send(tabId, {type: "chat2api.model.prepare.v149", model});
    if (!start?.ok) {
      throw failure(String(start?.code || "model_prepare_failed"),
        String(start?.stage || "prepare-model-selection"),
        String(start?.error || "Unable to prepare ChatGPT model selection"),
        start?.evidence || []);
    }
    const prepared = start.data || {};
    if (!prepared.already_selected) {
      // DO NOT RETRY a lost commit response: the click may already be executing.
      // Retrying would create a second, untracked mutation to the live picker.
      let commit;
      try {
        commit = await send(tabId,
          {type: "chat2api.model.commit.v149", ticket: prepared.ticket}, false);
      } catch (error) {
        throw failure("model_commit_ack_lost", "commit-model-selection",
          "Unable to establish model click acknowledgement: " + String(error?.message || error),
          prepared.evidence || []);
      }
      if (!commit?.ok || commit.click_acknowledged !== true) {
        throw failure(String(commit?.code || "model_commit_rejected"),
          "commit-model-selection",
          String(commit?.error || "ChatGPT model click not acknowledged"),
          prepared.evidence || []);
      }
    }
    const deadline = Date.now() + MAX_CONFIRM_MS;
    let lastInspection = null;
    let lastError = "";
    let attempts = 0;
    let matched = 0;
    let lastUrl = "";
    while (Date.now() < deadline) {
      attempts += 1;
      try {
        const tab = await chrome.tabs.get(tabId);
        if (!tab || tab.id !== tabId) throw new Error("Original ChatGPT tab no longer exists");
        const url = String(tab.pendingUrl || tab.url || "");
        lastUrl = url.split("?")[0].slice(0,140);
        if (!/^https:\/\/(chatgpt\.com|www\.chatgpt\.com|chat\.openai\.com)\//.test(url))
          throw failure("model_tab_navigated_away", "confirm-model-selection",
            "Original ChatGPT tab navigated outside ChatGPT", prepared.evidence || []);
        if (tab.status === "loading") {
          await delay(240);
          continue;
        }
        // One read-only inspection authority shared by validation/API routing.
        const probe = {ok:true,data:await inspectFreshSelection(tabId)};
        if (probe?.ok) {
          lastInspection = probe.data || {};
          lastError = "";
          if (lastInspection.selected_model === model) {
            matched += 1;
            // Two independent post-commit observations guard against optimistic
            // menu state that has not survived React/navigation settling.
            if (matched >= 2) return {
              model, current_model: model, validated: true, validation_revision: 145,
              validation_method: "fresh-tab-picker-state-v149",
              original_model: prepared.before || null,
              navigation_aware: true, inspection_attempts: attempts,
              observed_choices: prepared.evidence || [],
              inspected_choices: lastInspection.models || [],
              inspected_at: lastInspection.inspected_at || new Date().toISOString(),
            };
          } else {
            matched = 0;
          }
        } else {
          matched = 0;
          lastError = String(probe?.error || "Picker inspection failed").slice(0,180);
        }
      } catch (error) {
        if (error?.code === "model_tab_navigated_away") throw error;
        matched = 0;
        lastError = String(error?.message || error).slice(0,180);
      }
      await delay(350);
    }
    const after = (lastInspection?.models || []).map(row => ({
      id: row.id, enabled: row.enabled, selected: row.selected,
    }));
    throw failure("model_selection_unconfirmed", "confirm-model-selection",
      "ChatGPT model selection was not confirmed in fresh page after " +
      attempts + " inspection attempts: expected=" + model +
      "; last_selected=" + (lastInspection?.selected_model || "unknown") +
      "; url=" + lastUrl + "; inspection_error=" + lastError,
      prepared.evidence || [], after);
  }

  async function safeSelect(tabId, model) {
    try { return {ok:true, data:await select(tabId, model)}; }
    catch (error) {
      return {ok:false, code:String(error?.code || "model_selection_failed"),
        stage:String(error?.stage || "confirm-model-selection"),
        error:String(error?.message || error),
        evidence:error?.model_evidence || [], after:error?.model_after || []};
    }
  }

  globalThis[KEY] = {select, safeSelect};
})();