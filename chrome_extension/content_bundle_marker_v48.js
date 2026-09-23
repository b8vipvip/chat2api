(() => {
  const KEY = "__CHAT2API_CONTENT_BUNDLE_MARKER_V48__";
  if (globalThis[KEY]) return;
  globalThis[KEY] = Object.freeze({
    version: 48,
    bundle: "0.22.95",
    loaded_at_ms: Date.now(),
    document_url: String(location.href || ""),
  });
})();