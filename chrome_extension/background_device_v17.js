(() => {
  const DEVICE_KEY = "deviceId";

  async function ensureDeviceId() {
    const stored = await chrome.storage.local.get({[DEVICE_KEY]: ""});
    let value = String(stored[DEVICE_KEY] || "").trim();
    if (!value) {
      value = (self.crypto?.randomUUID?.() || `device-${Date.now()}-${Math.random().toString(36).slice(2)}`);
      await chrome.storage.local.set({[DEVICE_KEY]: value});
    }
    return value;
  }

  const originalFetch = self.fetch.bind(self);
  self.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input?.url || "";
    if (url.includes("/api/extensions/register") && String(init.method || "GET").toUpperCase() === "POST") {
      try {
        const deviceId = await ensureDeviceId();
        const body = JSON.parse(String(init.body || "{}"));
        body.device_id = deviceId;
        body.metadata = {...(body.metadata || {}), device_id: deviceId};
        init = {...init, body: JSON.stringify(body)};
      } catch (_) {}
    }
    return originalFetch(input, init);
  };

  if (typeof trySendSocket === "function") {
    const baseTrySendSocket = trySendSocket;
    trySendSocket = async payload => {
      if (payload?.type === "extension.status") {
        const deviceId = await ensureDeviceId();
        payload = {...payload, metadata: {...(payload.metadata || {}), device_id: deviceId}};
      }
      return baseTrySendSocket(payload);
    };
  }

  if (typeof sendSocket === "function") {
    const baseSendSocket = sendSocket;
    sendSocket = async payload => {
      if (payload?.type === "extension.hello") {
        const deviceId = await ensureDeviceId();
        payload = {...payload, metadata: {...(payload.metadata || {}), device_id: deviceId}};
      }
      return baseSendSocket(payload);
    };
  }

  ensureDeviceId().catch(() => {});
})();
