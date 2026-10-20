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
