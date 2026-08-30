(() => {
  const KEY = "__CHAT2API_NETWORK_STREAM_MAIN_V54__";
  if (globalThis[KEY]) return;

  const SOURCE = "chat2api-network-stream-v54";
  const EVENT = "chat2api.network-stream.v54";
  const READY_ATTR = "data-chat2api-network-stream-main-v54";
  const nativeFetch = globalThis.fetch;
  if (typeof nativeFetch !== "function") return;

  const state = {
    version: 54,
    streams: 0,
    chunks: 0,
    bytes: 0,
  };
  globalThis[KEY] = state;

  function markReady(attempt = 0) {
    const root = document.documentElement;
    if (root?.setAttribute) {
      try { root.setAttribute(READY_ATTR, "54"); } catch (_) {}
      return;
    }
    if (attempt < 20) setTimeout(() => markReady(attempt + 1), 0);
  }
  markReady();

  function requestMeta(input, init) {
    try {
      const request = typeof Request !== "undefined" && input instanceof Request ? input : null;
      const rawUrl = request?.url || (typeof input === "string" || input instanceof URL ? String(input) : "");
      const url = new URL(rawUrl, location.href);
      const method = String(init?.method || request?.method || "GET").toUpperCase();
      return { url, method };
    } catch (_) {
      return null;
    }
  }

  function isConversationStream(meta) {
    if (!meta || meta.method !== "POST") return false;
    if (!["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(meta.url.hostname)) return false;
    return meta.url.pathname === "/backend-api/f/conversation";
  }

  function emit(detail) {
    try {
      window.postMessage({
        source: SOURCE,
        type: EVENT,
        ...detail,
      }, location.origin);
    } catch (_) {}
  }

  async function consumeClone(response, streamId) {
    let reader = null;
    let totalBytes = 0;
    let sequence = 0;
    let lastEmitAt = 0;
    try {
      const clone = response.clone();
      reader = clone.body?.getReader?.() || null;
      if (!reader) {
        emit({ phase: "unavailable", stream_id: streamId });
        return;
      }
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunkBytes = Number(value?.byteLength || value?.length || 0);
        if (chunkBytes <= 0) continue;
        sequence += 1;
        totalBytes += chunkBytes;
        state.chunks += 1;
        state.bytes += chunkBytes;
        const now = Date.now();
        // Keep the signal cheap. We need liveness, not the response body itself.
        if (sequence === 1 || now - lastEmitAt >= 750) {
          lastEmitAt = now;
          emit({
            phase: "chunk",
            stream_id: streamId,
            sequence,
            chunk_bytes: chunkBytes,
            total_bytes: totalBytes,
          });
        }
      }
      emit({
        phase: "done",
        stream_id: streamId,
        sequence,
        total_bytes: totalBytes,
      });
    } catch (error) {
      emit({
        phase: "error",
        stream_id: streamId,
        sequence,
        total_bytes: totalBytes,
        error: String(error?.name || "stream-read-error").slice(0, 80),
      });
    } finally {
      try { reader?.releaseLock?.(); } catch (_) {}
    }
  }

  globalThis.fetch = async function chat2apiFetchV54(...args) {
    const meta = requestMeta(args[0], args[1]);
    const track = isConversationStream(meta);
    const response = await Reflect.apply(nativeFetch, globalThis, args);
    if (!track) return response;

    const contentType = String(response.headers?.get?.("content-type") || "").toLowerCase();
    const streamId = `${Date.now().toString(36)}-${(++state.streams).toString(36)}`;
    emit({
      phase: "response",
      stream_id: streamId,
      status: Number(response.status || 0),
      ok: Boolean(response.ok),
      event_stream: contentType.includes("text/event-stream"),
    });

    if (response.ok && contentType.includes("text/event-stream")) {
      consumeClone(response, streamId).catch(() => {});
    }
    return response;
  };
})();
