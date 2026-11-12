(() => {
  const KEY = "__CHAT2API_SHORT_REPLY_STABILITY_POLICY_V1__";
  if (globalThis[KEY]) return;

  const SHORT_VERIFIED_STABLE_MS = 4500;
  const DEFAULT_STABLE_MS = 8000;

  function requiredStableMs(rendered) {
    // Only a positive request-v6 short-text verification may use the shorter
    // quiet window. Missing, false or malformed evidence stays conservative.
    return rendered?.shortTextVerified === true
      ? SHORT_VERIFIED_STABLE_MS : DEFAULT_STABLE_MS;
  }

  function now() {
    return globalThis.performance.now();
  }

  function elapsedMs(startedAt, currentAt = now()) {
    const start = Number(startedAt);
    const current = Number(currentAt);
    if (!Number.isFinite(start) || !Number.isFinite(current) || current < start) {
      return null;
    }
    return current - start;
  }

  function isStable(rendered, startedAt, currentAt = now()) {
    const elapsed = elapsedMs(startedAt, currentAt);
    return elapsed !== null && elapsed >= requiredStableMs(rendered);
  }

  Object.defineProperty(globalThis, KEY, {
    value: Object.freeze({ requiredStableMs, now, elapsedMs, isStable }),
    configurable: false,
    enumerable: false,
    writable: false,
  });
})();
