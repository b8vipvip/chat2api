(() => {
  const KEY = "__CHAT2API_NETWORK_GATE_V26__";
  if (globalThis[KEY]) return;

  const PROBE_URL = "https://ipwho.is/";
  const CACHE_MS = 30 * 60 * 1000;
  const ERROR_CACHE_MS = 2 * 60 * 1000;
  const PROBE_TIMEOUT_MS = 5000;
  const STORAGE_DEFAULTS = {
    networkProbeStatus: "unknown",
    networkCountryCode: "",
    networkExternalReady: false,
    networkProbeProvider: "",
    networkProbeCheckedAt: 0,
    networkProbeError: "",
  };
  const state = { inFlight: null, probes: 0, cacheHits: 0 };
  globalThis[KEY] = state;

  const baseFetch = self.fetch.bind(self);

  function normalizeCountry(value) {
    const code = String(value || "").trim().toUpperCase();
    return /^[A-Z]{2}$/.test(code) ? code : "";
  }

  function publicSnapshot(stored) {
    return {
      status: String(stored?.networkProbeStatus || "unknown"),
      country_code: normalizeCountry(stored?.networkCountryCode),
      external_ready: stored?.networkExternalReady === true,
      provider: String(stored?.networkProbeProvider || ""),
      checked_at_ms: Number(stored?.networkProbeCheckedAt || 0),
      error: String(stored?.networkProbeError || ""),
    };
  }

  async function storedSnapshot() {
    const stored = await chrome.storage.local.get(STORAGE_DEFAULTS).catch(() => STORAGE_DEFAULTS);
    return publicSnapshot(stored);
  }

  async function persistSnapshot(snapshot) {
    const value = {
      networkProbeStatus: snapshot.status,
      networkCountryCode: snapshot.country_code || "",
      networkExternalReady: snapshot.external_ready === true,
      networkProbeProvider: snapshot.provider || "",
      networkProbeCheckedAt: Number(snapshot.checked_at_ms || Date.now()),
      networkProbeError: snapshot.error || "",
    };
    await chrome.storage.local.set(value).catch(() => {});
    return publicSnapshot(value);
  }

  function isFresh(snapshot) {
    const age = Date.now() - Number(snapshot.checked_at_ms || 0);
    if (age < 0) return false;
    const ttl = ["external", "china-mainland"].includes(snapshot.status) ? CACHE_MS : ERROR_CACHE_MS;
    return Boolean(snapshot.checked_at_ms && age < ttl);
  }

  async function runProbe() {
    state.probes += 1;
    if (globalThis.navigator?.onLine === false) {
      return persistSnapshot({
        status: "offline",
        country_code: "",
        external_ready: false,
        provider: "navigator",
        checked_at_ms: Date.now(),
        error: "Browser reports that the network is offline",
      });
    }

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
    try {
      const response = await baseFetch(PROBE_URL, {
        method: "GET",
        cache: "no-store",
        credentials: "omit",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (payload?.success === false) throw new Error(String(payload?.message || "IP country lookup failed"));
      const countryCode = normalizeCountry(payload?.country_code);
      if (!countryCode) throw new Error("IP country lookup returned no ISO country code");
      const external = countryCode !== "CN";
      return persistSnapshot({
        status: external ? "external" : "china-mainland",
        country_code: countryCode,
        external_ready: external,
        provider: "ipwho.is",
        checked_at_ms: Date.now(),
        error: "",
      });
    } catch (error) {
      const text = error?.name === "AbortError" ? "IP country lookup timed out" : String(error?.message || error);
      return persistSnapshot({
        status: "error",
        country_code: "",
        external_ready: false,
        provider: "ipwho.is",
        checked_at_ms: Date.now(),
        error: text,
      });
    } finally {
      clearTimeout(timeout);
    }
  }

  async function probe(force = false) {
    const cached = await storedSnapshot();
    if (!force && isFresh(cached)) {
      state.cacheHits += 1;
      return cached;
    }
    if (state.inFlight) return state.inFlight;
    state.inFlight = runProbe().finally(() => { state.inFlight = null; });
    return state.inFlight;
  }

  async function allowPrewarm() {
    const result = await probe(false);
    return result.external_ready === true;
  }

  async function statusSnapshot() {
    const socket = await chrome.storage.local.get({ socketState: "disconnected" }).catch(() => ({ socketState: "disconnected" }));
    if (socket.socketState === "connected") return probe(false);
    return storedSnapshot();
  }

  function metadata(snapshot) {
    return {
      network_probe_version: "v26",
      network_probe_status: snapshot?.status || "unknown",
      network_country_code: snapshot?.country_code || null,
      network_external_ready: snapshot?.external_ready === true,
      network_probe_provider: snapshot?.provider || null,
      network_probe_checked_at_ms: Number(snapshot?.checked_at_ms || 0) || null,
      network_probe_error: snapshot?.error ? String(snapshot.error).slice(0, 180) : null,
    };
  }

  state.probe = probe;
  state.allowPrewarm = allowPrewarm;
  state.snapshot = storedSnapshot;

  if (typeof trySendSocket === "function") {
    const baseTrySendSocket = trySendSocket;
    trySendSocket = async payload => {
      if (payload?.type === "extension.status") {
        const snapshot = await statusSnapshot();
        payload = { ...payload, metadata: { ...(payload.metadata || {}), ...metadata(snapshot) } };
      }
      return baseTrySendSocket(payload);
    };
  }

  if (typeof sendSocket === "function") {
    const baseSendSocket = sendSocket;
    sendSocket = async payload => {
      if (payload?.type === "extension.hello") {
        const snapshot = await storedSnapshot();
        payload = { ...payload, metadata: { ...(payload.metadata || {}), ...metadata(snapshot) } };
      }
      return baseSendSocket(payload);
    };
  }

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (changes.socketState?.newValue === "connected") probe(false).catch(() => {});
  });
})();
