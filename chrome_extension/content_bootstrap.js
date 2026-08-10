(() => {
  const baseEnsureContent = ensureContent;

  ensureContent = async function ensureChat2apiContentV8(tabId) {
    await baseEnsureContent(tabId);
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: [
          "content_request_v2.js",
          "content_guard.js",
          "content_voice.js",
          "content_voice_v2.js",
          "content_dictation.js",
          "content_dictation_v3.js"
        ],
      });
    } catch (_) {}
    return true;
  };
})();
