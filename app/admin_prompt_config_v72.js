(() => {
  const KEY = "__CHAT2API_PROMPT_CONFIG_V72__";
  if (window[KEY]) return;

  const state = { revision: 73, config: null, baseShow: window.show, baseLoadRequests: window.loadRequests };
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

  function ensureNavigation() {
    const nav = document.querySelector(".nav");
    if (!nav || nav.querySelector('[data-view="prompt-config"]')) return;
    const button = document.createElement("button");
    button.className = "nav-btn";
    button.dataset.view = "prompt-config";
    button.textContent = "提示词配置";
    const settings = nav.querySelector('[data-view="settings"]');
    nav.insertBefore(button, settings || null);
  }

  function ensureView() {
    if ($("view-prompt-config")) return;
    const main = document.querySelector("main");
    if (!main) return;
    const section = document.createElement("section");
    section.id = "view-prompt-config";
    section.className = "view";
    section.innerHTML = `
      <div class="card">
        <h3>系统默认提示词</h3>
        <div class="muted">处理顺序：系统默认前置提示词 → 自定义前置提示词 → OpenAI messages/chat2api 基础提示词 → 自定义后置提示词 → 脱敏规则 → Worker/ChatGPT。请求记录中的“提示词”显示最终实际发送内容。</div>
        <label style="display:block;margin-top:12px">系统默认前置提示词</label>
        <textarea id="pcSystemDefaultPrefix" rows="9" readonly style="width:100%;margin-top:6px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--surface-2,#f6f7f9)" placeholder="系统当前没有默认前置提示词"></textarea>
        <div class="muted" style="margin-top:6px">该内容由 chat2api 运行时内置，用于保证 API 请求不调用 ChatGPT 外部账户连接器。这里只读显示，避免控制台配置意外关闭运行时安全边界。</div>
        <label style="display:block;margin-top:12px">自定义前置提示词</label>
        <textarea id="pcPrefix" rows="7" style="width:100%;margin-top:6px" placeholder="留空表示不添加。会插入到系统默认前置提示词之后。"></textarea>
        <label style="display:block;margin-top:12px">自定义后置提示词</label>
        <textarea id="pcSuffix" rows="7" style="width:100%;margin-top:6px" placeholder="留空表示不添加。会插入到 chat2api 生成提示词之后。"></textarea>
      </div>
      <div class="card">
        <h3>脱敏配置</h3>
        <label style="display:flex;gap:8px;align-items:center;margin:8px 0"><input id="pcRedactionEnabled" type="checkbox"> 启用脱敏规则</label>
        <label style="display:flex;gap:8px;align-items:center;margin:8px 0"><input id="pcAudit" type="checkbox"> 在请求记录中保存最终提示词（仅管理员可查看）</label>
        <div class="muted">规则使用正则表达式。flags 支持 i / m / s。脱敏发生在发送给 ChatGPT 之前，因此日志与 ChatGPT 收到的内容保持一致。</div>
        <div style="overflow:auto;margin-top:10px">
          <table><thead><tr><th>启用</th><th>名称</th><th>正则 pattern</th><th>替换文本</th><th>flags</th><th>操作</th></tr></thead><tbody id="pcRules"></tbody></table>
        </div>
        <button id="pcAddRule" class="secondary" style="margin-top:10px">新增规则</button>
      </div>
      <div class="card">
        <h3>预览与保存</h3>
        <textarea id="pcPreviewInput" rows="5" style="width:100%" placeholder="输入一段示例提示词，预览当前已保存配置的最终结果"></textarea>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
          <button id="pcSave">保存配置</button>
          <button id="pcReload" class="secondary">重新加载</button>
          <button id="pcPreview" class="secondary">预览已保存配置</button>
        </div>
        <pre id="pcStatus" style="white-space:pre-wrap;margin-top:12px"></pre>
        <textarea id="pcPreviewOutput" rows="12" readonly style="width:100%;margin-top:8px" placeholder="预览结果"></textarea>
      </div>`;
    const settings = $("view-settings");
    main.insertBefore(section, settings || null);

    $("pcAddRule").onclick = () => addRule({ enabled: true, name: "新规则", pattern: "", replacement: "[REDACTED]", flags: "" });
    $("pcSave").onclick = saveConfig;
    $("pcReload").onclick = loadConfig;
    $("pcPreview").onclick = previewConfig;
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
      <td><button class="secondary" data-remove="1">删除</button></td>`;
    tr.querySelector('[data-remove="1"]').onclick = () => tr.remove();
    body.appendChild(tr);
  }

  function renderConfig(config) {
    state.config = config || {};
    $("pcSystemDefaultPrefix").value = config?.system_default_prefix || "";
    $("pcPrefix").value = config?.prefix || "";
    $("pcSuffix").value = config?.suffix || "";
    $("pcRedactionEnabled").checked = Boolean(config?.redaction_enabled);
    $("pcAudit").checked = config?.audit_final_prompt !== false;
    $("pcRules").innerHTML = "";
    for (const rule of config?.rules || []) addRule(rule);
    const systemChars = (config?.system_default_prefix || "").length;
    $("pcStatus").textContent = `配置版本 revision=${config?.revision || 1} · 系统默认前置提示词 ${systemChars} 字符${config?.updated_at ? ` · 更新时间 ${config.updated_at}` : ""}${config?.last_error ? `\n加载警告：${config.last_error}` : ""}`;
  }

  function collectConfig() {
    const rules = [...$("pcRules").querySelectorAll("tr")].map(tr => ({
      enabled: tr.querySelector('[data-field="enabled"]').checked,
      name: tr.querySelector('[data-field="name"]').value,
      pattern: tr.querySelector('[data-field="pattern"]').value,
      replacement: tr.querySelector('[data-field="replacement"]').value,
      flags: tr.querySelector('[data-field="flags"]').value,
    }));
    return {
      prefix: $("pcPrefix").value,
      suffix: $("pcSuffix").value,
      redaction_enabled: $("pcRedactionEnabled").checked,
      audit_final_prompt: $("pcAudit").checked,
      rules,
    };
  }

  async function loadConfig() {
    if (!$("pcStatus")) return;
    $("pcStatus").textContent = "正在加载…";
    try {
      const payload = await api("/api/admin/prompt-config");
      renderConfig(payload.config || {});
    } catch (error) {
      $("pcStatus").textContent = `加载失败：${error.message || error}`;
    }
  }

  async function saveConfig() {
    $("pcStatus").textContent = "正在保存并校验正则…";
    try {
      const payload = await api("/api/admin/prompt-config", { method: "PUT", body: JSON.stringify(collectConfig()) });
      renderConfig(payload.config || {});
      $("pcStatus").textContent = `保存成功 · revision=${payload.config?.revision || "-"} · 系统默认前置提示词保持只读 · 新请求立即生效`;
    } catch (error) {
      $("pcStatus").textContent = `保存失败：${error.message || error}`;
    }
  }

  async function previewConfig() {
    try {
      const payload = await api("/api/admin/prompt-config/preview", { method: "POST", body: JSON.stringify({ prompt: $("pcPreviewInput").value }) });
      $("pcPreviewOutput").value = payload.output || "";
      const redactions = payload.meta?.redaction_count || 0;
      $("pcStatus").textContent = `预览完成 · revision=${payload.meta?.revision || "-"} · 系统默认前置提示词=${payload.meta?.system_default_prefix_applied ? "已应用" : "未应用"} · 脱敏替换 ${redactions} 处`;
    } catch (error) {
      $("pcStatus").textContent = `预览失败：${error.message || error}`;
    }
  }

  function ensurePromptModal() {
    if ($("requestPromptModal")) return;
    const wrap = document.createElement("div");
    wrap.id = "requestPromptModal";
    wrap.style.cssText = "display:none;position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.55);align-items:center;justify-content:center;padding:24px";
    wrap.innerHTML = `
      <div class="card" style="width:min(1000px,94vw);max-height:88vh;display:flex;flex-direction:column;gap:10px">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:12px"><h3 style="margin:0">最终完整提示词</h3><button id="requestPromptClose" class="secondary">关闭</button></div>
        <div id="requestPromptMeta" class="muted"></div>
        <textarea id="requestPromptText" readonly style="width:100%;min-height:55vh;resize:vertical;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap"></textarea>
        <div style="display:flex;gap:8px"><button id="requestPromptSelect" class="secondary">全选</button><button id="requestPromptCopy">复制提示词</button></div>
      </div>`;
    document.body.appendChild(wrap);
    $("requestPromptClose").onclick = () => { wrap.style.display = "none"; };
    wrap.addEventListener("click", event => { if (event.target === wrap) wrap.style.display = "none"; });
    $("requestPromptSelect").onclick = () => { $("requestPromptText").focus(); $("requestPromptText").select(); };
    $("requestPromptCopy").onclick = async () => {
      const text = $("requestPromptText").value;
      try { await navigator.clipboard.writeText(text); }
      catch (_) { $("requestPromptText").focus(); $("requestPromptText").select(); document.execCommand("copy"); }
      $("requestPromptCopy").textContent = "已复制";
      setTimeout(() => { if ($("requestPromptCopy")) $("requestPromptCopy").textContent = "复制提示词"; }, 1200);
    };
  }

  async function showRequestPrompt(requestId) {
    ensurePromptModal();
    $("requestPromptMeta").textContent = `请求 ${requestId} · 正在加载…`;
    $("requestPromptText").value = "";
    $("requestPromptModal").style.display = "flex";
    try {
      const row = await api(`/api/admin/requests/${encodeURIComponent(requestId)}`);
      const text = row.final_prompt || "";
      $("requestPromptText").value = text || "该请求没有保存最终提示词。旧请求或关闭“保存最终提示词”的请求不会包含此字段。";
      $("requestPromptMeta").textContent = `请求 ${requestId} · ${row.final_prompt_chars ?? text.length} 字符 · prompt revision=${row.prompt_config_revision ?? "历史/未知"}${row.prompt_redaction_enabled ? " · 已启用脱敏" : ""}`;
    } catch (error) {
      $("requestPromptText").value = `加载失败：${error.message || error}`;
      $("requestPromptMeta").textContent = `请求 ${requestId}`;
    }
  }
  window.showRequestPromptV72 = showRequestPrompt;

  function ensurePromptColumnHeader() {
    const table = $("rqBody")?.closest("table");
    const row = table?.querySelector("thead tr");
    if (!row || row.querySelector('[data-prompt-column="1"]')) return;
    const th = document.createElement("th");
    th.dataset.promptColumn = "1";
    th.textContent = "提示词";
    row.appendChild(th);
  }

  function augmentRequestRows() {
    ensurePromptColumnHeader();
    const body = $("rqBody");
    if (!body) return;
    for (const tr of body.querySelectorAll("tr")) {
      if (tr.querySelector('[data-prompt-cell="1"]')) continue;
      const logButton = [...tr.querySelectorAll("button")].find(button => /日志/.test(button.textContent || ""));
      const onclick = logButton?.getAttribute("onclick") || "";
      const match = onclick.match(/showReq\(['\"]([^'\"]+)/);
      const td = document.createElement("td");
      td.dataset.promptCell = "1";
      if (match?.[1]) {
        const button = document.createElement("button");
        button.className = "secondary";
        button.textContent = "查看提示词";
        button.onclick = () => showRequestPrompt(match[1]);
        td.appendChild(button);
      } else {
        td.textContent = "-";
      }
      tr.appendChild(td);
    }
  }

  function patchRequestLoader() {
    if (typeof state.baseLoadRequests !== "function") return;
    window.loadRequests = async (...args) => {
      const result = await state.baseLoadRequests(...args);
      augmentRequestRows();
      return result;
    };
    if ($("rqGo")) $("rqGo").onclick = window.loadRequests;
    augmentRequestRows();
  }

  function patchShow() {
    if (typeof state.baseShow !== "function") return;
    window.show = async view => {
      const result = await state.baseShow(view);
      if (view === "prompt-config") await loadConfig();
      if (view === "requests") augmentRequestRows();
      return result;
    };
    for (const button of document.querySelectorAll(".nav-btn")) {
      button.onclick = () => window.show(button.dataset.view);
    }
  }

  ensureNavigation();
  ensureView();
  ensurePromptModal();
  patchRequestLoader();
  patchShow();
})();
