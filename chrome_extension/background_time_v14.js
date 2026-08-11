(() => {
  if (globalThis.__CHAT2API_BACKGROUND_TIME_V14__) return;
  globalThis.__CHAT2API_BACKGROUND_TIME_V14__ = true;

  function beijingIso(value = Date.now()) {
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return null;
    const shifted = new Date(date.getTime() + 8 * 60 * 60 * 1000);
    const p = (number, width = 2) => String(number).padStart(width, "0");
    return `${shifted.getUTCFullYear()}-${p(shifted.getUTCMonth() + 1)}-${p(shifted.getUTCDate())}` +
      `T${p(shifted.getUTCHours())}:${p(shifted.getUTCMinutes())}:${p(shifted.getUTCSeconds())}.${p(shifted.getUTCMilliseconds(), 3)}+08:00`;
  }

  globalThis.chat2apiBeijingIso = beijingIso;

  if (typeof updateState === "function") {
    const baseUpdateState = updateState;
    updateState = async function updateStateBeijing(socketState, socketError = "") {
      await baseUpdateState(socketState, socketError);
      await chrome.storage.local.set({ socketUpdatedAt: beijingIso() });
    };
  }

  if (typeof pair === "function") {
    const basePair = pair;
    pair = async function pairBeijing(args) {
      const result = await basePair(args);
      await chrome.storage.local.set({ pairedAt: beijingIso() });
      return result;
    };
  }

  chrome.storage.local.get(["socketUpdatedAt", "pairedAt"]).then(values => {
    const patch = {};
    if (values.socketUpdatedAt) patch.socketUpdatedAt = beijingIso(values.socketUpdatedAt) || values.socketUpdatedAt;
    if (values.pairedAt) patch.pairedAt = beijingIso(values.pairedAt) || values.pairedAt;
    if (Object.keys(patch).length) return chrome.storage.local.set(patch);
    return null;
  }).catch(() => {});
})();
