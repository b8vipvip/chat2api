(() => {
  const MAX_BYTES = 4 * 1024 * 1024;
  const SAFE_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
  const SOURCE_HOSTS = new Set(["chatgpt.com", "www.chatgpt.com", "chat.openai.com"]);
  const CDN_SUFFIXES = [".oaiusercontent.com", ".oaistatic.com", ".openai.com", ".chatgpt.com"];

  function trustedUrl(value) {
    try {
      const url = new URL(String(value || ""));
      const host = url.hostname.toLowerCase();
      return url.protocol === "https:" && !url.username && !url.password &&
        (CDN_SUFFIXES.some(suffix => host.endsWith(suffix))
          || SOURCE_HOSTS.has(host)) ? url : null;
    } catch (_) { return null; }
  }

  // Bounded generated-file relay. The content tab remains the requesting
  // authority; the background has only read-only, allowlisted fetch rights.
  // It never follows redirects, sends credentials, or accepts arbitrary URLs.
  const MAX_FILE_RELAY_BYTES = 4 * 1024 * 1024;
  const FILE_MIMES = Object.freeze({
    pdf: "application/pdf",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    zip: "application/zip", csv: "text/csv", txt: "text/plain",
    md: "text/markdown", json: "application/json", py: "text/x-python",
    js: "text/javascript", ts: "text/plain", html: "text/html",
  });
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type !== "chat2api.file.fetch-v188") return false;
    let senderHost = "";
    try { senderHost = new URL(String(sender?.url || "")).hostname.toLowerCase(); } catch (_) {}
    const url = trustedUrl(message.url);
    const filename = String(message.filename || "");
    const extension = /^[^\\/\r\n]{1,140}\.([a-z0-9]{1,8})$/i.exec(filename)?.[1]?.toLowerCase();
    const expectedMime = extension && FILE_MIMES[extension];
    if (!SOURCE_HOSTS.has(senderHost) || !sender?.tab?.id || !url || !expectedMime) {
      sendResponse({ok: false, error: "untrusted-file-source"});
      return false;
    }
    (async () => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch(url.toString(), {
          method: "GET", credentials: "omit", redirect: "error", signal: controller.signal,
        });
        const declaredSize = Number(response.headers.get("content-length") || 0);
        const responseMime = String(response.headers.get("content-type") || "")
          .split(";")[0].trim().toLowerCase();
        if (!response.ok || !Number.isFinite(declaredSize)
            || declaredSize < 0 || declaredSize > MAX_FILE_RELAY_BYTES
            || (responseMime === "text/html" && extension !== "html")) {
          sendResponse({ok: false, error: "unusable-file-response"});
          return;
        }
        const reader = response.body?.getReader?.();
        if (!reader) {
          sendResponse({ok: false, error: "file-stream-unavailable"});
          return;
        }
        const chunks = [];
        let total = 0;
        try {
          while (true) {
            const {done, value} = await reader.read();
            if (done) break;
            total += value.byteLength;
            if (total > MAX_FILE_RELAY_BYTES) {
              sendResponse({ok: false, error: "file-size-limit"});
              return;
            }
            chunks.push(value);
          }
        } finally { try { await reader.cancel(); } catch (_) {} }
        if (!total) {
          sendResponse({ok: false, error: "file-empty"});
          return;
        }
        const binary = [];
        for (const chunk of chunks)
          for (let offset = 0; offset < chunk.length; offset += 16384)
            binary.push(String.fromCharCode(...chunk.subarray(offset, offset + 16384)));
        sendResponse({
          ok: true, bytes: total, mime: expectedMime,
          data_url: "data:" + expectedMime + ";base64," + btoa(binary.join("")),
        });
      } catch (_) {
        sendResponse({ok: false, error: "file-download-unavailable"});
      } finally { clearTimeout(timeout); }
    })();
    return true;
  });

  // P2 identity proof: a ChatGPT page can ask for the digest of a trusted
  // raster it is actually displaying. Never return bytes or source URLs;
  // never follow redirects or send cookies/credentials.
  // Originals are archived up to 20 MiB. This larger cap applies ONLY to
  // hashing (the byte relay to a page retains its existing 4 MiB limit).
  const MAX_IDENTITY_BYTES = 20 * 1024 * 1024;
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type !== "chat2api.media.hash-v2") return false;
    let senderHost = "";
    try { senderHost = new URL(String(sender?.url || "")).hostname.toLowerCase(); }
    catch (_) {}
    const url = trustedUrl(message.url);
    if (!SOURCE_HOSTS.has(senderHost) || !sender?.tab?.id || !url) {
      sendResponse({ok:false,error:"untrusted-image-identity-source"});
      return false;
    }
    (async () => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 6500);
      try {
        const response = await fetch(url.toString(), {
          method:"GET", credentials:"omit", redirect:"error", signal:controller.signal,
        });
        const size = Number(response.headers.get("content-length") || 0);
        const mime = String(response.headers.get("content-type") || "")
          .split(";")[0].trim().toLowerCase();
        if (!response.ok || !SAFE_TYPES.has(mime) || !Number.isFinite(size)
            || size < 0 || size > MAX_IDENTITY_BYTES)
          throw new Error("unusable-raster");
        // Content-Length may be missing or dishonest. Cap the *actual* body
        // while streaming, before allocating the digest buffer, so large
        // untrusted CDN responses cannot be pulled into memory without bound.
        let bytes;
        const reader=response.body?.getReader?.();
        if (reader) {
          const chunks=[];
          let total=0, nativeEof=false;
          try {
            while (true) {
              const {done,value}=await reader.read();
              if (done) { nativeEof=true; break; }
              total+=value.byteLength;
              if (total > MAX_IDENTITY_BYTES) throw new Error("raster-size-limit");
              chunks.push(value);
            }
          } finally {
            // The original v637 code awaited cancel() even AFTER a clean EOF,
            // which can make an otherwise successful SHA result never ACK.
            // On overflow/read failure, only cancel this auxiliary image
            // reader, and never wait for a possibly unresolved cancellation
            // Promise. Do not touch the native ChatGPT conversation fetch.
            if(!nativeEof) {
              try {
                const cancellation=reader.cancel?.();
                cancellation?.catch?.(()=>{});
              } catch (_) {}
            }
            try { reader.releaseLock?.(); } catch (_) {}
          }
          if (!total) throw new Error("raster-empty");
          bytes=new Uint8Array(total);
          let offset=0;
          for(const part of chunks){ bytes.set(part,offset);offset+=part.byteLength; }
        } else {
          // Legacy fetch mocks and unusual browser responses have no stream.
          // Never download unknown-size bodies in this fallback path.
          if (!size) throw new Error("raster-size-unknown");
          bytes=new Uint8Array(await response.arrayBuffer());
          if (!bytes.byteLength || bytes.byteLength>MAX_IDENTITY_BYTES)
            throw new Error("raster-size-limit");
        }
        const digest = new Uint8Array(await crypto.subtle.digest("SHA-256",bytes));
        sendResponse({ok:true,sha256:[...digest].map(x=>x.toString(16).padStart(2,"0")).join(""),
                      bytes:bytes.byteLength});
      } catch (_) { sendResponse({ok:false,error:"identity-bytes-unavailable"}); }
      finally { clearTimeout(timer); }
    })();
    return true;
  });

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type !== "chat2api.media.fetch-v175") return false;
    // This is a narrow, read-only media transport, not a general URL proxy.
    // Only a live ChatGPT content tab can request approved OpenAI/CDN images.
    let senderHost = "";
    try { senderHost = new URL(String(sender?.url || "")).hostname.toLowerCase(); } catch (_) {}
    const url = trustedUrl(message.url);
    if (!SOURCE_HOSTS.has(senderHost) || !sender?.tab?.id || !url) {
      sendResponse({ ok: false, error: "untrusted-image-source" });
      return false;
    }
    (async () => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5500);
      try {
        const response = await fetch(url.toString(), {
          method: "GET",
          credentials: "omit",
          redirect: "error",
          signal: controller.signal,
        });
        const length = Number(response.headers.get("content-length") || 0);
        const type = String(response.headers.get("content-type") || "").split(";")[0].trim().toLowerCase();
        if (!response.ok || !SAFE_TYPES.has(type) || length > MAX_BYTES) {
          sendResponse({ ok: false, error: "unusable-image-response" });
          return;
        }
        const blob = await response.blob();
        if (!blob.size || blob.size > MAX_BYTES || !SAFE_TYPES.has(blob.type.toLowerCase())) {
          sendResponse({ ok: false, error: "image-size-or-format" });
          return;
        }
        const binary = new Uint8Array(await blob.arrayBuffer());
        const chunks = [];
        for (let index = 0; index < binary.length; index += 16384) {
          chunks.push(String.fromCharCode(...binary.subarray(index, index + 16384)));
        }
        sendResponse({
          ok: true,
          data_url: "data:" + blob.type.toLowerCase() + ";base64," + btoa(chunks.join("")),
          bytes: blob.size,
        });
      } catch (_) {
        sendResponse({ ok: false, error: "image-download-unavailable" });
      } finally {
        clearTimeout(timeout);
      }
    })();
    return true;
  });

})();
