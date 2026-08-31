(() => {
  const KEY = "__CHAT2API_NETWORK_STREAM_MAIN_V55__";
  if (globalThis[KEY]) return;

  const SOURCE = "chat2api-network-stream-v55";
  const CHANNEL = "conversation-fetch-v55";
  const decoderFactory = () => new TextDecoder("utf-8");
  const state = {
    version: 55,
    streams: 0,
    snapshots: 0,
    completions: 0,
    parse_errors: 0,
  };
  globalThis[KEY] = state;
  try {
    document.documentElement?.setAttribute?.("data-chat2api-network-stream-main-v55", "55");
  } catch (_) {}

  const nativeFetch = globalThis.fetch?.bind(globalThis);
  if (typeof nativeFetch !== "function") return;

  function post(detail) {
    try {
      globalThis.postMessage({ source: SOURCE, ...detail }, "*");
    } catch (_) {}
  }

  function conversationRequest(input, init) {
    try {
      const method = String(init?.method || input?.method || "GET").toUpperCase();
      const rawUrl = typeof input === "string" ? input : (input?.url || "");
      const url = new URL(rawUrl, location.href);
      return method === "POST"
        && ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(url.hostname)
        && url.pathname === "/backend-api/f/conversation";
    } catch (_) {
      return false;
    }
  }

  function contentType(response) {
    try { return String(response.headers?.get?.("content-type") || "").toLowerCase(); }
    catch (_) { return ""; }
  }

  function textFromContent(content) {
    if (typeof content === "string") return content;
    if (!content || typeof content !== "object") return "";
    if (typeof content.text === "string") return content.text;
    if (Array.isArray(content.parts)) {
      return content.parts.map(part => {
        if (typeof part === "string") return part;
        if (!part || typeof part !== "object") return "";
        if (typeof part.text === "string") return part.text;
        if (typeof part.content === "string") return part.content;
        if (typeof part.value === "string") return part.value;
        return "";
      }).join("");
    }
    if (typeof content.result === "string") return content.result;
    return "";
  }

  function assistantText(message) {
    if (!message || typeof message !== "object") return "";
    const role = String(message.author?.role || message.role || "").toLowerCase();
    if (role !== "assistant") return "";
    return textFromContent(message.content) || (typeof message.text === "string" ? message.text : "");
  }

  function collectAssistantTexts(node, output = [], depth = 0) {
    if (depth > 14 || node == null) return output;
    if (Array.isArray(node)) {
      for (const value of node) collectAssistantTexts(value, output, depth + 1);
      return output;
    }
    if (typeof node !== "object") return output;
    const direct = assistantText(node);
    if (direct) output.push(direct);
    for (const value of Object.values(node)) collectAssistantTexts(value, output, depth + 1);
    return output;
  }

  function pointerParts(pointer) {
    if (pointer == null || pointer === "") return [];
    if (typeof pointer !== "string" || !pointer.startsWith("/")) return null;
    return pointer.slice(1).split("/").map(part => part.replace(/~1/g, "/").replace(/~0/g, "~"));
  }

  function assignPatch(rootBox, patch) {
    const parts = pointerParts(patch?.p);
    if (!parts) return false;
    const op = String(patch?.o || patch?.op || "replace").toLowerCase();
    const value = patch?.v !== undefined ? patch.v : patch?.value;
    if (!parts.length) {
      if (op === "append" && typeof rootBox.value === "string") {
        rootBox.value += String(value ?? "");
      } else if (op === "append" && Array.isArray(rootBox.value)) {
        rootBox.value.push(value);
      } else {
        rootBox.value = value;
      }
      return true;
    }
    if (!rootBox.value || typeof rootBox.value !== "object") rootBox.value = {};
    let cursor = rootBox.value;
    for (let index = 0; index < parts.length - 1; index += 1) {
      const key = parts[index];
      const nextKey = parts[index + 1];
      if (cursor[key] == null || typeof cursor[key] !== "object") {
        cursor[key] = /^\d+$/.test(nextKey) ? [] : {};
      }
      cursor = cursor[key];
    }
    const key = parts[parts.length - 1];
    if (op === "append") {
      if (typeof cursor[key] === "string") cursor[key] += String(value ?? "");
      else if (Array.isArray(cursor[key])) cursor[key].push(value);
      else if (cursor[key] == null) cursor[key] = value;
      else return false;
    } else if (op === "remove") {
      if (Array.isArray(cursor) && /^\d+$/.test(key)) cursor.splice(Number(key), 1);
      else delete cursor[key];
    } else if (Array.isArray(cursor) && key === "-") {
      cursor.push(value);
    } else {
      cursor[key] = value;
    }
    return true;
  }

  function patchCandidates(node, output = [], depth = 0) {
    if (depth > 10 || node == null) return output;
    if (Array.isArray(node)) {
      for (const value of node) patchCandidates(value, output, depth + 1);
      return output;
    }
    if (typeof node !== "object") return output;
    if (
      typeof node.p === "string"
      && (Object.prototype.hasOwnProperty.call(node, "v") || Object.prototype.hasOwnProperty.call(node, "value"))
      && (node.o || node.op)
    ) output.push(node);
    for (const value of Object.values(node)) patchCandidates(value, output, depth + 1);
    return output;
  }

  function completionHint(node, depth = 0) {
    if (depth > 12 || node == null) return false;
    if (Array.isArray(node)) return node.some(value => completionHint(value, depth + 1));
    if (typeof node !== "object") return false;
    const type = String(node.type || node.event || node.status || "").toLowerCase();
    if (
      type === "finished_successfully"
      || type === "completed"
      || type === "message_end"
      || type === "response.completed"
      || type === "conversation.item.done"
      || type === "done"
    ) return true;
    if (node.finished_successfully === true || node.is_completion === true || node.end_turn === true) return true;
    return Object.values(node).some(value => completionHint(value, depth + 1));
  }

  function streamParser(streamId) {
    let buffer = "";
    let sequence = 0;
    let latestText = "";
    let patchRoot = { value: null };
    let sawDone = false;

    function emitSnapshot(text, source) {
      const normalized = String(text || "");
      if (!normalized || normalized === latestText) return;
      latestText = normalized;
      state.snapshots += 1;
      post({
        channel: CHANNEL,
        phase: "assistant-snapshot",
        stream_id: streamId,
        sequence: ++sequence,
        text: normalized,
        chars: normalized.length,
        parser_source: source,
      });
    }

    function inspectPayload(payload) {
      const direct = collectAssistantTexts(payload, []);
      if (direct.length) {
        if (patchRoot.value == null) {
          try { patchRoot.value = structuredClone(payload); }
          catch (_) { patchRoot.value = payload; }
        }
        emitSnapshot(direct[direct.length - 1], "assistant-message");
      }

      const patches = patchCandidates(payload, []);
      let patched = false;
      for (const patch of patches) patched = assignPatch(patchRoot, patch) || patched;
      if (patched) {
        const recovered = collectAssistantTexts(patchRoot.value, []);
        if (recovered.length) emitSnapshot(recovered[recovered.length - 1], "json-patch");
      }
      if (completionHint(payload)) sawDone = true;
    }

    function processEvent(raw) {
      const lines = raw.split(/\r?\n/);
      const dataLines = lines
        .filter(line => line.startsWith("data:"))
        .map(line => line.slice(5).replace(/^ /, ""));
      if (!dataLines.length) return;
      const data = dataLines.join("\n").trim();
      if (!data) return;
      if (data === "[DONE]") {
        sawDone = true;
        return;
      }
      try {
        inspectPayload(JSON.parse(data));
      } catch (_) {
        state.parse_errors += 1;
      }
    }

    return {
      push(text) {
        buffer += text;
        const blocks = buffer.split(/\r?\n\r?\n/);
        buffer = blocks.pop() || "";
        for (const block of blocks) processEvent(block);
      },
      finish() {
        if (buffer.trim()) processEvent(buffer);
        if (latestText) {
          state.completions += 1;
          post({
            channel: CHANNEL,
            phase: "assistant-complete",
            stream_id: streamId,
            sequence: ++sequence,
            text: latestText,
            chars: latestText.length,
            completion_hint: sawDone,
          });
        }
        return { latestText, sawDone, sequence };
      },
    };
  }

  async function observeConversation(response, streamId) {
    const type = contentType(response);
    const eventStream = response.ok && type.includes("text/event-stream");
    post({
      channel: CHANNEL,
      phase: "response",
      stream_id: streamId,
      status: Number(response.status || 0),
      ok: Boolean(response.ok),
      event_stream: eventStream,
      content_type: type.slice(0, 120),
    });
    if (!eventStream || !response.body) return;

    let clone;
    try { clone = response.clone(); } catch (_) { return; }
    const reader = clone.body?.getReader?.();
    if (!reader) return;
    const decoder = decoderFactory();
    const parser = streamParser(streamId);
    let chunks = 0;
    let bytes = 0;

    try {
      while (true) {
        const result = await reader.read();
        if (result.done) break;
        const value = result.value;
        const byteLength = Number(value?.byteLength || value?.length || 0);
        chunks += 1;
        bytes += byteLength;
        parser.push(decoder.decode(value, { stream: true }));
        post({
          channel: CHANNEL,
          phase: "chunk",
          stream_id: streamId,
          sequence: chunks,
          chunks,
          bytes,
        });
      }
      parser.push(decoder.decode());
      const parsed = parser.finish();
      post({
        channel: CHANNEL,
        phase: "done",
        stream_id: streamId,
        sequence: Math.max(chunks, parsed.sequence || 0) + 1,
        chunks,
        bytes,
        assistant_chars: String(parsed.latestText || "").length,
        completion_hint: Boolean(parsed.sawDone),
      });
    } catch (error) {
      post({
        channel: CHANNEL,
        phase: "error",
        stream_id: streamId,
        chunks,
        bytes,
        error: String(error?.message || error).slice(0, 240),
      });
    } finally {
      try { reader.releaseLock(); } catch (_) {}
    }
  }

  globalThis.fetch = async function chat2apiConversationFetchV55(input, init) {
    const watched = conversationRequest(input, init);
    const response = await nativeFetch(input, init);
    if (!watched) return response;
    const streamId = `${Date.now().toString(36)}-${++state.streams}`;
    observeConversation(response, streamId).catch(() => {});
    return response;
  };
})();