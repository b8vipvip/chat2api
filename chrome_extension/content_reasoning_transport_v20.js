(() => {
  const KEY = "__CHAT2API_REASONING_TRANSPORT_V20__";
  if (globalThis[KEY]) return;
  globalThis[KEY] = true;

  const baseSendMessage = chrome.runtime.sendMessage.bind(chrome.runtime);
  chrome.runtime.sendMessage = function chat2apiReasoningTransport(message, ...rest) {
    const event = message?.type === "chat2api.event" ? message.event : null;
    if (event?.type === "chat.status" && event.request_id) {
      return baseSendMessage({
        type: "chat2api.event",
        event: {
          type: "chat.diagnostics",
          request_id: event.request_id,
          diagnostics: {
            visible_reasoning_status: String(event.text || "").slice(0, 240),
            visible_reasoning_status_kind: String(event.status || "working").slice(0, 40),
            visible_reasoning_source: String(event.source || "visible-chatgpt-ui-v20").slice(0, 80),
          },
        },
      }, ...rest);
    }
    return baseSendMessage(message, ...rest);
  };
})();
