(() => {
  const VERSION = "0.22.94-key-capacity-only";
  const MIN_LIMIT = 1;
  const MAX_LIMIT = 32;
  let keyRenderInFlight = false;
  let keyRefreshScheduled = false;

  function keyViewActive() {
    return document.getElementById("view-keys")?.classList.contains("active");
  }

  function ensureToastHost() {
    let host = document.getElementById("chat2apiActionToastHost");
    if (host) return host;
    host = document.createElement("div");
    host.id = "chat2apiActionToastHost";
    host.style.cssText = "position:fixed;top:18px;right:18px;z-index:10000;display:flex;flex-direction:column;gap:8px;max-width:min(520px,calc(100vw - 36px));pointer-events:none";
    document.body.appendChild(host);
    return host;
  }

  function showActionResult(message, level = "ok", button = null) {
    const text = String(message || "").trim();
    if (!text) return;
    if (button) button.title = text;
    if (typeof globalThis.status === "function") globalThis.status(text, level === "bad" ? "bad" : level === "warn" ? "warnText" : "ok");
    const toast = document.createElement("div");
    toast.textContent = text;
    toast.style.cssText = "pointer-events:auto;padding:10px 12px;border:1px solid rgba(148,163,184,.32);border-radius:10px;background:#0f172a;box-shadow:0 14px 36px rgba(0,0,0,.4);font-size:13px;line-height:1.45;white-space:normal";
    toast.classList.add(level === "bad" ? "bad" : level === "warn" ? "warnText" : "ok");
    ensureToastHost().appendChild(toast);
    setTimeout(() => toast.remove(), 4800);
  }
  globalThis.chat2apiActionToast = showActionResult;

  function ensureKeyConcurrencyHeader() {
    const body = document.getElementById("keysBody");
    const header = body?.closest("table")?.querySelector("thead tr");
    if (!body || !header) return false;
    let th = header.querySelector("th[data-key-concurrency-v57]");
    if (!th) {
      th = document.createElement("th");
      th.dataset.keyConcurrencyV57 = "1";
      th.textContent = "最大并发";
      th.title = "此 API Key 可同时执行的最大请求数，默认 3；超过后 FIFO 排队。";
      header.insertBefore(th, header.lastElementChild);
    }
    return true;
  }

  async function renderKeyConcurrency() {
    if (!keyViewActive() || keyRenderInFlight || typeof globalThis.api !== "function" || !ensureKeyConcurrencyHeader()) return;
    keyRenderInFlight = true;
    try {
      const [keys, capacity] = await Promise.all([api("/api/admin/keys"), api("/api/admin/capacity-v57")]);
      const rows = [...document.querySelectorAll("#keysBody tr")];
      const data = Array.isArray(keys.data) ? keys.data : [];
      const defaults = Number(capacity?.defaults?.api_key_max_concurrency || 3);
      const configured = capacity?.keys || {};
      const active = capacity?.key_active || {};
      rows.forEach((tr, index) => {
        const item = data[index];
        if (!item || tr.cells.length < 2) return;
        const keyId = String(item.key_id || "");
        let cell = tr.querySelector("td[data-key-concurrency-v57]");
        if (!cell) {
          cell = document.createElement("td");
          cell.dataset.keyConcurrencyV57 = "1";
          tr.insertBefore(cell, tr.lastElementChild);
        }
        const limit = Number(configured[keyId] || defaults);
        let editor = cell.querySelector("[data-key-capacity-editor]");
        if (!editor || String(editor.dataset.keyId || "") !== keyId) {
          cell.innerHTML = `<div data-key-capacity-editor data-key-id="${keyId}" style="display:flex;align-items:center;gap:6px"><span class="muted" data-key-active></span><input data-key-max type="number" min="${MIN_LIMIT}" max="${MAX_LIMIT}" value="${limit}" style="width:58px;padding:5px 6px"><button class="action" data-key-save style="padding:5px 8px">保存</button></div>`;
          editor = cell.querySelector("[data-key-capacity-editor]");
        }
        if (!editor) return;
        const activeNode = editor.querySelector("[data-key-active]");
        if (activeNode) activeNode.textContent = `${Number(active[keyId] || 0)}/`;
        const input = editor.querySelector("[data-key-max]");
        if (input && !editor.contains(document.activeElement) && Number(input.value) !== limit) input.value = String(limit);
      });
    } catch (_) {
    } finally {
      keyRenderInFlight = false;
    }
  }

  function scheduleKeyRefresh() {
    if (keyRefreshScheduled) return;
    keyRefreshScheduled = true;
    requestAnimationFrame(() => {
      keyRefreshScheduled = false;
      renderKeyConcurrency().catch(() => {});
    });
  }

  async function saveKeyConcurrency(button) {
    const editor = button.closest("[data-key-capacity-editor]");
    const keyId = String(editor?.dataset.keyId || "");
    const maximum = Number(editor?.querySelector("[data-key-max]")?.value || 0);
    if (!keyId || !Number.isInteger(maximum) || maximum < MIN_LIMIT || maximum > MAX_LIMIT) {
      showActionResult(`Key 最大并发请输入 ${MIN_LIMIT}-${MAX_LIMIT} 的整数`, "bad", button);
      return;
    }
    button.disabled = true;
    const old = button.textContent;
    button.textContent = "保存中";
    try {
      await api(`/api/admin/keys/${encodeURIComponent(keyId)}/concurrency-v57`, {method:"PUT", body:{max_concurrency:maximum}});
      showActionResult(`API Key 最大并发已设置为 ${maximum}；超过部分将排队依次执行。`, "ok", button);
      await renderKeyConcurrency();
    } catch (error) {
      showActionResult(`API Key 并发设置失败：${String(error?.message || error)}`, "bad", button);
    } finally {
      button.disabled = false;
      button.textContent = old || "保存";
    }
  }

  document.addEventListener("click", event => {
    const keySave = event.target?.closest?.("[data-key-save]");
    if (keySave) { event.preventDefault(); saveKeyConcurrency(keySave).catch(() => {}); }
  }, true);

  const keysBody = document.getElementById("keysBody");
  if (keysBody) new MutationObserver(() => scheduleKeyRefresh()).observe(keysBody, {childList:true});

  globalThis.__CHAT2API_WORKER_SETTINGS_RENDER_OWNER_V59__ = {
    owner: "retired-v21-5",
    column: "worker_settings",
    structural_updates: "none",
    polling: false,
    legacy_renderer_removed: true,
  };

  renderKeyConcurrency();
})();