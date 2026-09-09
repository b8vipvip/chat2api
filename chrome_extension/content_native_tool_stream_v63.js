(() => {
  const KEY = "__CHAT2API_NATIVE_TOOL_STREAM_CONTENT_V63__";
  if (globalThis[KEY]) return;

  const SOURCE = "chat2api-native-tool-stream-v63";
  const state = { revision: 63, calls: 0, results: 0, final_snapshots: 0, final_completions: 0, completions: 0, ignored: 0 };
  globalThis[KEY] = state;

  function activeRequest() {
    for (const key of ["__CHAT2API_REQUEST_CONTENT_V6__", "__CHAT2API_REQUEST_CONTENT_V5__"]) {
      const active = globalThis[key]?.active;
      if (active?.requestId && !active.cancelled) return active;
    }
    const fallback = globalThis.__CHAT2API_RESPONSE_STREAM_RECOVERY_V49__?.request;
    if (fallback?.requestId && !fallback.completed && !fallback.failed) return fallback;
    return null;
  }

  async function emit(event) {
    try {
      const result = await chrome.runtime.sendMessage({ type: "chat2api.event", event });
      return result?.ok !== false;
    } catch (_) {
      return false;
    }
  }

  function bounded(value, max = 100000) {
    const text = String(value || "");
    return text.length <= max ? text : text.slice(0, max) + "\n<chat2api-truncated>";
  }

  function cleanMetadata(value) {
    if (!value || typeof value !== "object") return {};
    try {
      const text = JSON.stringify(value);
      if (text.length <= 180000) return value;
      return { truncated: true, preview: text.slice(0, 180000) };
    } catch (_) {
      return {};
    }
  }

  async function handle(detail) {
    const phase = String(detail?.phase || "");
    if (["topic-subscribe", "topic-unsubscribe"].includes(phase)) return;
    const active = activeRequest();
    if (!active?.requestId) {
      state.ignored += 1;
      return;
    }
    const requestId = String(active.requestId);

    if (phase === "final-snapshot") {
      state.final_snapshots += 1;
      await emit({
        type: "chat.response.snapshot",
        request_id: requestId,
        text: bounded(detail.text, 1000000),
        message_id: String(detail.message_id || ""),
        status: String(detail.status || "in_progress"),
        native: true,
      });
      return;
    }

    if (phase === "final-complete") {
      state.final_completions += 1;
      await emit({
        type: "chat.response.completed",
        request_id: requestId,
        text: bounded(detail.text, 1000000),
        message_id: String(detail.message_id || ""),
        status: String(detail.status || "completed"),
        native: true,
      });
      return;
    }

    if (phase === "tool-call") {
      state.calls += 1;
      await emit({
        type: "chat.tool.call",
        request_id: requestId,
        tool_name: String(detail.tool_name || "tool"),
        call_id: String(detail.call_id || ""),
        arguments: bounded(detail.arguments),
        status: String(detail.status || "completed"),
        native: true,
        metadata: cleanMetadata(detail.metadata),
      });
      return;
    }

    if (phase === "tool-result") {
      state.results += 1;
      await emit({
        type: "chat.tool.result",
        request_id: requestId,
        tool_name: String(detail.tool_name || "tool"),
        call_id: String(detail.call_id || ""),
        result_id: String(detail.result_id || ""),
        output: bounded(detail.output),
        status: String(detail.status || "completed"),
        native: true,
        metadata: cleanMetadata(detail.metadata),
      });
      return;
    }

    if (phase === "turn-complete") {
      state.completions += 1;
      await emit({
        type: "chat.turn.completed",
        request_id: requestId,
        source: String(detail.completion_source || "native-tool-stream-v63"),
        native: true,
      });
    }
  }

  globalThis.addEventListener("message", event => {
    if (event.source !== globalThis) return;
    const detail = event.data;
    if (!detail || detail.source !== SOURCE) return;
    handle(detail).catch(() => {});
  });
})();
