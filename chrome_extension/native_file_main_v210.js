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
  // The page can prepare an Office Blob before the user clicks Download.
  // Retain at most eight recent, bounded Blobs in THIS physical tab. Their
  // bytes are exposed only after the separately verified request-scoped click
  // and exact filename/signature checks; no background harvesting occurs.
  const BLOB_TTL_MS = 10 * 60 * 1000;
  let armed = null;
  const blobs = new Map();
  function pruneBlobs() {
    const now = Date.now();
    for (const [url, item] of blobs) {
      if (now - item.createdAt > BLOB_TTL_MS) blobs.delete(url);
    }
    while (blobs.size > 8) blobs.delete(blobs.keys().next().value);
  }
  const nativeCreate = URL.createObjectURL;
  URL.createObjectURL = function(object) {
    const url = Reflect.apply(nativeCreate, this, arguments);
    if (object instanceof Blob && object.size > 0 && object.size <= MAX_BYTES) {
      blobs.set(String(url), {blob: object, createdAt: Date.now()});
      pruneBlobs();
    }
    return url;
  };
  const nativeRevoke = URL.revokeObjectURL;
  URL.revokeObjectURL = function(url) {
    const result = Reflect.apply(nativeRevoke, this, arguments);
    // Blob URLs may be revoked immediately after download. Keep only our
    // bounded, short-lived reference for a verified same-reply retry.
    pruneBlobs();
    return result;
  };
  // Diagnostics only: fixed stage codes, no URLs, response bytes or page text.
  // Page-side observation never decides a request terminal.
  function stage(session, code) {
    if (session && session === armed && !session.used)
      window.postMessage({kind: KIND, direction: "probe", nonce: session.nonce,
        stage: code}, location.origin);
  }
  const encoded = bytes => {
    let result = "";
    for (let at = 0; at < bytes.length; at += 16384)
      result += String.fromCharCode(...bytes.subarray(at, at + 16384));
    return btoa(result);
  };
  const validName = text => /^[^\/\\\r\n]{1,140}\.(?:xlsx|pptx|docx|zip|json|pdf|csv|txt|md)$/i.test(text);
  // Never consume a one-shot verified file arm with an HTML preview or a
  // failed download: the real browser file may arrive on a later click.
  const validBytes = (bytes, filename) => {
    if (!bytes || bytes.length < 4 || bytes.length > MAX_BYTES) return false;
    const ext = String(filename || "").split(".").pop().toLowerCase();
    if (["xlsx","pptx","zip","docx"].includes(ext))
      return bytes[0] === 0x50 && bytes[1] === 0x4b && bytes[2] === 3 && bytes[3] === 4;
    if (ext === "json") {
      try { JSON.parse(new TextDecoder().decode(bytes)); return true; }
      catch (_) { return false; }
    }
    if (ext === "pdf") return bytes[0] === 37 && bytes[1] === 80 && bytes[2] === 68 && bytes[3] === 70;
    return !bytes.slice(0, Math.min(256,bytes.length)).includes(0);
  };
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
    if (!validName(name)) { stage(session, "download-filename-invalid"); return; }
    if (!allowedSource(raw)) { stage(session, "download-url-not-trusted"); return; }
    pruneBlobs();
    const blob = blobs.get(raw)?.blob;
    // A finished reply may trigger a file link without an HTML download
    // attribute. Permit only a file-shaped, allowlisted URL while specifically
    // armed by one verified reply button; ordinary preview/navigation links
    // must never be read as binary file handoffs.
    const path = new URL(raw, location.href).pathname.toLowerCase();
    const fileEndpoint = /\.(?:xlsx|pptx|docx|zip|json|pdf|csv|txt|md)$/.test(path)
      || /\/(?:download|files?\/download)(?:\/|$)/.test(path);
    if (!blob && !anchor?.hasAttribute?.("download") && !fileEndpoint) {
      stage(session, "download-action-is-preview"); return;
    }
    stage(session, blob ? "same-tab-blob-found" : "trusted-download-fetch");
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
      if (!validBytes(bytes,name)) { stage(session, "download-signature-invalid"); return; }
      session.used = true;
      window.postMessage({
        kind: KIND, direction: "captured", nonce: session.nonce,
        filename: name, bytes: bytes.length, base64: encoded(bytes),
      }, location.origin);
    } catch (_) {
      stage(session, "download-read-failed");
      // Do not consume this one-shot arm on a failed preview.
    }
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
        const rawName = /filename\*\s*=\s*utf-8''([^;]+)/i.exec(disposition)?.[1]
          || /filename\s*=\s*"?([^";]+)"?/i.exec(disposition)?.[1] || "";
        let dispositionName = rawName.trim();
        try { dispositionName = decodeURIComponent(dispositionName); } catch (_) {}
        const exactName = dispositionName.toLowerCase() === session.filename.toLowerCase();
        const pathName = new URL(raw).pathname.toLowerCase().endsWith("/" + session.filename.toLowerCase());
        // Explicitly named OTHER downloads must never satisfy this request.
        if (dispositionName && !exactName) { stage(session, "fetch-filename-mismatch"); return; }
        if (!exactName && !pathName && !mimeMatches) return;
        if (Number(response.headers.get("content-length") || 0) > MAX_BYTES) return;
        session.inFlight = true;
        try {
          // A clone's arrayBuffer() is unbounded if Content-Length is absent.
          // Read incrementally and enforce the SAME 6 MiB limit as Blob paths.
          const reader = response.clone().body?.getReader();
          if (!reader) return;
          const parts = [];
          let size = 0;
          try {
            for (;;) {
              const {done, value} = await reader.read();
              if (done) break;
              size += value.byteLength;
              if (size > MAX_BYTES) return;
              parts.push(value);
            }
          } finally { try { await reader.cancel(); } catch (_) {} }
          const bytes = new Uint8Array(size);
          let offset = 0;
          for (const part of parts) { bytes.set(part, offset); offset += part.byteLength; }
          if (session !== armed || session.used) return;
          if (!validBytes(bytes, session.filename)) {
            stage(session, "fetch-signature-invalid"); return;
          }
          session.used = true;
          window.postMessage({kind:KIND,direction:"captured",nonce:session.nonce,
            filename:session.filename,bytes:bytes.length,base64:encoded(bytes)},
            location.origin);
        } catch (_) { stage(session, "fetch-clone-read-failed"); /* read-only */ }
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
    // Do not clear pre-created same-tab Blobs when a verified click is armed.
    pruneBlobs();
    window.postMessage({kind:KIND, direction:"armed", nonce:data.nonce}, location.origin);
  });
  globalThis[KEY] = Object.freeze({version:210});
})();