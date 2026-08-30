(() => {
  const KEY = "__CHAT2API_RESPONSE_SEMANTIC_RECOVERY_V51__";
  if (globalThis[KEY]) return;

  // v0.8.9 made this module a second response-observation timer and explicitly
  // stopped the proven v49 page-progress observer. If ChatGPT's DOM did not
  // match v51's narrower assistant-body selectors, no module remained to emit a
  // snapshot, a page-progress diagnostic, or a bounded browser error; the server
  // then waited for its 150s watchdog. Semantic filtering is now integrated into
  // v49. Keep v51 only as a compatibility surface for runtime contracts/tests.
  const owner = globalThis.__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__ || null;
  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  const ROLE_ONLY = /^(?:chatgpt|assistant|ai)\s*(?:said|says|回复|回答|说)\s*[:：]?\s*$/i;
  const ROLE_PREFIX = /^(?:chatgpt|assistant|ai)\s*(?:said|says|回复|回答|说)\s*[:：]\s*/i;

  function sanitize(value) {
    if (typeof owner?.sanitizeAssistantText === "function") {
      return owner.sanitizeAssistantText(value);
    }
    const text = normalize(value);
    if (!text) return { text: "", filtered: false };
    if (ROLE_ONLY.test(text)) return { text: "", filtered: true };
    const stripped = text.replace(ROLE_PREFIX, "").trim();
    return { text: stripped, filtered: stripped !== text };
  }

  function bodyText(turn) {
    if (typeof owner?.extractTurnText === "function") {
      const text = owner.extractTurnText(turn);
      return { text: String(text || ""), filtered: false, source: "v49-single-owner" };
    }
    const result = sanitize(turn?.innerText || turn?.textContent || "");
    return { ...result, source: "compat-fallback" };
  }

  globalThis[KEY] = Object.freeze({
    version: 51,
    mode: "semantic-helper-only",
    owner: "response-stream-v49-single-owner-v53",
    timer: null,
    sanitize,
    bodyText,
  });
})();
