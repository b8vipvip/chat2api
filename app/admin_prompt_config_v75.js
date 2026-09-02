(() => {
  const KEY = "__CHAT2API_PROMPT_CONFIG_V75__";
  if (window[KEY]) return;

  const legacy = window.__CHAT2API_PROMPT_CONFIG_V72__;
  const state = { revision: 75, legacy };
  window[KEY] = state;
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

  async function api(url, options = {}) {
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
    if (Object.prototype.hasOwnProperty.call(defaults, field)) return defaults[field];
    return "";
  }

  async function savePromptField(field, input, button) {
    if (!input || !button) return;
    const previous = button.textContent;
    button.disabled = true;
    button.textContent = "保存中…";
    setStatus(`正在保存${field === "system_default_prefix" ? "系统默认前置提示词" : field === "prefix" ? "自定义前置提示词" : "自定义后置提示词"}…`);
    try {
      const payload = await api("/api/admin/prompt-config", {
        method: "PUT",
        body: JSON.stringify(savedPayload({ [field]: input.value })),
      });
      setConfig(payload.config || {});
      input.value = payload.config?.[field] ?? "";
      setStatus(`保存成功 · revision=${payload.config?.revision || "-"} · 当前提示词已立即生效`);
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
    const bar = document.createElement("div");
    bar.className = "pc-inline-actions";
    bar.dataset.pcActions = field;
    bar.innerHTML = `<button type="button" data-pc-default="1">默认推荐</button><button type="button" data-pc-save="1">保存</button>`;
    input.insertAdjacentElement("afterend", bar);
    bar.querySelector('[data-pc-default="1"]').onclick = () => {
      input.value = String(recommended(field) ?? "");
      input.focus();
      setStatus("已填充系统默认推荐值；确认后点击右侧“保存”即可立即生效。");
    };
    const save = bar.querySelector('[data-pc-save="1"]');
    save.onclick = () => savePromptField(field, input, save);
  }

  function collectRules() {
    return [...$("pcRules").querySelectorAll("tr")].map(tr => ({
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
      tr.querySelector('[data-remove="1"]').onclick = () => tr.remove();
      body.appendChild(tr);
    }
  }

  async function saveRedaction(button) {
    const previous = button.textContent;
    button.disabled = true;
    button.textContent = "保存中…";
    setStatus("正在保存脱敏配置并校验正则…");
    try {
      const payload = await api("/api/admin/prompt-config", {
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
    bar.querySelector('[data-pc-default="1"]').onclick = () => {
      const defaults = config().recommended || {};
      if ($("pcRedactionEnabled")) $("pcRedactionEnabled").checked = Boolean(defaults.redaction_enabled);
      if ($("pcAudit")) $("pcAudit").checked = defaults.audit_final_prompt !== false;
      renderRules(Array.isArray(defaults.rules) ? defaults.rules : []);
      setStatus("已填充脱敏配置默认推荐值；点击右侧“保存”后生效。");
    };
    const save = bar.querySelector('[data-pc-save="1"]');
    save.onclick = () => saveRedaction(save);
  }

  function install() {
    const system = $("pcSystemDefaultPrefix");
    if (!system) return;

    const oldHelp = system.nextElementSibling;
    system.readOnly = false;
    system.removeAttribute("readonly");
    system.placeholder = "可编辑；留空表示不添加系统默认前置提示词";
    if (oldHelp?.classList?.contains("muted")) {
      oldHelp.textContent = "该提示词现在可编辑并持久化。修改后点击输入框右下角“保存”即可立即应用到新请求；“默认推荐”可恢复 chat2api 推荐值。";
    }

    addPromptActionBar("pcSystemDefaultPrefix", "system_default_prefix");
    addPromptActionBar("pcPrefix", "prefix");
    addPromptActionBar("pcSuffix", "suffix");
    installRedactionActions();

    const globalSave = $("pcSave");
    if (globalSave) globalSave.remove();
    const previewCardTitle = $("pcPreviewInput")?.closest(".card")?.querySelector("h3");
    if (previewCardTitle) previewCardTitle.textContent = "预览";
  }

  install();
})();
