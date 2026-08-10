(() => {
  const baseEnsureContent = ensureContent;

  ensureContent = async function ensureChat2apiContentV9(tabId) {
    await baseEnsureContent(tabId);
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: [
          "content_request_v2.js",
          "content_multimodal.js",
          "content_request_v3.js",
          "content_guard.js",
          "content_voice.js",
          "content_voice_v2.js"
        ],
      });
    } catch (_) {}
    return true;
  };
})();
