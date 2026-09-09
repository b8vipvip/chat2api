(() => {
  const KEY = "__CHAT2API_NATIVE_TOOL_STREAM_MAIN_V63__";
  if (globalThis[KEY]) return;

  const SOURCE = "chat2api-native-tool-stream-v63";
  const REVISION = 63;
  const trackedTopics = new Set();
  const topicState = new Map();
  const emitted = new Map();
  const state = {
    revision: REVISION,
    tracked_topics: trackedTopics,
    inbound_frames: 0,
    decoded_items: 0,
    tool_calls: 0,
    tool_results: 0,
    final_snapshots: 0,
    final_completions: 0,
    turn_completions: 0,
    parse_errors: 0,
  };
  globalThis[KEY] = state;
  try {
    document.documentElement?.setAttribute?.("data-chat2api-native-tool-stream", String(REVISION));
  } catch (_) {}

  const NativeWebSocket = globalThis.WebSocket;
  const nativeSend = NativeWebSocket?.prototype?.send;
  const messageDataDescriptor = Object.getOwnPropertyDescriptor(MessageEvent.prototype, "data");
  if (typeof NativeWebSocket !== "function" || typeof nativeSend !== "function" || !messageDataDescriptor?.get) return;

  function post(detail) {
    try {
      globalThis.postMessage({ source: SOURCE, revision: REVISION, ...detail }, "*");
    } catch (_) {}
  }

  function json(value) {
    try { return JSON.parse(String(value || "")); } catch (_) { return null; }
  }

  function pointerParts(pointer) {
    if (pointer == null || pointer === "") return [];
    if (typeof pointer !== "string" || !pointer.startsWith("/")) return null;
    return pointer.slice(1).split("/").map(part => part.replace(/~1/g, "/").replace(/~0/g, "~"));
  }

  function applyPatch(rootBox, patch) {
    const pointer = patch?.p !== undefined ? patch.p : patch?.path;
    const parts = pointerParts(pointer);
    if (!parts) return false;
    const op = String(patch?.o || patch?.op || "replace").toLowerCase();
    const value = patch?.v !== undefined ? patch.v : patch?.value;
    if (!parts.length) {
      if (op === "append" && typeof rootBox.value === "string") rootBox.value += String(value ?? "");
      else if (op === "append" && Array.isArray(rootBox.value)) rootBox.value.push(value);
      else if (op === "remove") rootBox.value = null;
      else rootBox.value = value;
      return true;
    }
    if (!rootBox.value || typeof rootBox.value !== "object") rootBox.value = {};
    let cursor = rootBox.value;
    for (let index = 0; index < parts.length - 1; index += 1) {
      const key = parts[index];
      const nextKey = parts[index + 1];
      if (cursor[key] == null || typeof cursor[key] !== "object") cursor[key] = /^\d+$/.test(nextKey) ? [] : {};
      cursor = cursor[key];
    }
    const key = parts.at(-1);
    if (op === "append") {
      if (typeof cursor[key] === "string") cursor[key] += String(value ?? "");
      else if (Array.isArray(cursor[key])) cursor[key].push(value);
      else if (cursor[key] == null) cursor[key] = value;
      else if (typeof value === "string") cursor[key] = String(cursor[key] ?? "") + value;
      else return false;
    } else if (op === "remove") {
      if (Array.isArray(cursor) && /^\d+$/.test(key)) cursor.splice(Number(key), 1);
      else delete cursor[key];
    } else if (Array.isArray(cursor) && key === "-") cursor.push(value);
    else cursor[key] = value;
    return true;
  }

  function contentText(content) {
    if (typeof content === "string") return content;
    if (!content || typeof content !== "object") return "";
    if (typeof content.text === "string") return content.text;
    if (Array.isArray(content.parts)) return content.parts.map(item => typeof item === "string" ? item : contentText(item)).join("");
    return "";
  }

  function toolNameForMessage(message) {
    const role = String(message?.author?.role || message?.role || "").toLowerCase();
    if (role === "tool") return String(message?.author?.name || message?.name || message?.metadata?.real_author || "tool").replace(/^tool:/, "");
    const recipient = String(message?.recipient || "").trim();
    if (role === "assistant" && recipient && !["all", "assistant", "web"].includes(recipient)) return recipient;
    return "";
  }

  function safeToolMetadata(metadata) {
    if (!metadata || typeof metadata !== "object") return {};
    const result = {};
    for (const key of ["search_model_queries", "search_result_groups", "citations", "content_references", "tool_icons", "reasoning_title", "reasoning_status"]) {
      if (metadata[key] !== undefined) result[key] = metadata[key];
    }
    return result;
  }

  function signature(message, kind) {
    const text = contentText(message?.content);
    return [kind, message?.id || "", message?.status || "", text.length, text.slice(-120)].join(":");
  }

  function maybeEmitFinal(message) {
    if (!message || typeof message !== "object") return;
    const role = String(message?.author?.role || message?.role || "").toLowerCase();
    const channel = String(message?.channel || "").toLowerCase();
    if (role !== "assistant" || channel !== "final") return;
    const text = contentText(message.content);
    if (!text) return;
    const status = String(message.status || "");
    const key = `final:${message.id || ""}:${status}:${text.length}:${text.slice(-120)}`;
    if (emitted.get(`final:${message.id || ""}`) === key) return;
    emitted.set(`final:${message.id || ""}`, key);
    state.final_snapshots += 1;
    post({
      phase: "final-snapshot",
      message_id: String(message.id || ""),
      text,
      status: status || "in_progress",
    });
    if (["finished_successfully", "completed"].includes(status) || message.end_turn === true) {
      state.final_completions += 1;
      post({
        phase: "final-complete",
        message_id: String(message.id || ""),
        text,
        status: status || "completed",
      });
    }
  }

  function maybeEmitTool(message) {
    if (!message || typeof message !== "object") return;
    const role = String(message?.author?.role || message?.role || "").toLowerCase();
    const name = toolNameForMessage(message);
    if (!name) return;
    const status = String(message.status || "");
    const text = contentText(message.content);
    const metadata = safeToolMetadata(message.metadata);

    if (role === "assistant") {
      if (!text || !["finished_successfully", "completed"].includes(status)) return;
      const key = signature(message, "call");
      if (emitted.get(message.id) === key) return;
      emitted.set(message.id, key);
      state.tool_calls += 1;
      post({
        phase: "tool-call",
        tool_name: name,
        call_id: String(message.id || ""),
        arguments: text,
        status: status || "completed",
        metadata,
      });
      return;
    }

    if (role === "tool") {
      if (!["finished_successfully", "completed"].includes(status)) return;
      const key = signature(message, "result") + JSON.stringify(metadata).length;
      if (emitted.get(message.id) === key) return;
      emitted.set(message.id, key);
      state.tool_results += 1;
      post({
        phase: "tool-result",
        tool_name: name,
        call_id: String(message?.metadata?.parent_id || message?.parent_id || ""),
        result_id: String(message.id || ""),
        output: text,
        status: status || "completed",
        metadata,
      });
    }
  }

  function inspectRoot(topicId) {
    const root = topicState.get(topicId)?.root?.value;
    const message = root?.message || root?.v?.message || null;
    if (message) {
      maybeEmitTool(message);
      maybeEmitFinal(message);
    }
  }

  function applyDelta(topicId, payload) {
    if (!payload || typeof payload !== "object") return;
    let row = topicState.get(topicId);
    if (!row) {
      row = { root: { value: null } };
      topicState.set(topicId, row);
    }
    const directMessage = payload?.v?.message || payload?.message || null;
    if (directMessage) {
      row.root.value = { ...(payload.v || {}), message: directMessage };
      maybeEmitTool(directMessage);
      maybeEmitFinal(directMessage);
    }
    if (payload.o === "patch" && Array.isArray(payload.v)) {
      for (const patch of payload.v) applyPatch(row.root, patch);
      inspectRoot(topicId);
      return;
    }
    if ((payload.p !== undefined || payload.path !== undefined) && (payload.o || payload.op)) {
      applyPatch(row.root, payload);
      inspectRoot(topicId);
    }
  }

  function processNestedSse(topicId, encoded) {
    const blocks = String(encoded || "").split(/\r?\n\r?\n/);
    for (const block of blocks) {
      const dataLines = block.split(/\r?\n/).filter(line => line.startsWith("data:")).map(line => line.slice(5).replace(/^ /, ""));
      if (!dataLines.length) continue;
      const raw = dataLines.join("\n").trim();
      if (!raw || raw === "[DONE]") continue;
      const payload = json(raw);
      if (typeof payload === "string" || payload == null) continue;
      state.decoded_items += 1;
      applyDelta(topicId, payload);
    }
  }

  function processTurnStream(topicId, payload) {
    if (!payload || typeof payload !== "object") return;
    if (payload.type === "stream-item" && typeof payload.encoded_item === "string") {
      processNestedSse(topicId, payload.encoded_item);
      return;
    }
    if (payload.type === "done") {
      state.turn_completions += 1;
      post({ phase: "turn-complete", completion_source: "conversation-turn-stream-done" });
    }
  }

  function walkInbound(node, inheritedTopic = "") {
    if (Array.isArray(node)) {
      for (const item of node) walkInbound(item, inheritedTopic);
      return;
    }
    if (!node || typeof node !== "object") return;
    const topicId = String(node.topic_id || inheritedTopic || "");

    if (node.type === "message" && node.payload?.type === "conversation-turn-stream") {
      if (!trackedTopics.size || trackedTopics.has(topicId)) processTurnStream(topicId, node.payload?.payload);
    }
    if (node.type === "message" && node.payload?.type === "conversation-turn-complete") {
      state.turn_completions += 1;
      post({ phase: "turn-complete", completion_source: "conversation-turn-complete" });
    }
    if (node.reply?.type === "subscribe" && Array.isArray(node.reply.catchups)) {
      for (const item of node.reply.catchups) walkInbound(item, String(node.reply.topic_id || topicId));
    }
    for (const [key, value] of Object.entries(node)) {
      if (["payload", "reply"].includes(key)) continue;
      if (value && typeof value === "object") walkInbound(value, topicId);
    }
  }

  function inspectOutbound(data) {
    if (typeof data !== "string") return;
    const frame = json(data);
    if (!frame) return;
    const rows = Array.isArray(frame) ? frame : [frame];
    for (const row of rows) {
      const command = row?.command;
      const topicId = String(command?.topic_id || "");
      if (command?.type === "subscribe" && topicId.startsWith("conversation-turn-")) {
        trackedTopics.add(topicId);
        post({ phase: "topic-subscribe" });
      } else if (command?.type === "unsubscribe" && topicId.startsWith("conversation-turn-")) {
        trackedTopics.delete(topicId);
        topicState.delete(topicId);
        post({ phase: "topic-unsubscribe" });
      }
    }
  }

  NativeWebSocket.prototype.send = function chat2apiNativeToolSendV63(data) {
    try { inspectOutbound(data); } catch (_) { state.parse_errors += 1; }
    return nativeSend.call(this, data);
  };

  if (messageDataDescriptor.configurable !== false) {
    try {
      Object.defineProperty(MessageEvent.prototype, "data", {
        configurable: messageDataDescriptor.configurable,
        enumerable: messageDataDescriptor.enumerable,
        get() {
          const value = messageDataDescriptor.get.call(this);
          try {
            const target = this.currentTarget || this.target;
            if (target instanceof NativeWebSocket && typeof value === "string") {
              state.inbound_frames += 1;
              const frame = json(value);
              if (frame) walkInbound(frame);
            }
          } catch (_) {
            state.parse_errors += 1;
          }
          return value;
        },
      });
    } catch (_) {}
  }
})();
