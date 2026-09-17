(() => {
  const KEY = "__CHAT2API_RESPONSE_SEMANTIC_RECOVERY_V51__";
  if (globalThis[KEY]) return;

  // Semantic filtering is a helper only. Terminal ownership belongs to
  // request-v6; this module deliberately has no observer, timer or dependency
  // on the retired v49/v69 response-terminal owners.
  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  const ROLE_ONLY = /^(?:chatgpt|assistant|ai)\s*(?:said|says|回复|回答|说)\s*[:：]?\s*$/i;
  const ROLE_PREFIX = /^(?:chatgpt|assistant|ai)\s*(?:said|says|回复|回答|说)\s*[:：]\s*/i;

  function sanitize(value) {
    const text = normalize(value);
    if (!text) return { text: "", filtered: false };
    if (ROLE_ONLY.test(text)) return { text: "", filtered: true };
    const stripped = text.replace(ROLE_PREFIX, "").trim();
    return { text: stripped, filtered: stripped !== text };
  }

  function bodyText(turn) {
    const result = sanitize(turn?.innerText || turn?.textContent || "");
    return { ...result, source: "semantic-helper-v51" };
  }

  globalThis[KEY] = Object.freeze({
    version: 51,
    mode: "semantic-helper-only",
    owner: "request-v6",
    timer: null,
    sanitize,
    bodyText,
  });
})();
