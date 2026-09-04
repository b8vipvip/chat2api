(() => {
  const KEY = "__CHAT2API_PROMPT_CONFIG_V72__";
  if (globalThis[KEY]) return;

  const state = { revision: 93, config: null };
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

  function ensureNavigation() {
    const nav = document.querySelector(".nav");
    if (!nav) return null;
    let button = nav.querySelector('[data-view="prompt-config"]');
    if (!button) {
      button = document.createElement("button");
      button.className = "nav-btn";
      button.dataset.view = "prompt-config";
      button.textContent = "提示词配置";
      const settings = nav.querySelector('[data-view="settings"]');
      nav.insertBefore(button, settings || null);
    }
    if (button.dataset.chat2apiPromptNavV93 !== "1") {
      button.dataset.chat2apiPromptNavV93 = "1";
      button.addEventListener("click", async () => {
        if (typeof globalThis.show === "function") await globalThis.show("prompt-config");
        await loadConfig();
      });
    }
    return button;
  }

  function ensureView() {
    if ($("view-prompt-config")) return $("view-prompt-config");
    const content = document.querySelector(".content") || document.querySelector("main");
    if (!content) return null;
    const section = document.createElement("section");
    section.id = "view-prompt-config";
    section.className = "view";
    section.innerHTML = `
      <div class="panel card">
        <h3>系统默认提示词</h3>
        <div class="muted">处理顺序：系统默认前置提示词 → 自定义前置提示词 → OpenAI messages/chat2api 基础提示词 → 自定义后置提示词 → 脱敏规则 → Worker/ChatGPT。</div>
        <label style="display:block;margin-top:12px">系统默认前置提示词</label>
        <textarea id="pcSystemDefaultPrefix" rows="9" style="width:100%;margin-top:6px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace" placeholder="可编辑；留空表示不添加系统默认前置提示词"></textarea>
        <div class="muted" style="margin-top:6px">修改后使用输入框右下角“保存”立即应用到新请求；“默认推荐”恢复 chat2api 推荐值。</div>
        <label style="display:block;margin-top:12px">自定义前置提示词</label>
        <textarea id="pcPrefix" rows="7" style="width:100%;margin-top:6px" placeholder="留空表示不添加。"></textarea>
        <label style="display:block;margin-top:12px">自定义后置提示词</label>
        <textarea id="pcSuffix" rows="7" style="width:100%;margin-top:6px" placeholder="留空表示不添加。"></textarea>
      </div>
      <div class="panel card">
        <h3>脱敏配置</h3>
        <label style="display:flex;gap:8px;align-items:center;margin:8px 0"><input id="pcRedactionEnabled" type="checkbox"> 启用脱敏规则</label>
        <label style="display:flex;gap:8px;align-items:center;margin:8px 0"><input id="pcAudit" type="checkbox"> 在请求记录中保存最终提示词（仅管理员可查看）</label>
        <div class="muted">规则使用正则表达式。flags 支持 i / m / s。脱敏发生在发送给 ChatGPT 之前。</div>
        <div style="overflow:auto;margin-top:10px"><table><thead><tr><th>启用</th><th>名称</th><th>正则 pattern</th><th>替换文本</th><th>flags</th><th>操作</th></tr></thead><tbody id="pcRules"></tbody></table></div>
        <button id="pcAddRule" type="button" style="margin-top:10px">新增规则</button>
      </div>
      <div class="panel card">
        <h3>预览</h3>
        <textarea id="pcPreviewInput" rows="5" style="width:100%" placeholder="输入一段示例提示词，预览当前已保存配置的最终结果"></textarea>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
          <button id="pcReload" type="button">重新加载</button>
          <button id="pcPreview" type="button">预览已保存配置</button>
        </div>
        <pre id="pcStatus" style="white-space:pre-wrap;margin-top:12px"></pre>
        <textarea id="pcPreviewOutput" rows="12" readonly style="width:100%;margin-top:8px" placeholder="预览结果"></textarea>
      </div>`;
    content.appendChild(section);
    $("pcAddRule")?.addEventListener("click", () => addRule({ enabled: true, name: "新规则", pattern: "", replacement: "[REDACTED]", flags: "" }));
    $("pcReload")?.addEventListener("click", loadConfig);
    $("pcPreview")?.addEventListener("click", previewConfig);
    return section;
  }

  function addRule(rule = {}) {
    const body = $("pcRules");
    if (!body) return;
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

  function renderConfig(config) {
    state.config = config || {};
    if ($("pcSystemDefaultPrefix")) $("pcSystemDefaultPrefix").value = config?.system_default_prefix || "";
    if ($("pcPrefix")) $("pcPrefix").value = config?.prefix || "";
    if ($("pcSuffix")) $("pcSuffix").value = config?.suffix || "";
    if ($("pcRedactionEnabled")) $("pcRedactionEnabled").checked = Boolean(config?.redaction_enabled);
    if ($("pcAudit")) $("pcAudit").checked = config?.audit_final_prompt !== false;
    const rules = $("pcRules");
    if (rules) {
      rules.innerHTML = "";
      for (const rule of config?.rules || []) addRule(rule);
    }
    const status = $("pcStatus");
    if (status) status.textContent = `配置版本 revision=${config?.revision || 1}${config?.updated_at ? ` · 更新时间 ${config.updated_at}` : ""}${config?.last_error ? `\n加载警告：${config.last_error}` : ""}`;
    document.dispatchEvent(new CustomEvent("chat2api:prompt-config-loaded", { detail: config || {} }));
  }

  async function loadConfig() {
    if (!ensureView()) return;
    const status = $("pcStatus");
    if (status) status.textContent = "正在加载…";
    try {
      const payload = await request("/api/admin/prompt-config");
      renderConfig(payload.config || {});
    } catch (error) {
      if (status) status.textContent = `加载失败：${error.message || error}`;
    }
  }

  async function previewConfig() {
    try {
      const payload = await request("/api/admin/prompt-config/preview", { method: "POST", body: JSON.stringify({ prompt: $("pcPreviewInput")?.value || "" }) });
      if ($("pcPreviewOutput")) $("pcPreviewOutput").value = payload.output || "";
      if ($("pcStatus")) $("pcStatus").textContent = `预览完成 · revision=${payload.meta?.revision || "-"} · 脱敏替换 ${payload.meta?.redaction_count || 0} 处`;
    } catch (error) {
      if ($("pcStatus")) $("pcStatus").textContent = `预览失败：${error.message || error}`;
    }
  }

  function ensurePromptModal() {
    if ($("requestPromptModal")) return;
    const wrap = document.createElement("div");
    wrap.id = "requestPromptModal";
    wrap.style.cssText = "display:none;position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.55);align-items:center;justify-content:center;padding:24px";
    wrap.innerHTML = `
      <div class="panel card" style="width:min(1000px,94vw);max-height:88vh;display:flex;flex-direction:column;gap:10px">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:12px"><h3 style="margin:0">最终完整提示词</h3><button id="requestPromptClose" type="button">关闭</button></div>
        <div id="requestPromptMeta" class="muted"></div>
        <textarea id="requestPromptText" readonly style="width:100%;min-height:55vh;resize:vertical;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap"></textarea>
        <div style="display:flex;gap:8px"><button id="requestPromptSelect" type="button">全选</button><button id="requestPromptCopy" type="button">复制提示词</button></div>
      </div>`;
    document.body.appendChild(wrap);
    $("requestPromptClose").onclick = () => { wrap.style.display = "none"; };
    wrap.addEventListener("click", event => { if (event.target === wrap) wrap.style.display = "none"; });
    $("requestPromptSelect").onclick = () => { $("requestPromptText")?.focus(); $("requestPromptText")?.select(); };
    $("requestPromptCopy").onclick = async () => {
      const text = $("requestPromptText")?.value || "";
      try { await navigator.clipboard.writeText(text); }
      catch (_) { $("requestPromptText")?.focus(); $("requestPromptText")?.select(); document.execCommand("copy"); }
    };
  }

  async function showRequestPrompt(requestId) {
    ensurePromptModal();
    if ($("requestPromptMeta")) $("requestPromptMeta").textContent = `请求 ${requestId} · 正在加载…`;
    if ($("requestPromptText")) $("requestPromptText").value = "";
    if ($("requestPromptModal")) $("requestPromptModal").style.display = "flex";
    try {
      const row = await request(`/api/admin/requests/${encodeURIComponent(requestId)}`);
      const text = row.final_prompt || "";
      if ($("requestPromptText")) $("requestPromptText").value = text || "该请求没有保存最终提示词。旧请求或关闭“保存最终提示词”的请求不会包含此字段。";
      if ($("requestPromptMeta")) $("requestPromptMeta").textContent = `请求 ${requestId} · ${row.final_prompt_chars ?? text.length} 字符 · prompt revision=${row.prompt_config_revision ?? "历史/未知"}${row.prompt_redaction_enabled ? " · 已启用脱敏" : ""}`;
    } catch (error) {
      if ($("requestPromptText")) $("requestPromptText").value = `加载失败：${error.message || error}`;
      if ($("requestPromptMeta")) $("requestPromptMeta").textContent = `请求 ${requestId}`;
    }
  }

  globalThis.showRequestPromptV72 = showRequestPrompt;
  state.load = loadConfig;
  state.render = renderConfig;

  ensureNavigation();
  ensureView();
  ensurePromptModal();
  if ($("view-prompt-config")?.classList.contains("active")) loadConfig();
})();
