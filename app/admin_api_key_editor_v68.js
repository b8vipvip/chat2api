(() => {
  const KEY = "__CHAT2API_API_KEY_EDITOR_V68__";
  if (globalThis[KEY]) return;

  const VERSION = 68;
  const SCOPE_ORDER = ["chat", "models", "files", "images", "audio"];
  const SCOPE_LABELS = new Map([
    ["admin", "管理员"],
    ["chat", "对话"],
    ["models", "模型列表"],
    ["files", "文件"],
    ["images", "图片生成"],
    ["audio", "音频/语音"],
  ]);
  const state = { rows: [], popup: null, loading: false };
  globalThis[KEY] = state;

  const escapeHtml = value => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

  function apiCall(path, options) {
    if (typeof globalThis.api !== "function") return Promise.reject(new Error("管理控制台 API 尚未就绪"));
    return globalThis.api(path, options);
  }

  function notify(message, kind = "ok") {
    if (typeof globalThis.status === "function") globalThis.status(message, kind);
  }

  function fmtTime(value) {
    if (typeof globalThis.fmtTime === "function") return globalThis.fmtTime(value);
    if (!value) return "-";
    try { return new Date(value).toLocaleString(); } catch (_) { return String(value); }
  }

  function scopeLabel(scope) {
    return SCOPE_LABELS.get(String(scope || "")) || `其他权限（${String(scope || "-")}）`;
  }

  function scopeSummary(row) {
    const scopes = Array.isArray(row?.scopes) ? row.scopes : [];
    return scopes.length ? scopes.map(scopeLabel).join("、") : "无权限";
  }

  function iconButton(action, keyId, title) {
    return `<button type="button" class="action" data-api-key-edit="${action}" data-key-id="${escapeHtml(keyId)}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}" style="padding:2px 7px;min-width:30px;margin-left:7px;line-height:1.35">✎</button>`;
  }

  function statusPill(row) {
    const usable = row?.enabled === true;
    return `<span class="pill ${usable ? "ok" : "bad"}">${usable ? "可用" : "停用"}</span>`;
  }

  function actions(row) {
    if (!row?.managed) return '<span class="muted">.env 管理</span>';
    if (row.revoked_at) return '<span class="muted">已撤销</span>';
    const keyId = escapeHtml(row.key_id || "");
    const configured = row.configured_enabled !== false;
    const copy = row.secret_recoverable
      ? `<button class="action" data-api-key-action="copy" data-key-id="${keyId}">复制 Key</button>`
      : "";
    return `<div class="rowactions">${copy}<button class="action" data-api-key-action="toggle" data-key-id="${keyId}" data-enabled="${configured ? "0" : "1"}">${configured ? "停用" : "启用"}</button><button class="action danger" data-api-key-action="revoke" data-key-id="${keyId}">撤销</button></div>`;
  }

  function rowHtml(row) {
    const editable = row?.managed && !row?.revoked_at;
    const name = `<span>${escapeHtml(row?.name || "-")}</span>${editable ? iconButton("name", row.key_id, "修改令牌名称") : ""}`;
    const permissions = `<span>${escapeHtml(scopeSummary(row))}</span>${editable ? iconButton("scopes", row.key_id, "编辑权限") : ""}`;
    return `<tr data-api-key-row="${escapeHtml(row?.key_id || "")}">
      <td><div style="display:flex;align-items:center;gap:2px">${name}</div></td>
      <td><code>${escapeHtml(row?.prefix || "-")}</code></td>
      <td>${statusPill(row)}</td>
      <td><div style="display:flex;align-items:center;gap:2px;white-space:normal;min-width:170px">${permissions}</div></td>
      <td>${fmtTime(row?.last_used_at)}</td>
      <td>${Number(row?.request_count || 0)} / ${Number(row?.estimated_tokens || 0)}</td>
      <td>${actions(row)}</td>
    </tr>`;
  }

  async function loadKeysV68() {
    const body = document.getElementById("keysBody");
    if (!body || state.loading) return;
    state.loading = true;
    try {
      const data = await apiCall("/api/admin/keys");
      state.rows = Array.isArray(data?.data) ? data.data : [];
      body.innerHTML = state.rows.length
        ? state.rows.map(rowHtml).join("")
        : '<tr><td colspan="7" class="muted">暂无 API Key</td></tr>';
      document.documentElement.dataset.chat2apiApiKeyEditorRevision = String(VERSION);
    } catch (error) {
      notify(String(error?.message || error), "bad");
    } finally {
      state.loading = false;
    }
  }

  function closePopup() {
    state.popup?.remove?.();
    state.popup = null;
  }

  function popupShell(anchor, width = 360) {
    closePopup();
    const popup = document.createElement("div");
    popup.dataset.apiKeyEditorPopup = "1";
    popup.style.cssText = `position:fixed;z-index:420;width:min(${width}px,calc(100vw - 24px));max-height:min(70vh,520px);overflow:auto;padding:12px;border:1px solid rgba(148,163,184,.32);border-radius:12px;background:#0f172a;box-shadow:0 20px 55px rgba(0,0,0,.55)`;
    document.body.appendChild(popup);
    const rect = anchor.getBoundingClientRect();
    const margin = 12;
    const maxLeft = Math.max(margin, window.innerWidth - popup.offsetWidth - margin);
    popup.style.left = `${Math.min(Math.max(margin, rect.left), maxLeft)}px`;
    const below = rect.bottom + 8;
    const preferredTop = below + Math.min(popup.offsetHeight, 420) <= window.innerHeight - margin
      ? below
      : Math.max(margin, rect.top - Math.min(popup.offsetHeight, 420) - 8);
    popup.style.top = `${preferredTop}px`;
    state.popup = popup;
    return popup;
  }

  function rowFor(keyId) {
    return state.rows.find(row => String(row.key_id || "") === String(keyId || "")) || null;
  }

  function openNameEditor(button, row) {
    const popup = popupShell(button, 360);
    popup.innerHTML = `<div style="font-weight:700;margin-bottom:9px">修改令牌名称</div>
      <input data-key-name-input maxlength="120" style="width:100%" value="${escapeHtml(row.name || "")}">
      <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:11px"><button class="action" data-key-popup-cancel>取消</button><button class="action good" data-key-name-save>保存</button></div>`;
    const input = popup.querySelector("[data-key-name-input]");
    input?.focus();
    input?.select();
    popup.querySelector("[data-key-popup-cancel]")?.addEventListener("click", closePopup);
    popup.querySelector("[data-key-name-save]")?.addEventListener("click", async () => {
      const name = String(input?.value || "").trim();
      if (!name) return notify("令牌名称不能为空", "bad");
      try {
        await apiCall(`/api/admin/keys/${encodeURIComponent(row.key_id)}/settings`, { method: "PATCH", body: { name } });
        closePopup();
        await loadKeysV68();
        notify("令牌名称已更新", "ok");
      } catch (error) { notify(String(error?.message || error), "bad"); }
    });
  }

  function openScopeEditor(button, row) {
    const selected = new Set(Array.isArray(row.scopes) ? row.scopes : []);
    const popup = popupShell(button, 390);
    const options = SCOPE_ORDER.map(scope => `<label style="display:flex;align-items:center;gap:9px;padding:8px 5px;border-bottom:1px solid rgba(148,163,184,.12)"><input type="checkbox" data-key-scope="${scope}" ${selected.has(scope) ? "checked" : ""}><span>${escapeHtml(scopeLabel(scope))}</span></label>`).join("");
    popup.innerHTML = `<div style="font-weight:700">编辑权限</div><div class="muted" style="font-size:12px;margin:4px 0 8px">勾选允许此令牌使用的接口权限。</div>${options}
      <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:11px"><button class="action" data-key-popup-cancel>取消</button><button class="action good" data-key-scope-save>保存</button></div>`;
    popup.querySelector("[data-key-popup-cancel]")?.addEventListener("click", closePopup);
    popup.querySelector("[data-key-scope-save]")?.addEventListener("click", async () => {
      const scopes = [...popup.querySelectorAll("[data-key-scope]:checked")].map(input => input.dataset.keyScope);
      if (!scopes.length) return notify("至少保留一个权限", "bad");
      try {
        await apiCall(`/api/admin/keys/${encodeURIComponent(row.key_id)}/settings`, { method: "PATCH", body: { scopes } });
        closePopup();
        await loadKeysV68();
        notify("令牌权限已更新", "ok");
      } catch (error) { notify(String(error?.message || error), "bad"); }
    });
  }

  async function action(button) {
    const keyId = String(button.dataset.keyId || "");
    const kind = String(button.dataset.apiKeyAction || "");
    if (!keyId) return;
    if (kind === "copy") {
      const data = await apiCall(`/api/admin/keys/${encodeURIComponent(keyId)}/secret`);
      await navigator.clipboard.writeText(data.token || "");
      notify("API Key 已复制", "ok");
      return;
    }
    if (kind === "toggle") {
      await apiCall(`/api/admin/keys/${encodeURIComponent(keyId)}`, { method: "PATCH", body: { enabled: button.dataset.enabled === "1" } });
      await loadKeysV68();
      return;
    }
    if (kind === "revoke") {
      if (!confirm("确定永久撤销？")) return;
      await apiCall(`/api/admin/keys/${encodeURIComponent(keyId)}`, { method: "DELETE" });
      await loadKeysV68();
    }
  }

  function installEvents() {
    document.addEventListener("click", event => {
      const target = event.target;
      const edit = target?.closest?.("[data-api-key-edit]");
      if (edit) {
        event.preventDefault();
        event.stopPropagation();
        const row = rowFor(edit.dataset.keyId);
        if (!row) return;
        if (edit.dataset.apiKeyEdit === "name") openNameEditor(edit, row);
        else if (edit.dataset.apiKeyEdit === "scopes") openScopeEditor(edit, row);
        return;
      }
      const actionButton = target?.closest?.("[data-api-key-action]");
      if (actionButton) {
        event.preventDefault();
        action(actionButton).catch(error => notify(String(error?.message || error), "bad"));
        return;
      }
      if (state.popup && !target?.closest?.("[data-api-key-editor-popup]")) closePopup();
    }, true);
    document.addEventListener("keydown", event => { if (event.key === "Escape") closePopup(); });
    window.addEventListener("resize", closePopup);
    window.addEventListener("scroll", closePopup, true);
  }

  function installLoadOwner() {
    globalThis.loadKeys = loadKeysV68;
    globalThis.chat2apiReloadApiKeysV68 = loadKeysV68;
  }

  function boot() {
    installLoadOwner();
    installEvents();
    if (document.getElementById("view-keys")?.classList.contains("active")) loadKeysV68();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
})();
