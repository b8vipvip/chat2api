(() => {
  const KEY = "__CHAT2API_RESERVE_STATUS_RECONNECT_V29__";
  if (globalThis[KEY]) return;
  globalThis[KEY] = true;

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local" || changes.socketState?.newValue !== "connected") return;
    const reserve = globalThis.__CHAT2API_RESERVE_POOL_V29__;
    if (!reserve) return;
    reserve.lastReportSignature = "";
    setTimeout(() => reserve.report?.(true)?.catch?.(() => {}), 0);
  });
})();
