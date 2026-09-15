(() => {
  if (window.__CHAT2API_TERMINAL_INTEGRITY_V89__) return;
  window.__CHAT2API_TERMINAL_INTEGRITY_V89__ = true;

  const REVISION = 89;
  const runtime = chrome && chrome.runtime;
  if (!runtime || typeof runtime.sendMessage !== "function") return;

  const originalSendMessage = runtime.sendMessage.bind(runtime);
  const pending = new Map();

  function textOf(value) {
    return typeof value === "string" ? value.trim() : "";
  }

  function controllerCandidate() {
    try {
      const contract = window.__CHAT2API_REQUEST_CONTROLLER_V6__?.contract;
      if (!contract) return { text: "", source: "" };
      const state = typeof contract.currentAssistantState === "function" ? contract.currentAssistantState() : null;
      const finalNode = typeof contract.finalNodeText === "function" ? contract.finalNodeText() : null;
      const candidates = [
        { text: textOf(state?.text), source: "controller-state" },
        { text: textOf(finalNode?.text), source: "controller-final-node" },
      ].filter((item) => item.text);
      candidates.sort((a, b) => b.text.length - a.text.length);
      return candidates[0] || { text: "", source: "" };
    } catch (_) {
      return { text: "", source: "" };
    }
  }

  function compatibleUpgrade(base, candidate) {
    const current = textOf(base);
    const next = textOf(candidate);
    if (!next || next.length <= current.length) return false;
    if (!current) return true;
    // Terminal reconciliation must never replace one answer with a different
    // answer. It may only extend a prefix already observed from the same turn.
    return next.startsWith(current);
  }

  async function settle(entry) {
    let best = textOf(entry.message.text);
    let bestSource = "terminal-event";
    const initialChars = best.length;
    const samples = [];

    // A network SSE can finish a fraction before ChatGPT's rendered assistant
    // node receives its final text. Sample the request controller after the
    // network terminal event and only accept monotonic prefix extensions.
    for (let index = 0; index < 8; index += 1) {
      await new Promise((resolve) => setTimeout(resolve, 300));
      const candidate = controllerCandidate();
      if (candidate.text) samples.push({ source: candidate.source, chars: candidate.text.length });
      if (compatibleUpgrade(best, candidate.text)) {
        best = candidate.text;
        bestSource = candidate.source;
      }
    }

    const message = {
      ...entry.message,
      text: best,
      terminal_integrity_revision: REVISION,
      terminal_integrity_initial_chars: initialChars,
      terminal_integrity_final_chars: best.length,
      terminal_integrity_source: bestSource,
      terminal_integrity_upgraded: best.length > initialChars,
      terminal_integrity_samples: samples.slice(-4),
    };

    try {
      const result = await originalSendMessage(message);
      entry.resolve(result);
    } catch (error) {
      entry.reject(error);
    } finally {
      pending.delete(entry.requestId);
    }
  }

  runtime.sendMessage = function terminalIntegritySendMessage(message, ...args) {
    const isTerminal = message && message.type === "chat.completed" && textOf(message.request_id);
    // Preserve callback-style and extension-id overloads untouched. chat2api's
    // request owners use the Promise form for terminal events.
    if (!isTerminal || args.length) return originalSendMessage(message, ...args);

    const requestId = textOf(message.request_id);
    const existing = pending.get(requestId);
    if (existing) {
      const incoming = textOf(message.text);
      const current = textOf(existing.message.text);
      if (compatibleUpgrade(current, incoming)) existing.message = { ...existing.message, ...message };
      return existing.promise;
    }

    let resolvePromise;
    let rejectPromise;
    const promise = new Promise((resolve, reject) => {
      resolvePromise = resolve;
      rejectPromise = reject;
    });
    const entry = {
      requestId,
      message: { ...message },
      promise,
      resolve: resolvePromise,
      reject: rejectPromise,
    };
    pending.set(requestId, entry);
    settle(entry);
    return promise;
  };
})();
