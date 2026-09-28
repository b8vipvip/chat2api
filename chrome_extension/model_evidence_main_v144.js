(() => {
  const KEY = "__CHAT2API_MODEL_EVIDENCE_MAIN_V144__";
  if (globalThis[KEY]) return;

  const SOURCE = "chat2api-model-evidence-v144";
  const AUTHORITY_SOURCE = "chat2api-model-authority-v144";
  const MAX_DEPTH = 14;
  const MAX_BUFFER = 16 * 1024 * 1024;
  const aliases = Object.freeze({
    "gpt-5.6-sol-wm": "gpt-5.6-sol",
    "gpt-5.6-terra-wm": "gpt-5.6-terra",
    "gpt-5.6-luna-wm": "gpt-5.6-luna",
    "gpt-5.5-wm": "gpt-5.5",
    "gpt-5-5-instant": "gpt-5.5",
    "gpt-5-5-thinking": "gpt-5.5",
    "gpt-5-6-thinking": "gpt-5.6-sol",
    "gpt-5-6": "gpt-5.6-sol",
    "gpt-6-astra-wm": "gpt-6-astra",
    "gpt-6-sol-wm": "gpt-6-sol",
    "gpt-6-luna-wm": "gpt-6-luna",
  });
  const servedKeys = new Set(["used_model", "used_model_slug", "resolved_model", "resolved_model_slug", "served_model", "served_model_slug"]);
  const defaultKeys = new Set(["default_model", "default_model_slug"]);
  const reasoningKeys = new Set(["reasoning_effort", "reasoningeffort", "reasoning_level", "reasoninglevel", "thinking_level", "thinkinglevel", "thinking_effort"]);
  const skippedKeys = new Set(["content", "parts", "text", "prompt", "input", "output_text", "arguments"]);
  const state = { version: 144, authority: null, sequence: 0, requests: 0, rewrites: 0, evidence: 0 };
  globalThis[KEY] = state;

  const nativeFetch = globalThis.fetch?.bind(globalThis);
  if (typeof nativeFetch !== "function") return;

  function canonicalKey(value) { return String(value || "").trim().toLowerCase().replace(/[-.]/g, "_"); }
  function normalizeModel(value) {
    const model = String(value || "").trim().toLowerCase();
    if (!/^[a-z0-9._:-]{1,128}$/.test(model)) return null;
    if (model === "gpt-6-astra" || /^gpt-6-astra[.:_-]/.test(model)) return "gpt-6-astra";
    if (model === "gpt-6-sol" || /^gpt-6-sol[.:_-]/.test(model)) return "gpt-6-sol";
    return aliases[model] || model;
  }
  function normalizeReasoning(value) {
    const level = String(value || "").trim().toLowerCase().replace(/_/g, "-");
    if (["extra high", "extra-high", "xhigh"].includes(level)) return "extra-high";
    if (level === "extended") return "high";
    return ["low", "medium", "high"].includes(level) ? level : null;
  }
  function post(detail) {
    try { globalThis.postMessage({ source: SOURCE, revision: 144, sequence: ++state.sequence, ...detail }, "*"); } catch (_) {}
  }
  function conversationRequest(input, init) {
    try {
      const method = String(init?.method || input?.method || "GET").toUpperCase();
      const rawUrl = typeof input === "string" ? input : input?.url || "";
      const url = new URL(rawUrl, location.href);
      return method === "POST" && ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(url.hostname)
        && ["/backend-api/conversation", "/backend-api/f/conversation"].includes(url.pathname);
    } catch (_) { return false; }
  }
  function select(candidates) {
    if (!candidates.length) return { value: null, conflict: false, field: null };
    const score = Math.max(...candidates.map(row => row.score));
    const best = candidates.filter(row => row.score === score);
    const values = [...new Set(best.map(row => row.value))];
    if (values.length !== 1) return { value: null, conflict: true, field: null };
    const row = best[best.length - 1];
    return { value: row.value, conflict: false, field: row.field };
  }
  function inspectObjects(values) {
    const models = [], defaults = [], reasoning = [];
    function walk(value, path = [], depth = 0) {
      if (depth > MAX_DEPTH || value == null || typeof value !== "object") return;
      if (Array.isArray(value)) { value.forEach((child, index) => walk(child, [...path, String(index)], depth + 1)); return; }
      for (const [rawKey, child] of Object.entries(value)) {
        const key = canonicalKey(rawKey), field = [...path, rawKey].join(".");
        if (servedKeys.has(key) && typeof child === "string") {
          const model = normalizeModel(child); if (model) models.push({ value: model, score: 150, field });
        } else if (defaultKeys.has(key) && typeof child === "string") {
          const model = normalizeModel(child); if (model) defaults.push({ value: model, score: 110, field });
        }
        if (reasoningKeys.has(key) && typeof child === "string") {
          const level = normalizeReasoning(child); if (level) reasoning.push({ value: level, score: 115, field });
        }
        if (!skippedKeys.has(key)) walk(child, [...path, rawKey], depth + 1);
      }
    }
    values.forEach(value => walk(value));
    const served = select(models), fallback = select(defaults), think = select(reasoning);
    return {
      served_model: served.value,
      default_model: fallback.value,
      reasoning: think.value,
      model_conflict: served.conflict || fallback.conflict,
      reasoning_conflict: think.conflict,
      model_field: served.field,
      default_model_field: fallback.field,
      reasoning_field: think.field,
      evidence_source: "network_response_metadata",
    };
  }
  function parseEvent(raw) {
    const data = raw.split(/\r?\n/).filter(line => line.startsWith("data:"))
      .map(line => line.slice(5).trimStart()).join("\n").trim();
    if (!data || data === "[DONE]") return null;
    try { const value = JSON.parse(data); return value && typeof value === "object" ? value : null; } catch (_) { return null; }
  }
  function requestModel(payload) { return payload && typeof payload === "object" ? normalizeModel(payload.model) : null; }

  async function bodyText(input, init) {
    if (typeof init?.body === "string") return init.body;
    try { if (input instanceof Request) return await input.clone().text(); } catch (_) {}
    return "";
  }
  async function applyAuthority(input, init, authority, streamId) {
    const raw = await bodyText(input, init);
    let payload = null;
    try { payload = raw ? JSON.parse(raw) : null; } catch (_) {}
    const before = requestModel(payload);
    const target = normalizeModel(authority?.routed_model);
    const transport = String(authority?.transport_model || "").trim() || target;
    let rewritten = false;
    let nextInput = input, nextInit = init;
    if (payload && typeof payload === "object" && !Array.isArray(payload) && target) {
      const beforeTransport = String(payload.model || "");
      if (normalizeModel(beforeTransport) !== target || beforeTransport !== transport) {
        payload.model = transport;
        const nextBody = JSON.stringify(payload);
        if (typeof init?.body === "string") {
          nextInit = { ...(init || {}), body: nextBody };
          rewritten = true;
        } else if (input instanceof Request) {
          try {
            nextInput = new Request(input, { body: nextBody });
            nextInit = init;
            rewritten = true;
          } catch (_) {}
        }
      }
    }
    const after = requestModel(payload) || before;
    if (rewritten) state.rewrites += 1;
    post({ phase: "request", stream_id: streamId, request_model: after, original_request_model: before, authority, request_rewritten: rewritten,
      authority_violation: Boolean(target && after && target !== after) });
    return { input: nextInput, init: nextInit, requestModel: after, authorityViolation: Boolean(target && after && target !== after), rewritten };
  }

  function observeResponse(response, streamId, requestInfo) {
    let clone;
    try { clone = response.clone(); } catch (_) { return; }
    const headerModel = ["x-openai-model", "openai-model", "x-gpt-model", "x-model"]
      .map(name => normalizeModel(clone.headers?.get?.(name))).find(Boolean) || null;
    post({ phase: "response", stream_id: streamId, status: Number(clone.status || 0), ok: Boolean(clone.ok), request_model: requestInfo.requestModel,
      request_rewritten: requestInfo.rewritten, authority_violation: requestInfo.authorityViolation, header_model: headerModel });
    const reader = clone.body?.getReader?.();
    if (!reader) return;
    const decoder = new TextDecoder("utf-8");
    let buffer = "", bytes = 0, lastFingerprint = "";
    const objects = [];
    const emitEvidence = terminal => {
      const evidence = inspectObjects(objects);
      if (headerModel) {
        if (evidence.served_model && evidence.served_model !== headerModel) {
          evidence.served_model = null; evidence.model_conflict = true; evidence.model_field = null;
        } else if (!evidence.model_conflict) {
          evidence.served_model = headerModel; evidence.model_field = "response-header";
        }
      }
      const fingerprint = JSON.stringify(evidence);
      if (!terminal && fingerprint === lastFingerprint) return;
      lastFingerprint = fingerprint;
      state.evidence += 1;
      post({ phase: "evidence", stream_id: streamId, terminal: Boolean(terminal), request_model: requestInfo.requestModel,
        request_rewritten: requestInfo.rewritten, authority_violation: requestInfo.authorityViolation, ...evidence });
    };
    (async () => {
      try {
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          bytes += value?.byteLength || 0;
          if (bytes > MAX_BUFFER) break;
          buffer += decoder.decode(value, { stream: true });
          const blocks = buffer.split(/\r?\n\r?\n/); buffer = blocks.pop() || "";
          for (const block of blocks) { const parsed = parseEvent(block); if (parsed) { objects.push(parsed); emitEvidence(false); } }
        }
        buffer += decoder.decode();
        if (buffer.trim()) { const parsed = parseEvent(buffer); if (parsed) objects.push(parsed); }
        emitEvidence(true);
        post({ phase: "done", stream_id: streamId, bytes });
      } catch (error) {
        // Preserve the strongest evidence already seen even when an HTTP 200 stream aborts.
        emitEvidence(true);
        post({ phase: "error", stream_id: streamId, bytes, error: String(error?.message || error) });
      }
    })();
  }

  globalThis.addEventListener("message", event => {
    if (event.source !== globalThis) return;
    const detail = event.data;
    if (!detail || detail.source !== AUTHORITY_SOURCE) return;
    state.authority = detail.authority && typeof detail.authority === "object" ? { ...detail.authority, request_id: detail.request_id || null } : null;
  });

  globalThis.fetch = async function chat2apiModelEvidenceFetch(input, init) {
    if (!conversationRequest(input, init)) return nativeFetch(input, init);
    state.requests += 1;
    const streamId = `model-${Date.now().toString(36)}-${state.requests.toString(36)}`;
    const authority = state.authority;
    const requestInfo = await applyAuthority(input, init, authority, streamId);
    let response;
    try { response = await nativeFetch(requestInfo.input, requestInfo.init); }
    catch (error) { post({ phase: "error", stream_id: streamId, request_model: requestInfo.requestModel, error: String(error?.message || error) }); throw error; }
    observeResponse(response, streamId, requestInfo);
    return response;
  };
})();
