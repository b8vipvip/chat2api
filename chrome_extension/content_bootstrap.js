(() => {
  const baseEnsureContent = ensureContent;

  ensureContent = async function ensureChat2apiContentV21(tabId) {
    await baseEnsureContent(tabId);
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        files: ["network_stream_main_v54.js"],
      });
      await chrome.scripting.executeScript({
        target: { tabId },
        files: [
          "content_page_adapter_v22.js",
          "content_login_v27.js",
          "content_rate_limit_guard_v52.js",
          "content_tool_isolation_v48.js",
          "content_page_driver_v22.js",
          "content_request_v2.js",
          "content_model_v5.js",
          "content_model_fast_v21.js",
          "content_model_cache_migrate_v7.js",
          "content_model_v7.js",
          "content_model_transition_v15.js",
          "content_reasoning_v7.js",
          "content_page_smoke_v22.js",
          "content_multimodal.js",
          "content_request_v3.js",
          "content_multimodal_v4.js",
          "content_request_v4.js",
          "content_request_v5.js",
          "content_request_hygiene_v42.js",
          "content_draft_ownership_v43.js",
          "content_response_capture_v41.js",
          "content_response_stream_recovery_v49.js",
          "content_network_stream_progress_v54.js",
          "content_request_stall_guard_v34.js",
          "content_generation_liveness_v49.js",
          "content_request_perf_v21.js",
          "content_completion_v6.js",
          "content_completion_fast_v21.js",
          "content_reasoning_transport_v20.js",
          "content_format_v20.js",
          "content_account_v20.js",
          "content_guard.js",
          "content_voice.js",
          "content_voice_v2.js",
          "content_voice_fix_v3.js",
          "content_voice_fix_v4.js",
          "content_runtime_log.js",
          "content_runtime_contract_v48.js"
        ],
      });
    } catch (_) {}
    return true;
  };
})();