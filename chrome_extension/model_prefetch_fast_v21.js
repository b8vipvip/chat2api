(() => {
  const KEY = "__CHAT2API_MODEL_PREFETCH_FAST_V21__";
  if (globalThis[KEY]) return;

  const baseHandleServerMessage = handleServerMessage;
  const state = { attempts: 0, successes: 0, failures: 0, lastTabId: null };
  globalThis[KEY] = state;

  const SUPPORTED = new Set(["gpt-5.6-sol", "gpt-5.5"]);

  function requestedModel(message) {
    return String(message?.options?.model || "").trim().toLowerCase();
  }

  async function maybeFastPrefetch(message) {
    if (message?.type !== "chat.request") return null;
    const model = requestedModel(message);
    if (!SUPPORTED.has(model)) return null;

    const tab = await resolveTargetTab();
    if (!tab?.id) return { skipped: true, reason: "no-target-tab" };

    const settings = await config().catch(() => ({}));
    const current = String(settings.currentModel || "").trim().toLowerCase();
    const sameTab = state.lastTabId === tab.id;
    state.lastTabId = tab.id;

    // currentModel is extension-global storage. It is reliable as a skip hint only
    // while the routed tab is unchanged. A newly claimed warm tab may still be on
    // ChatGPT's default family even when storage remembers the previous tab's model.
    if (sameTab && current === model) return { skipped: true, reason: "same-tab-cached-family-match" };

    await ensureContent(tab.id);

    state.attempts += 1;
    const started = performance.now();
    let response = null;
    try {
      response = await chrome.tabs.sendMessage(tab.id, {
        type: "chat2api.model.prepare.fast.v21",
        model,
      });
    } catch (_) {
      try {
        await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content_model_fast_v21.js"] });
        await sleep(80);
        response = await chrome.tabs.sendMessage(tab.id, {
          type: "chat2api.model.prepare.fast.v21",
          model,
        });
      } catch (error) {
        response = { ok: false, error: String(error?.message || error) };
      }
    }

    const elapsedMs = Math.round((performance.now() - started) * 10) / 10;
    if (response?.ok) state.successes += 1;
    else state.failures += 1;

    if (message?.request_id) {
      await trySendSocket({
        type: "chat.diagnostics",
        request_id: message.request_id,
        diagnostics: {
          model_prefetch_fast_v21: Boolean(response?.ok),
          model_prefetch_attempted: true,
          model_prefetch_requested_model: model,
          model_prefetch_cached_model_before: current || null,
          model_prefetch_same_tab_as_previous: sameTab,
          model_prefetch_tab_id: tab.id,
          model_prefetch_elapsed_ms: elapsedMs,
          model_prefetch_selected: Boolean(response?.data?.selected),
          model_prefetch_already_visible: Boolean(response?.data?.already_visible),
          model_prefetch_strategy: response?.data?.strategy || null,
          model_prefetch_error: response?.ok ? null : String(response?.error || "fast preselection failed").slice(0, 240),
        },
      }).catch(() => {});
    }

    return { ok: Boolean(response?.ok), elapsed_ms: elapsedMs, data: response?.data || null };
  }

  handleServerMessage = async function handleServerMessageWithFastModelPrefetch(message) {
    // This wrapper is intentionally loaded before conversation_dispatch.js. During a
    // routed request, conversation_dispatch has already assigned state.currentTab,
    // so resolveTargetTab() below resolves to that exact routed tab without claiming
    // another warm window. The canonical model router still performs passive proof
    // afterwards; this step only tries to make that proof a zero-op.
    try {
      await maybeFastPrefetch(message);
    } catch (_) {
      // Optimization failure must never replace the canonical model-selection path.
    }
    return baseHandleServerMessage(message);
  };
})();
