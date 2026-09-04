(() => {
  const KEY = "__CHAT2API_PROMPT_CONFIG_V75__";
  if (globalThis[KEY]) return;

  const legacy = globalThis.__CHAT2API_PROMPT_CONFIG_V72__;
  const state = { revision: 93, legacy };
  globalThis[KEY] = state;
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

  async function request(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    let payload = null;
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(payload?.detail || `HTTP ${response.status}`);
    return payload || {};
  }

  function config() {
    return legacy?.config || {};
  }

  function setConfig(next) {
    if (legacy) legacy.config = next || {};
  }

  function setStatus(text) {
    if ($("pcStatus")) $("pcStatus").textContent = text;
  }

  function savedPayload(overrides = {}) {
    const current = config();
    return {
      system_default_prefix: current.system_default_prefix ?? "",
      prefix: current.prefix ?? "",
      suffix: current.suffix ?? "",
      redaction_enabled: Boolean(current.redaction_enabled),
      audit_final_prompt: current.audit_final_prompt !== false,
      rules: Array.isArray(current.rules) ? current.rules.map(rule => ({ ...rule })) : [],
      ...overrides,
    };
  }

  function recommended(field) {
    const defaults = config().recommended || {};
    return Object.prototype.hasOwnProperty.call(defaults, field) ? defaults[field] : "";
  }

  async function savePromptField(field, input, button) {
    if (!input || !button) return;
    const previous = button.textContent;
    button.disabled = true;
    button.textContent = "保存中…";
    try {
      const payload = await request("/api/admin/prompt-config", {
        method: "PUT",
        body: JSON.stringify(savedPayload({ [field]: input.value })),
      });
      setConfig(payload.config || {});
      input.value = payload.config?.[field] ?? "";
      setStatus(`保存成功 · revision=${payload.config?.revision || "-"} · 新请求立即生效`);
    } catch (error) {
      setStatus(`保存失败：${error.message || error}`);
    } finally {
      button.disabled = false;
      button.textContent = previous;
    }
  }

  function addPromptActionBar(inputId, field) {
    const input = $(inputId);
    if (!input || document.querySelector(`[data-pc-actions="${field}"]`)) return;
    input.readOnly = false;
    input.removeAttribute("readonly");
    const bar = document.createElement("div");
    bar.className = "pc-inline-actions";
    bar.dataset.pcActions = field;
    bar.innerHTML = `<button type="button" data-pc-default="1">默认推荐</button><button type="button" data-pc-save="1">保存</button>`;
    input.insertAdjacentElement("afterend", bar);
    bar.querySelector('[data-pc-default="1"]')?.addEventListener("click", () => {
      input.value = String(recommended(field) ?? "");
      input.focus();
      setStatus("已填充系统默认推荐值；点击右侧“保存”后生效。");
    });
    const save = bar.querySelector('[data-pc-save="1"]');
    save?.addEventListener("click", () => savePromptField(field, input, save));
  }

  function collectRules() {
    return [...($("pcRules")?.querySelectorAll("tr") || [])].map(tr => ({
      enabled: tr.querySelector('[data-field="enabled"]')?.checked === true,
      name: tr.querySelector('[data-field="name"]')?.value || "",
      pattern: tr.querySelector('[data-field="pattern"]')?.value || "",
      replacement: tr.querySelector('[data-field="replacement"]')?.value || "",
      flags: tr.querySelector('[data-field="flags"]')?.value || "",
    }));
  }

  function renderRules(rules) {
    const body = $("pcRules");
    if (!body) return;
    body.innerHTML = "";
    for (const rule of rules || []) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><input data-field="enabled" type="checkbox" ${rule.enabled ? "checked" : ""}></td>
        <td><input data-field="name" value="${esc(rule.name || "")}" style="min-width:110px"></td>
        <td><input data-field="pattern" value="${esc(rule.pattern || "")}" style="min-width:320px;font-family:monospace"></td>
        <td><input data-field="replacement" value="${esc(rule.replacement || "")}" style="min-width:180px"></td>
        <td><input data-field="flags" value="${esc(rule.flags || "")}" style="width:64px"></td>
        <td><button type="button" data-remove="1">删除</button></td>`;
      tr.querySelector('[data-remove="1"]')?.addEventListener("click", () => tr.remove());
      body.appendChild(tr);
    }
  }

  async function saveRedaction(button) {
    const previous = button.textContent;
    button.disabled = true;
    button.textContent = "保存中…";
    try {
      const payload = await request("/api/admin/prompt-config", {
        method: "PUT",
        body: JSON.stringify(savedPayload({
          redaction_enabled: $("pcRedactionEnabled")?.checked === true,
          audit_final_prompt: $("pcAudit")?.checked !== false,
          rules: collectRules(),
        })),
      });
      setConfig(payload.config || {});
      setStatus(`脱敏配置保存成功 · revision=${payload.config?.revision || "-"} · 新请求立即生效`);
    } catch (error) {
      setStatus(`保存失败：${error.message || error}`);
    } finally {
      button.disabled = false;
      button.textContent = previous;
    }
  }

  function installRedactionActions() {
    const add = $("pcAddRule");
    if (!add || document.querySelector('[data-pc-actions="redaction"]')) return;
    const bar = document.createElement("div");
    bar.className = "pc-inline-actions";
    bar.dataset.pcActions = "redaction";
    bar.innerHTML = `<button type="button" data-pc-default="1">默认推荐</button><button type="button" data-pc-save="1">保存</button>`;
    add.insertAdjacentElement("afterend", bar);
    bar.querySelector('[data-pc-default="1"]')?.addEventListener("click", () => {
      const defaults = config().recommended || {};
      if ($("pcRedactionEnabled")) $("pcRedactionEnabled").checked = Boolean(defaults.redaction_enabled);
      if ($("pcAudit")) $("pcAudit").checked = defaults.audit_final_prompt !== false;
      renderRules(Array.isArray(defaults.rules) ? defaults.rules : []);
      setStatus("已填充脱敏配置默认推荐值；点击右侧“保存”后生效。");
    });
    const save = bar.querySelector('[data-pc-save="1"]');
    save?.addEventListener("click", () => saveRedaction(save));
  }

  function install() {
    if (!$("pcSystemDefaultPrefix")) return false;
    addPromptActionBar("pcSystemDefaultPrefix", "system_default_prefix");
    addPromptActionBar("pcPrefix", "prefix");
    addPromptActionBar("pcSuffix", "suffix");
    installRedactionActions();
    return true;
  }

  // v93 authority boundary: prompt configuration owns only its editor and modal.
  // Request history rows are rendered once by admin_request_history_v93.js. This
  // module must never observe or mutate #rqBody and must never replace show() or
  // loadRequests().
  if (!install()) setTimeout(install, 0);
  document.addEventListener("chat2api:prompt-config-loaded", install);
})();
