(() => {
  const KEY = "__CHAT2API_PLATFORM_V26__";
  if (globalThis[KEY]) return;

  const state = { inFlight: null, value: null };
  globalThis[KEY] = state;

  function normalize(value) {
    return String(value || "").trim().toLowerCase();
  }

  async function detectPlatform() {
    if (state.value) return state.value;
    if (state.inFlight) return state.inFlight;
    state.inFlight = (async () => {
      let raw = {};
      try { raw = await chrome.runtime.getPlatformInfo(); } catch (_) {}
      const os = normalize(raw?.os) || "unknown";
      const arch = normalize(raw?.arch) || "unknown";
      const naclArch = normalize(raw?.nacl_arch) || "";
      const value = {
        os,
        arch,
        nacl_arch: naclArch || null,
        linux_supported: os === "linux",
        supported_desktop: ["win", "linux", "mac"].includes(os),
        detected_at_ms: Date.now(),
      };
      state.value = value;
      await chrome.storage.local.set({
        platformOs: value.os,
        platformArch: value.arch,
        platformNaclArch: value.nacl_arch || "",
        platformLinuxSupported: value.linux_supported,
        platformSupportedDesktop: value.supported_desktop,
        platformDetectedAt: value.detected_at_ms,
      }).catch(() => {});
      return value;
    })().finally(() => { state.inFlight = null; });
    return state.inFlight;
  }

  function metadata(platform) {
    return {
      platform_metadata_version: "v26",
      platform_os: platform?.os || "unknown",
      platform_arch: platform?.arch || "unknown",
      platform_linux_supported: platform?.linux_supported === true,
      platform_supported_desktop: platform?.supported_desktop === true,
    };
  }

  state.detect = detectPlatform;

  const baseFetch = self.fetch.bind(self);
  self.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input?.url || "";
    if (url.includes("/api/extensions/register") && String(init.method || "GET").toUpperCase() === "POST") {
      try {
        const platform = await detectPlatform();
        const body = JSON.parse(String(init.body || "{}"));
        body.metadata = { ...(body.metadata || {}), ...metadata(platform) };
        init = { ...init, body: JSON.stringify(body) };
      } catch (_) {}
    }
    return baseFetch(input, init);
  };

  if (typeof trySendSocket === "function") {
    const baseTrySendSocket = trySendSocket;
    trySendSocket = async payload => {
      if (payload?.type === "extension.status") {
        const platform = await detectPlatform();
        payload = { ...payload, metadata: { ...(payload.metadata || {}), ...metadata(platform) } };
      }
      return baseTrySendSocket(payload);
    };
  }

  if (typeof sendSocket === "function") {
    const baseSendSocket = sendSocket;
    sendSocket = async payload => {
      if (payload?.type === "extension.hello") {
        const platform = await detectPlatform();
        payload = { ...payload, metadata: { ...(payload.metadata || {}), ...metadata(platform) } };
      }
      return baseSendSocket(payload);
    };
  }

  detectPlatform().catch(() => {});
})();
