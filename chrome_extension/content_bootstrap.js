(() => {
  const baseEnsureContent = ensureContent;

  ensureContent = async function ensureChat2apiContentV6(tabId) {
    await baseEnsureContent(tabId);
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: ["content_guard.js", "content_voice.js"],
      });
    } catch (_) {}
    return true;
  };
})();
