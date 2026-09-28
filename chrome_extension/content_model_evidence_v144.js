(() => {
  const KEY = "__CHAT2API_MODEL_EVIDENCE_V144__";
  if (globalThis[KEY]) return;

  const SOURCE = "chat2api-model-evidence-v144";
  const AUTHORITY_SOURCE = "chat2api-model-authority-v144";
  const EVIDENCE_KEY = "chat2api:model-evidence:v144";
  const KNOWN_KEY = "chat2api:known-models:v144";
  const MAX_KNOWN = 64;
  const rows = new Map();
  let activeAuthority = null;
  let activeRequestId = "";

  const aliases = Object.freeze({
    "gpt-5.6-sol-wm": "gpt-5.6-sol", "gpt-5.6-terra-wm": "gpt-5.6-terra", "gpt-5.6-luna-wm": "gpt-5.6-luna",
    "gpt-5.5-wm": "gpt-5.5", "gpt-5-5-instant": "gpt-5.5", "gpt-5-5-thinking": "gpt-5.5", "gpt-5-6-thinking": "gpt-5.6-sol", "gpt-5-6": "gpt-5.6-sol",
    "gpt-6-astra-wm": "gpt-6-astra", "gpt-6-sol-wm": "gpt-6-sol", "gpt-6-luna-wm": "gpt-6-luna",
  });
  function normalizeModel(value) {
    const model = String(value || "").trim().toLowerCase();
    if (!/^[a-z0-9._:-]{1,128}$/.test(model)) return null;
    if (model === "auto" || model === "default" || model === "chatgpt-web") return null;
    if (model === "gpt-6-astra" || /^gpt-6-astra[.:_-]/.test(model)) return "gpt-6-astra";
    if (model === "gpt-6-sol" || /^gpt-6-sol[.:_-]/.test(model)) return "gpt-6-sol";
    return aliases[model] || model;
  }
  async function emit(event) {
    try { return await chrome.runtime.sendMessage({ type: "chat2api.event", event }); } catch (_) { return null; }
  }
  function rememberModel(value, source) {
    const model = normalizeModel(value);
    if (!model) return;
    chrome.storage.local.get({ [KNOWN_KEY]: [] }).then(stored => {
      const previous = Array.isArray(stored[KNOWN_KEY]) ? stored[KNOWN_KEY] : [];
      const filtered = previous.filter(item => normalizeModel(item?.id || item?.model || item) !== model);
      filtered.push({ id: model, source: String(source || "network"), last_seen_at: new Date().toISOString() });
      return chrome.storage.local.set({ [KNOWN_KEY]: filtered.slice(-MAX_KNOWN) });
    }).catch(() => {});
  }
  function authorityFrom(message) {
    const value = message?.options?.model_authority;
    return value && typeof value === "object" ? { ...value } : null;
  }
  function broadcastAuthority(requestId, authority) {
    try { globalThis.postMessage({ source: AUTHORITY_SOURCE, request_id: requestId, authority }, "*"); } catch (_) {}
  }
  function bind(streamId) {
    const key = String(streamId || "");
    if (!key) return null;
    let row = rows.get(key);
    if (!row) {
      row = { streamId: key, requestId: activeRequestId, authority: activeAuthority, evidence: {} };
      rows.set(key, row);
    }
    return row;
  }
  function publicEvidence(row, detail) {
    const evidence = {
      request_model: normalizeModel(detail.request_model || row.evidence.request_model),
      served_model: normalizeModel(detail.served_model || row.evidence.served_model),
      default_model: normalizeModel(detail.default_model || row.evidence.default_model),
      reasoning: detail.reasoning || row.evidence.reasoning || null,
      model_conflict: Boolean(detail.model_conflict || row.evidence.model_conflict),
      reasoning_conflict: Boolean(detail.reasoning_conflict || row.evidence.reasoning_conflict),
      model_field: detail.model_field || row.evidence.model_field || null,
      default_model_field: detail.default_model_field || row.evidence.default_model_field || null,
      reasoning_field: detail.reasoning_field || row.evidence.reasoning_field || null,
      evidence_source: detail.evidence_source || "network_response_metadata",
      request_rewritten: Boolean(detail.request_rewritten || row.evidence.request_rewritten),
      authority_violation: Boolean(detail.authority_violation || row.evidence.authority_violation),
      authority_requested_model: normalizeModel(row.authority?.requested_model),
      authority_routed_model: normalizeModel(row.authority?.routed_model),
      authority_transport_model: row.authority?.transport_model || null,
      redesigned_page_compatible: true,
      hidden_work_profile_supported: true,
    };
    row.evidence = evidence;
    return evidence;
  }
  async function publishEvidence(row, detail) {
    if (!row?.requestId) return;
    const evidence = publicEvidence(row, detail);
    rememberModel(evidence.served_model, "network_served");
    rememberModel(evidence.default_model, "network_default_profile");
    try { await chrome.storage.session?.set?.({ [EVIDENCE_KEY]: { request_id: row.requestId, at: Date.now(), ...evidence } }); } catch (_) {}
    await emit({
      type: "chat.diagnostics",
      request_id: row.requestId,
      diagnostics: {
        model_evidence_v144: evidence,
        model_evidence_revision: 144,
        model_evidence_transport: "main-world-fetch",
        redesigned_chatgpt_model_adapter: "v144",
      },
    });
  }

  globalThis.addEventListener("message", event => {
    if (event.source !== globalThis) return;
    const detail = event.data;
    if (!detail || detail.source !== SOURCE) return;
    const row = bind(detail.stream_id);
    if (!row) return;
    if (detail.phase === "request") {
      row.requestId = row.requestId || activeRequestId;
      row.authority = row.authority || activeAuthority;
      row.evidence.request_model = detail.request_model || null;
      row.evidence.request_rewritten = Boolean(detail.request_rewritten);
      row.evidence.authority_violation = Boolean(detail.authority_violation);
      publishEvidence(row, detail).catch(() => {});
      return;
    }
    if (detail.phase === "evidence") {
      publishEvidence(row, detail).catch(() => {});
      return;
    }
    if (detail.phase === "error" || detail.phase === "done") {
      if (Object.keys(row.evidence).length) publishEvidence(row, detail).catch(() => {});
      setTimeout(() => rows.delete(row.streamId), 15000);
    }
  });

  chrome.runtime.onMessage.addListener((message, _sender, _sendResponse) => {
    if (message?.type === "chat2api.request") {
      activeRequestId = String(message.requestId || "");
      activeAuthority = authorityFrom(message);
      if (activeAuthority) broadcastAuthority(activeRequestId, activeAuthority);
    } else if (message?.type === "chat2api.cancel" && String(message.requestId || "") === activeRequestId) {
      activeRequestId = "";
      activeAuthority = null;
      broadcastAuthority("", null);
    }
    return false;
  });

  const api = {
    version: 144,
    normalizeModel,
    snapshot() { return [...rows.values()].map(row => ({ requestId: row.requestId, streamId: row.streamId, authority: row.authority, evidence: { ...row.evidence } })); },
    async knownModels() {
      const stored = await chrome.storage.local.get({ [KNOWN_KEY]: [] });
      return (Array.isArray(stored[KNOWN_KEY]) ? stored[KNOWN_KEY] : []).map(item => normalizeModel(item?.id || item?.model || item)).filter(Boolean);
    },
  };
  globalThis[KEY] = api;
})();
