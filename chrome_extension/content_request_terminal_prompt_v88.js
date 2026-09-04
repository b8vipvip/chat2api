(() => {
  const KEY = "__CHAT2API_REQUEST_TERMINAL_PROMPT_V88__";
  if (globalThis[KEY]) return;

  const REQUEST_KEY = "__CHAT2API_REQUEST_CONTENT_V5__";
  const LONG_PROMPT_THRESHOLD = 2048;
  const state = {
    revision: 88,
    fast_insert_count: 0,
    fast_insert_chars: 0,
    suppressed_secondary_terminals: 0,
    last_fast_insert_ms: 0,
    last_fast_insert_chars: 0,
  };
  globalThis[KEY] = state;

  const nativeExecCommand = typeof document.execCommand === "function"
    ? document.execCommand.bind(document)
    : null;

  function activeRequest() {
    return globalThis[REQUEST_KEY]?.active || null;
  }

  function activeEditable() {
    const active = document.activeElement;
    return active instanceof HTMLElement && active.isContentEditable ? active : null;
  }

  function reportFastInsert(chars, elapsedMs) {
    const active = activeRequest();
    const requestId = String(active?.requestId || "");
    if (!requestId) return;
    try {
      chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "chat.diagnostics",
          request_id: requestId,
          stage: "prompt-fast-insert",
          diagnostics: {
            prompt_fast_insert_revision: 88,
            prompt_fast_insert_method: "direct-text-node+input-event",
            prompt_fast_insert_chars: chars,
            prompt_fast_insert_ms: Math.round(elapsedMs * 10) / 10,
          },
        },
      }).catch?.(() => {});
    } catch (_) {}
  }

  // ChatGPT's Lexical/contenteditable surface can block the page for minutes when
  // execCommand("insertText") is handed a multi-kilobyte prompt. request-v6 emits
  // a normal InputEvent immediately after this call, so replace only the expensive
  // bulk DOM insertion while preserving the existing controller's ownership,
  // validation and submit path.
  if (nativeExecCommand) {
    document.execCommand = function chat2apiExecCommandV88(command, showUi, value) {
      const cmd = String(command || "").toLowerCase();
      const text = typeof value === "string" ? value : "";
      const editable = activeEditable();
      if (cmd === "inserttext" && editable && text.length >= LONG_PROMPT_THRESHOLD) {
        const started = performance.now();
        try {
          editable.replaceChildren(document.createTextNode(text));
          const selection = globalThis.getSelection?.();
          if (selection) {
            const range = document.createRange();
            range.selectNodeContents(editable);
            range.collapse(false);
            selection.removeAllRanges();
            selection.addRange(range);
          }
          const elapsed = performance.now() - started;
          state.fast_insert_count += 1;
          state.fast_insert_chars += text.length;
          state.last_fast_insert_ms = elapsed;
          state.last_fast_insert_chars = text.length;
          queueMicrotask(() => reportFastInsert(text.length, elapsed));
          return true;
        } catch (_) {
          // Fall back to the browser implementation only if the bounded direct
          // mutation itself failed. Ordinary/short writes are untouched.
        }
      }
      return nativeExecCommand(command, showUi, value);
    };
  }

  const nativeSendMessage = chrome.runtime.sendMessage.bind(chrome.runtime);
  chrome.runtime.sendMessage = function chat2apiSendMessageV88(message, ...args) {
    const event = message?.type === "chat2api.event" ? message.event : null;
    const active = activeRequest();
    const requestId = String(event?.request_id || "");
    const sameRequest = Boolean(requestId && String(active?.requestId || "") === requestId);
    const successAlreadyOwnedByNetwork = Boolean(sameRequest && active?.networkCompleted === true);
    const secondaryFailure = event?.type === "chat.cancelled" || event?.type === "chat.error";

    if (successAlreadyOwnedByNetwork && secondaryFailure) {
      state.suppressed_secondary_terminals += 1;
      const callback = args.find(value => typeof value === "function");
      const result = {
        ok: true,
        suppressed: true,
        reason: "network-success-is-terminal-v88",
      };
      if (callback) {
        queueMicrotask(() => callback(result));
        return undefined;
      }
      return Promise.resolve(result);
    }
    return nativeSendMessage(message, ...args);
  };
})();
