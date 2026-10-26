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
    // A finished reply may trigger a file link without an HTML download
    // attribute. Permit only a file-shaped, allowlisted URL while specifically
    // armed by one verified reply button; ordinary preview/navigation links
    // must never be read as binary file handoffs.
    const path = new URL(raw, location.href).pathname.toLowerCase();
    const fileEndpoint = /\.(?:xlsx|pptx|docx|zip|json|pdf|csv|txt|md)$/.test(path)
      || /\/(?:download|files?\/download)(?:\/|$)/.test(path);
    if (!blob && !anchor?.hasAttribute?.("download") && !fileEndpoint) return;
    if (session.inFlight) return;
    session.inFlight = true;
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
      session.used = true;
      window.postMessage({
        kind: KIND, direction: "captured", nonce: session.nonce,
        filename: name, bytes: bytes.length, base64: encoded(bytes),
      }, location.origin);
    } catch (_) { /* Do not consume this one-shot arm on a failed preview. */ }
    finally { session.inFlight = false; }
  }
  // Some ChatGPT download controls use fetch() and a browser-owned save
  // operation rather than <a download>. Inspect only an armed, allowlisted
  // file response; never intercept normal conversation SSE or mutate it.
  const beforeFileFetch = globalThis.fetch;
  globalThis.fetch = function(...args) {
    const result = Reflect.apply(beforeFileFetch, this, args);
    const session = armed;
    if (session && !session.used && Date.now() < session.until) {
      void Promise.resolve(result).then(async response => {
        if (session !== armed || session.used || session.inFlight || !response?.ok) return;
        const raw = String(response.url || "");
        if (!allowedSource(raw)) return;
        const disposition = String(response.headers?.get?.("content-disposition") || "");
        const type = String(response.headers?.get?.("content-type") || "").toLowerCase();
        const ext = session.filename.split(".").pop().toLowerCase();
        const mimeMatches = (ext === "zip" && /application\/(?:zip|x-zip-compressed)/.test(type))
          || (ext === "xlsx" && type.includes("spreadsheetml.sheet"))
          || (ext === "pptx" && type.includes("presentationml.presentation"))
          || (ext === "json" && type.includes("application/json") && /attachment/i.test(disposition));
        const exactName = disposition.includes(session.filename);
        const pathName = new URL(raw).pathname.toLowerCase().endsWith("/" + session.filename.toLowerCase());
        if (!exactName && !pathName && !mimeMatches) return;
        if (Number(response.headers.get("content-length") || 0) > MAX_BYTES) return;
        session.inFlight = true;
        try {
          const buffer = await response.clone().arrayBuffer();
          if (!buffer.byteLength || buffer.byteLength > MAX_BYTES || session !== armed || session.used) return;
          session.used = true;
          window.postMessage({kind:KIND,direction:"captured",nonce:session.nonce,
            filename:session.filename,bytes:buffer.byteLength,base64:encoded(new Uint8Array(buffer))},
            location.origin);
        } catch (_) { /* read-only observation must not change native fetch */ }
        finally { session.inFlight = false; }
      }).catch(() => {});
    }
    return result;
  };
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