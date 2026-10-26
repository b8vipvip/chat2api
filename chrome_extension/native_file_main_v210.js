/* v210: narrowly armed, same-tab ChatGPT native download byte observer.
 * No request/terminal authority. Never sends bytes to the Worker transport:
 * the isolated content script verifies signed formats before ordinary handoff.
 */
(() => {
  "use strict";
  const KEY = "__CHAT2API_NATIVE_FILE_MAIN_V210__";
  if (globalThis[KEY]) return;
  const KIND = "chat2api.native-file.v210";
  const MAX_BYTES = 6 * 1024 * 1024;
  let armed = null;
  const blobs = new Map();
  const nativeCreate = URL.createObjectURL;
  URL.createObjectURL = function(object) {
    const url = Reflect.apply(nativeCreate, this, arguments);
    if (armed && Date.now() < armed.until && object instanceof Blob
        && object.size > 0 && object.size <= MAX_BYTES) {
      if (blobs.size >= 8) blobs.clear();
      blobs.set(url, object);
    }
    return url;
  };
  const nativeRevoke = URL.revokeObjectURL;
  URL.revokeObjectURL = function(url) {
    const result = Reflect.apply(nativeRevoke, this, arguments);
    // Preserve just-created Blobs during the short, armed native download.
    if (!armed || Date.now() >= armed.until) blobs.delete(String(url));
    return result;
  };
  const encoded = bytes => {
    let result = "";
    for (let at = 0; at < bytes.length; at += 16384)
      result += String.fromCharCode(...bytes.subarray(at, at + 16384));
    return btoa(result);
  };
  const validName = text => /^[^\/\\\r\n]{1,140}\.(?:xlsx|pptx|docx|zip|json|pdf|csv|txt|md)$/i.test(text);
  const allowedSource = raw => {
    try {
      const url = new URL(raw, location.href);
      if (url.protocol === "blob:" && url.origin === location.origin) return true;
      return url.protocol === "https:" && !url.username && !url.password
        && (url.hostname === location.hostname
          || /\.(?:oaiusercontent\.com|oaistatic\.com|openai\.com|chatgpt\.com)$/i.test(url.hostname));
    } catch (_) { return false; }
  };
  async function observed(anchor) {
    const session = armed;
    if (!session || session.used || Date.now() >= session.until) return;
    const name = String(anchor?.download || session.filename || "").trim();
    const raw = String(anchor?.href || "");
    if (!validName(name) || !allowedSource(raw)) return;
    const blob = blobs.get(raw);
    // Only an explicit browser download action, not a conversation preview.
    if (!blob && !anchor?.hasAttribute?.("download")) return;
    session.used = true;
    try {
      let bytes;
      if (blob) {
        bytes = new Uint8Array(await blob.arrayBuffer());
      } else {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 6000);
        try {
          const url = new URL(raw);
          if (url.hostname === "chatgpt.com" && /^\/c\//.test(url.pathname)) return;
          const response = await fetch(raw, {
            method: "GET", redirect: "error", signal: controller.signal,
            credentials: url.origin === location.origin ? "include" : "omit",
          });
          if (!response.ok || Number(response.headers.get("content-length") || 0) > MAX_BYTES) return;
          const body = await response.blob();
          if (!body.size || body.size > MAX_BYTES) return;
          bytes = new Uint8Array(await body.arrayBuffer());
        } finally { clearTimeout(timer); }
      }
      if (!bytes || bytes.length < 4 || bytes.length > MAX_BYTES) return;
      window.postMessage({
        kind: KIND, direction: "captured", nonce: session.nonce,
        filename: name, bytes: bytes.length, base64: encoded(bytes),
      }, location.origin);
    } catch (_) { /* Explicitly report timeout to the isolated observer. */ }
  }
  const nativeClick = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function() {
    void observed(this);
    return Reflect.apply(nativeClick, this, arguments);
  };
  document.addEventListener("click", event => {
    const target = event.target;
    const anchor = target?.closest?.("a");
    if (anchor) void observed(anchor);
  }, true);
  window.addEventListener("message", event => {
    if (event.source !== window || event.origin !== location.origin) return;
    const data = event.data;
    if (data?.kind !== KIND || data.direction !== "arm"
        || typeof data.nonce !== "string" || !/^[a-z0-9-]{16,80}$/i.test(data.nonce)
        || !validName(String(data.filename || ""))) return;
    armed = {nonce:data.nonce, filename:data.filename, used:false, until:Date.now()+8500};
    blobs.clear();
    window.postMessage({kind:KIND, direction:"armed", nonce:data.nonce}, location.origin);
  });
  globalThis[KEY] = Object.freeze({version:210});
})();