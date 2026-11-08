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
    const previous=row.evidence || {};
    const owns=key=>Object.prototype.hasOwnProperty.call(detail,key);
    // A later authoritative response may EXPLICITLY invalidate an earlier
    // model. 'newValue || oldValue' accidentally resurrected the old served
    // model when the network adapter emitted {served_model:null,
    // model_conflict:true}. Conflicts are sticky for this same stream.
    const select=key=>owns(key)?detail[key]:previous[key];
    const modelConflict=Boolean(detail.model_conflict || previous.model_conflict);
    const defaultConflict=Boolean(detail.default_model_conflict || previous.default_model_conflict);
    const reasoningConflict=Boolean(detail.reasoning_conflict || previous.reasoning_conflict);
    const evidence = {
      request_model: normalizeModel(select("request_model")),
      // Raw model field actually handed to same-request /conversation fetch.
      // Keep transport suffixes here; logical normalization would hide a
      // failed rewrite even while falsely claiming the picker was correct.
      request_wire_model: (() => {
        const raw=String(select("request_wire_model") || "").toLowerCase();
        return /^[a-z0-9._:-]{1,128}$/.test(raw) ? raw : null;
      })(),
      served_model: modelConflict?null:normalizeModel(select("served_model")),
      default_model: defaultConflict?null:normalizeModel(select("default_model")),
      reasoning: reasoningConflict?null:(select("reasoning") || null),
      model_conflict: modelConflict,
      default_model_conflict: defaultConflict,
      reasoning_conflict: reasoningConflict,
      model_field: modelConflict?null:(select("model_field") || null),
      default_model_field: defaultConflict?null:(select("default_model_field") || null),
      reasoning_field: reasoningConflict?null:(select("reasoning_field") || null),
      evidence_source: detail.evidence_source || previous.evidence_source || "network_request_metadata",
      request_rewritten: Boolean(detail.request_rewritten || row.evidence.request_rewritten),
      // Only the immutable request-phase MAIN-world observer may supply
      // this fact; response-phase events must not manufacture new wire proof.
      page_native_wire_respected: previous.page_native_wire_respected === true,
      original_wire_model: (() => {
        const raw=String(previous.original_wire_model || "").toLowerCase();
        return /^[a-z0-9._:-]{1,128}$/.test(raw) ? raw : null;
      })(),
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
  function publishEvidence(row, detail) {
    if (!row?.requestId) return Promise.resolve();
    const evidence = publicEvidence(row, detail);
    const revision = (row.publishRevision || 0) + 1;
    row.publishRevision = revision;
    // chrome.storage.session.set() and runtime.sendMessage() are async. When
    // a newer event invalidates served_model, an older pending write could
    // otherwise finish last and resurrect a false served-model record in
    // both session storage and server telemetry. Serialize per network stream,
    // skipping superseded revisions; distinct streams are unaffected.
    const previous = row.publishTail || Promise.resolve();
    const job = previous.catch(() => {}).then(async () => {
      if (revision !== row.publishRevision) return;
      rememberModel(evidence.served_model, "network_served");
      rememberModel(evidence.default_model, "network_default_profile");
      try {
        await chrome.storage.session?.set?.({
          [EVIDENCE_KEY]: { request_id: row.requestId, at: Date.now(), ...evidence }
        });
      } catch (_) {}
      if (revision !== row.publishRevision) return;
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
    });
    row.publishTail = job.catch(() => {});
    return job;
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
      row.evidence.request_wire_model = detail.request_wire_model || null;
      row.evidence.request_rewritten = Boolean(detail.request_rewritten);
      row.evidence.page_native_wire_respected = detail.page_native_wire_respected === true;
      row.evidence.original_wire_model = typeof detail.original_wire_model === "string"
        && /^[a-z0-9._:-]{1,128}$/.test(detail.original_wire_model)
        ? detail.original_wire_model : null;
      row.evidence.authority_violation = Boolean(detail.authority_violation);
      publishEvidence(row, detail).catch(() => {});
      return;
    }
    if (detail.phase === "response") {
      // Some valid /conversation fetch replies expose only a response header
      // (e.g. an empty or non-SSE response). Previously the main-world
      // adapter posted this proof but the isolated bridge ignored the phase.
      // An error status is NOT proof that a model served the request.
      const headerModel=detail.ok===true && Number(detail.status)===200
        ? normalizeModel(detail.header_model):null;
      if (headerModel) publishEvidence(row,{
        ...detail,
        served_model:headerModel,
        model_field:"response-header",
        evidence_source:"network_response_header",
      }).catch(() => {});
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
