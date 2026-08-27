(() => {
  const baseEnsureContent = ensureContent;

  ensureContent = async function ensureChat2apiContentV21(tabId) {
    await baseEnsureContent(tabId);
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: [
          "content_page_adapter_v22.js",
          "content_login_v27.js",
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
          "content_response_capture_v41.js",
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
          "content_runtime_log.js"
        ],
      });
    } catch (_) {}
    return true;
  };
})();
