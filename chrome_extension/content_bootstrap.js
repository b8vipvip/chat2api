(() => {
  const baseEnsureContent = ensureContent;

  ensureContent = async function ensureChat2apiContentV7(tabId) {
    await baseEnsureContent(tabId);
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: ["content_guard.js", "content_voice.js", "content_voice_v2.js", "content_dictation.js"],
      });
    } catch (_) {}
    return true;
  };
})();
