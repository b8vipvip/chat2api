(() => {
  const baseEnsureContent = ensureContent;

  ensureContent = async function ensureChat2apiContentV11(tabId) {
    await baseEnsureContent(tabId);
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: [
          "content_request_v2.js",
          "content_multimodal.js",
          "content_request_v3.js",
          "content_multimodal_v4.js",
          "content_request_v4.js",
          "content_request_v5.js",
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
