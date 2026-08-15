(() => {
  const KEY = "__CHAT2API_MODEL_CONTRACT_V25__";
  if (globalThis[KEY]) return;

  const MINI_MODEL = "gpt-5.5-mini";
  const MINI_CAPABILITIES = ["text", "vision", "file-understanding"];

  function canonicalModelId(value) {
    return String(value || "").trim().toLowerCase().replace(/\s+/g, "-");
  }

  function normalizeModelRecord(raw) {
    const item = typeof raw === "string" ? { id: raw, label: raw } : { ...(raw || {}) };
    const id = canonicalModelId(item.id || "");
    if (!id) return null;
    item.id = id;
    if (id === MINI_MODEL) {
      item.label = item.label || "GPT-5.5 Mini";
      const capabilities = Array.isArray(item.capabilities) ? [...item.capabilities] : [];
      for (const capability of MINI_CAPABILITIES) {
        if (!capabilities.includes(capability)) capabilities.push(capability);
      }
      item.capabilities = capabilities;
      item.reasoning_efforts = [];
    }
    return item;
  }

  function normalizeModels(models) {
    const rows = [];
    const seen = new Set();
    for (const raw of Array.isArray(models) ? models : []) {
      const item = normalizeModelRecord(raw);
      if (!item || seen.has(item.id)) continue;
      seen.add(item.id);
      rows.push(item);
    }
    if (!seen.has(MINI_MODEL)) {
      rows.push({
        id: MINI_MODEL,
        label: "GPT-5.5 Mini",
        family: MINI_MODEL,
        reasoning: null,
        reasoning_efforts: [],
        capabilities: [...MINI_CAPABILITIES],
      });
    }
    return rows;
  }

  const baseHandleServerMessage = handleServerMessage;
  handleServerMessage = async function handleCanonicalModelIdsV25(message) {
    if (message && typeof message === "object") {
      message = { ...message };
      if (message.model) message.model = canonicalModelId(message.model);
      if (message.options && typeof message.options === "object") {
        message.options = { ...message.options };
        for (const key of ["model", "requested_model", "logical_model", "effective_model", "fallback_model"]) {
          if (message.options[key]) message.options[key] = canonicalModelId(message.options[key]);
        }
      }
    }
    return baseHandleServerMessage(message);
  };

  if (typeof discoverModels === "function") {
    const baseDiscoverModels = discoverModels;
    discoverModels = async function discoverModelsV25(...args) {
      const result = await baseDiscoverModels(...args);
      if (!result || typeof result !== "object") return result;
      return {
        ...result,
        models: normalizeModels(result.models),
        current_model: result.current_model ? canonicalModelId(result.current_model) : result.current_model,
      };
    };
  }

  const affinity = globalThis.chat2apiModelAffinityV23;
  if (affinity && typeof affinity.requestedCombo === "function") {
    const baseRequestedCombo = affinity.requestedCombo.bind(affinity);
    affinity.requestedCombo = function requestedComboV25(message, accountType = "unknown") {
      const value = message && typeof message === "object" ? { ...message } : message;
      if (value && typeof value === "object") {
        if (value.model) value.model = canonicalModelId(value.model);
        if (value.options && typeof value.options === "object") {
          value.options = { ...value.options };
          if (value.options.model) value.options.model = canonicalModelId(value.options.model);
        }
      }
      return baseRequestedCombo(value, accountType);
    };
  }

  globalThis.chat2apiCanonicalModelIdV25 = canonicalModelId;
  globalThis.chat2apiNormalizeModelsV25 = normalizeModels;
  globalThis[KEY] = true;
})();
