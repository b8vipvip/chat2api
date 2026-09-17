(() => {
  const KEY = "__CHAT2API_REQUEST_TERMINAL_PROMPT_V88__";
  if (globalThis[KEY]) return;

  const REQUEST_KEY = "__CHAT2API_REQUEST_CONTENT_V5__";
  const LONG_PROMPT_THRESHOLD = 2048;
  const state = {
    revision: 90,
    role: "prompt-insertion-only",
    terminal_authority: "request-v6",
    fast_insert_count: 0,
    fast_insert_chars: 0,
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
            prompt_fast_insert_revision: 90,
            prompt_fast_insert_method: "direct-text-node+input-event",
            prompt_fast_insert_chars: chars,
            prompt_fast_insert_ms: Math.round(elapsedMs * 10) / 10,
            terminal_authority: "request-v6",
          },
        },
      }).catch?.(() => {});
    } catch (_) {}
  }

  if (nativeExecCommand) {
    document.execCommand = function chat2apiExecCommandV90(command, showUi, value) {
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
        } catch (_) {}
      }
      return nativeExecCommand(command, showUi, value);
    };
  }
})();
