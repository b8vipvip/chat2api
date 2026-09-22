(() => {
  const KEY = "__CHAT2API_WORKER_PRESENTATION_V66__";
  if (globalThis[KEY]) return;
  const VERSION = 66;

  function callApi(path, options) {
    if (typeof globalThis.api === "function") return globalThis.api(path, options);
    if (typeof api === "function") return api(path, options);
    return Promise.reject(new Error("管理控制台 API helper 不可用"));
  }

  function applyPairings(rows) {
    const body = document.getElementById("pairingBody");
    if (!body) return;
    const list = Array.isArray(rows) ? rows : [];
    [...body.rows].forEach((tr, index) => {
      const row = list[index];
      if (!row) return;
      const actions = tr.querySelector(".rowactions");
      if (!actions) return;
      let button = actions.querySelector("[data-v66-pairing-rename]");
      if (!button) {
        button = document.createElement("button");
        button.className = "action";
        button.dataset.v66PairingRename = "1";
        button.textContent = "改名";
        actions.insertBefore(button, actions.firstChild || null);
      }
      button.dataset.pairingId = String(row.pairing_id || "");
      button.dataset.pairingName = String(row.name || "");
    });
  }

  async function refreshPairings() {
    const data = await callApi("/api/admin/extensions");
    applyPairings(data?.pairing_codes);
    return data;
  }

  function installReloadHook() {
    const base = globalThis.chat2apiReloadCanonicalWorkerListV59;
    if (typeof base !== "function" || base.__chat2apiPresentationV66) return;
    const wrapped = async (...args) => {
      const result = await base(...args);
      await refreshPairings();
      return result;
    };
    wrapped.__chat2apiPresentationV66 = true;
    globalThis.chat2apiReloadCanonicalWorkerListV59 = wrapped;
  }

  document.addEventListener("click", async event => {
    const rename = event.target?.closest?.("[data-v66-pairing-rename]");
    if (!rename) return;
    event.preventDefault();
    event.stopPropagation();
    const pairingId = String(rename.dataset.pairingId || "");
    const current = String(rename.dataset.pairingName || "");
    const next = prompt("设备名称", current);
    if (next == null) return;
    const clean = next.trim();
    if (!clean) {
      if (typeof globalThis.status === "function") globalThis.status("设备名称不能为空", "bad");
      return;
    }
    try {
      await callApi(`/api/admin/pairing-codes/${encodeURIComponent(pairingId)}/name`, {method:"PATCH", body:{name:clean}});
      if (typeof globalThis.status === "function") globalThis.status("设备名称已更新", "ok");
      const reload = globalThis.chat2apiReloadCanonicalWorkerListV59;
      if (typeof reload === "function") await reload();
      else await refreshPairings();
    } catch (error) {
      if (typeof globalThis.status === "function") globalThis.status("设备名称修改失败：" + String(error?.message || error), "bad");
    }
  }, true);

  function start() {
    installReloadHook();
    setTimeout(() => { installReloadHook(); refreshPairings().catch(() => {}); }, 140);
  }

  globalThis[KEY] = Object.freeze({version:VERSION, applyPairings, refreshPairings, worker_table_render_authority:false});
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, {once:true});
  else start();
})();