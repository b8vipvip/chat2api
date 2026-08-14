(() => {
  const VERSION = "0.20.1";
  const TEXT_MODELS = ["gpt-5.6-sol", "gpt-5.5", "gpt-5.5-mini"];
  const MINI_MODEL = "gpt-5.5-mini";
  const MODEL_META = {
    "gpt-5.6-sol": {
      name: "GPT-5.6 Sol",
      category: "text",
      kicker: "旗舰推理 · 多模态",
      summary: "复杂分析、代码、视觉与文件理解的主力模型。",
      badges: ["文本", "视觉", "文件", "推理"],
      input: "文本 / 图片 / 文件",
      output: "文本",
      reasoning: "low / medium / high · 默认 medium",
      endpoints: ["POST /v1/responses", "POST /v1/chat/completions", "POST /v1/completions"],
      useCases: ["复杂分析", "代码", "视觉 / 文件理解"],
      sample: {model: "gpt-5.6-sol", reasoning_effort: "medium", messages: [{role: "user", content: "你好"}]},
    },
    "gpt-5.5": {
      name: "GPT-5.5",
      category: "text",
      kicker: "通用推理 · 多模态",
      summary: "通用文本与多模态模型，支持极速 / 中 / 高三档推理强度。",
      badges: ["文本", "视觉", "文件", "推理"],
      input: "文本 / 图片 / 文件",
      output: "文本",
      reasoning: "low / medium / high · 默认 medium",
      endpoints: ["POST /v1/responses", "POST /v1/chat/completions", "POST /v1/completions"],
      useCases: ["日常问答", "结构化分析", "视觉 / 文件理解"],
      sample: {model: "gpt-5.5", reasoning_effort: "medium", messages: [{role: "user", content: "你好"}]},
    },
    "gpt-5.5-mini": {
      name: "GPT-5.5 Mini",
      category: "text",
      kicker: "Free 账户默认 · 自动回退",
      summary: "Free 账户专用逻辑模型。优先使用在线 Free 扩展的默认模型；没有可用 Free 扩展时自动使用普通扩展的 GPT-5.5 + low（极速）。",
      badges: ["文本", "Free", "自动路由"],
      input: "文本",
      output: "文本",
      reasoning: "Free 默认；回退时 low / 极速",
      endpoints: ["POST /v1/responses", "POST /v1/chat/completions", "POST /v1/completions"],
      useCases: ["Free 账户", "低成本文本", "快速回复"],
      sample: {model: "gpt-5.5-mini", messages: [{role: "user", content: "你好"}]},
    },
    "gpt-image": {
      name: "GPT Image",
      category: "image",
      kicker: "图片生成 · 参考图",
      summary: "在 ChatGPT 浏览器会话中执行图片生成，支持参考图。",
      badges: ["图片生成", "参考图", "Base64"],
      input: "文本 / 参考图片",
      output: "图片",
      reasoning: "不适用",
      endpoints: ["POST /v1/images/generations"],
      useCases: ["文生图", "参考图再创作"],
      sample: {model: "gpt-image", prompt: "生成一张简洁的蓝天白云测试图片。", response_format: "b64_json"},
    },
    "gpt-live": {
      name: "GPT Live",
      category: "audio",
      kicker: "语音生成 · 语音对话",
      summary: "ChatGPT Voice 浏览器链路的主语音模型 ID。",
      badges: ["语音生成", "语音对话", "音频"],
      input: "文本 / 音频",
      output: "音频 / 转写",
      reasoning: "不适用",
      endpoints: ["POST /v1/audio/speech", "POST /v1/audio/conversations"],
      useCases: ["语音生成", "语音问答"],
      sample: {model: "gpt-live", input: "请说：语音测试成功", response_format: "b64_json"},
    },
    "gpt-live-mini": {
      name: "GPT Live Mini",
      category: "audio",
      kicker: "轻量语音模型",
      summary: "与 GPT Live 共用音频入口的轻量模型 ID。",
      badges: ["语音", "Audio API"],
      input: "文本 / 音频",
      output: "音频 / 转写",
      reasoning: "不适用",
      endpoints: ["POST /v1/audio/speech", "POST /v1/audio/conversations"],
      useCases: ["轻量语音", "客户端模型分流"],
      sample: {model: "gpt-live-mini", input: "请说：语音测试成功", response_format: "b64_json"},
    },
  };

  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = `Server Console · v${VERSION}`;

  function renderTestModels() {
    const select = document.getElementById("testModel");
    if (!select) return;
    const previous = TEXT_MODELS.includes(select.value) ? select.value : TEXT_MODELS[0];
    select.innerHTML = TEXT_MODELS.map(id => `<option value="${id}">${id}</option>`).join("");
    select.value = previous;
    updateReasoningState();
  }

  function updateReasoningState() {
    const select = document.getElementById("testModel");
    const reasoning = document.getElementById("testReasoning");
    if (!select || !reasoning) return;
    const mini = select.value === MINI_MODEL;
    reasoning.disabled = mini;
    reasoning.title = mini
      ? "gpt-5.5-mini 使用 Free 账户默认能力；无 Free 扩展时由服务端回退到 GPT-5.5 + 极速"
      : "low=极速，medium=中，high=高";
    const label = reasoning.previousElementSibling;
    if (label?.tagName === "LABEL") label.textContent = mini ? "推理强度（mini 自动）" : "推理强度";
  }

  const modelSelect = document.getElementById("testModel");
  if (modelSelect) modelSelect.addEventListener("change", updateReasoningState);
  renderTestModels();
  fillModels = renderTestModels;

  streamChat = async function streamChatV201(model, prompt, attachments = [], testToken = key()) {
    const resolvedModel = TEXT_MODELS.includes(model) ? model : TEXT_MODELS[0];
    const started = performance.now();
    let first = null;
    let totalText = 0;
    let meta = null;
    let rawError = "";
    const body = {
      model: resolvedModel,
      messages: [{ role: "user", content: prompt }],
      attachments,
      stream: true,
      timeout: 300,
    };
    const effort = document.getElementById("testReasoning")?.value || "";
    if (resolvedModel !== MINI_MODEL && effort) body.reasoning_effort = effort;
    const response = await fetch("/v1/chat/completions", {
      method: "POST",
      headers: { ...headers(testToken), "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        for (const line of block.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          const payload = line.slice(6);
          if (payload === "[DONE]") continue;
          let data;
          try { data = JSON.parse(payload); } catch (_) { continue; }
          if (data.error) { rawError = data.error.message || "stream error"; continue; }
          const content = data.choices?.[0]?.delta?.content || "";
          if (content) {
            if (first === null) first = performance.now() - started;
            totalText += content.length;
          }
          if (data.usage || data.chat2api) meta = { usage: data.usage, chat2api: data.chat2api };
        }
      }
    }
    if (rawError) throw new Error(rawError);
    return {
      first_token_ms: first,
      total_ms: performance.now() - started,
      response_chars: totalText,
      meta,
      requested_reasoning_effort: resolvedModel === MINI_MODEL ? null : (effort || null),
    };
  };

  function renderCurrentDocs() {
    const panel = document.querySelector("#view-docs .panel");
    if (!panel) return;
    const base = location.origin;
    panel.innerHTML = `
      <h1>开发文档</h1>
      <p>Base URL：<code>${esc(base)}/v1</code>。业务调用使用控制台创建的 managed API Key；Chrome Bridge 必须在线。</p>
      <h2>当前公开模型</h2>
      <table><thead><tr><th>模型 ID</th><th>用途</th><th>推理</th></tr></thead><tbody>
        <tr><td><code>gpt-5.6-sol</code></td><td>文本 / 视觉 / 文件理解</td><td><code>low / medium / high</code>，默认 <code>medium</code></td></tr>
        <tr><td><code>gpt-5.5</code></td><td>文本 / 视觉 / 文件理解</td><td><code>low / medium / high</code>，默认 <code>medium</code></td></tr>
        <tr><td><code>gpt-5.5-mini</code></td><td>Free 账户文本路由</td><td>不需要指定；无 Free 扩展时回退 GPT-5.5 + <code>low</code></td></tr>
        <tr><td><code>gpt-image</code></td><td>图片生成 / 参考图</td><td>不适用</td></tr>
        <tr><td><code>gpt-live</code></td><td>语音生成 / 语音对话</td><td>不适用</td></tr>
        <tr><td><code>gpt-live-mini</code></td><td>轻量语音模型</td><td>不适用</td></tr>
      </tbody></table>
      <h2>Chat Completions</h2>
      <div class="codebox">POST ${esc(base)}/v1/chat/completions\nAuthorization: Bearer YOUR_API_KEY\nContent-Type: application/json\n\n{\n  "model": "gpt-5.6-sol",\n  "reasoning_effort": "medium",\n  "messages": [{"role":"user","content":"你好"}],\n  "stream": true\n}</div>
      <h2>Free / Mini</h2>
      <div class="codebox">POST ${esc(base)}/v1/chat/completions\nAuthorization: Bearer YOUR_API_KEY\nContent-Type: application/json\n\n{\n  "model": "gpt-5.5-mini",\n  "messages": [{"role":"user","content":"你好"}],\n  "stream": true\n}</div>
      <p><code>gpt-5.5-mini</code> 优先随机选择在线且空闲的 Free 扩展，并直接使用 Free 页面默认模型，不操作模型或推理菜单；没有可用 Free 扩展时自动使用其它兼容扩展执行 GPT-5.5 + 极速。</p>
      <h2>Responses API</h2>
      <div class="codebox">POST ${esc(base)}/v1/responses\n{\n  "model": "gpt-5.6-sol",\n  "reasoning": {"effort":"medium"},\n  "input": "你好",\n  "stream": true\n}</div>
      <h2>图片与语音</h2>
      <div class="codebox">POST ${esc(base)}/v1/images/generations\n{"model":"gpt-image","prompt":"生成一张测试图片","response_format":"b64_json"}\n\nPOST ${esc(base)}/v1/audio/speech\n{"model":"gpt-live","input":"请说：语音测试成功"}</div>
      <h2>Bridge 重试与恢复</h2>
      <p>Chrome Bridge 已内置 WebSocket 指数退避自动重连、页面发送最多 3 次确认重试、模型选择被动恢复和 Composer 预检恢复。若请求在服务端因 <code>409 busy</code> 或 <code>503 offline</code> 被拒绝，它尚未到达扩展，因此应由调用方或上游 Control Plane 做等待 / 串行 / 重试。</p>
      <h2>状态码</h2>
      <ul><li>401：业务 API Key 无效、过期或停用。</li><li>409：选定扩展正忙。</li><li>503：没有可用的在线 Chrome Bridge。</li><li>504：等待 ChatGPT / Images 超时。</li></ul>
      <p><a href="/docs" target="_blank" style="color:var(--accent)">打开 FastAPI OpenAPI</a></p>`;
  }
  renderDocs = renderCurrentDocs;
  if (document.getElementById("view-docs")?.classList.contains("active")) renderCurrentDocs();

  const plazaState = { filter: "all", query: "", live: new Map() };

  function categoryLabel(category) {
    return category === "text" ? "文本 / 多模态" : category === "image" ? "图片" : "语音";
  }

  function plazaTemplate() {
    return `
      <div class="modelPlazaHero">
        <div class="modelPlazaHeroMain"><div class="modelPlazaEyebrow">Model catalog · chat2api</div><h1>模型广场</h1><p>当前公开模型目录。页面只展示仍受支持的模型 ID，并结合服务端实时目录标记在线可用性。</p></div>
        <div class="modelPlazaStats">
          <div class="modelPlazaStat"><span>已发布模型</span><strong>${Object.keys(MODEL_META).length}</strong></div>
          <div class="modelPlazaStat"><span>当前可用</span><strong id="modelPlazaOnlineV201">-</strong></div>
          <div class="modelPlazaStat"><span>文本模型</span><strong>3</strong></div>
          <div class="modelPlazaStat"><span>图片 / 语音</span><strong>3</strong></div>
        </div>
      </div>
      <div class="modelPlazaToolbar">
        <input class="field modelPlazaSearch" id="modelPlazaSearchV201" placeholder="搜索模型 ID、能力或使用场景…">
        <div class="modelPlazaFilters" id="modelPlazaFiltersV201">
          <button class="modelPlazaFilter active" data-filter="all">全部</button>
          <button class="modelPlazaFilter" data-filter="text">文本 / 多模态</button>
          <button class="modelPlazaFilter" data-filter="image">图片</button>
          <button class="modelPlazaFilter" data-filter="audio">语音</button>
        </div>
        <button class="action" id="modelPlazaRefreshV201">刷新状态</button>
      </div>
      <div class="modelPlazaGrid" id="modelPlazaGridV201"></div>
      <div class="modelPlazaNotice">模型 ID 以本页和 <code>/v1/models</code> 为准。已停止公开的历史别名不会再显示在控制台、测试场或开发文档中。</div>`;
  }

  function card(id, meta) {
    const live = plazaState.live.get(id) || null;
    const clients = Array.isArray(live?.clients) ? live.clients : [];
    const online = clients.length > 0;
    const badges = meta.badges.map(item => `<span class="modelBadge">${esc(item)}</span>`).join("");
    const samplePath = meta.category === "text" ? "/v1/chat/completions" : meta.category === "image" ? "/v1/images/generations" : "/v1/audio/speech";
    const sample = `POST ${samplePath}\n${JSON.stringify(meta.sample, null, 2)}`;
    return `<article class="modelCard">
      <div class="modelCardTop"><div><div class="modelPlazaEyebrow">${esc(categoryLabel(meta.category))} · ${esc(meta.kicker)}</div><div class="modelCardTitle">${esc(meta.name)}</div><div class="modelCardId">${esc(id)}</div></div><div class="modelStatus ${online ? "online" : ""}"><span class="modelStatusDot"></span>${online ? "当前可用" : "未在线"}</div></div>
      <p class="modelCardSummary">${esc(meta.summary)}</p><div class="modelBadges">${badges}</div>
      <div class="modelFacts"><div class="modelFact"><span>输入</span><strong>${esc(meta.input)}</strong></div><div class="modelFact"><span>输出</span><strong>${esc(meta.output)}</strong></div><div class="modelFact"><span>推理</span><strong>${esc(meta.reasoning)}</strong></div><div class="modelFact"><span>在线 Bridge</span><strong>${online ? String(clients.length) : "—"}</strong></div></div>
      <div><div class="modelSectionLabel">适用场景</div><div class="modelUseCases">${meta.useCases.map(item => `<span class="modelUseCase">${esc(item)}</span>`).join("")}</div></div>
      <div><div class="modelSectionLabel">支持接口</div><div class="modelApiList">${meta.endpoints.map(item => `<span class="modelEndpoint">${esc(item)}</span>`).join("")}</div></div>
      <details><summary>最小调用示例</summary><pre>${esc(sample)}</pre></details>
      <div class="modelCardActions"><button class="action" data-copy-model-v201="${esc(id)}">复制模型 ID</button><button class="action" data-copy-sample-v201="${esc(id)}">复制示例</button></div>
    </article>`;
  }

  function renderPlaza() {
    const grid = document.getElementById("modelPlazaGridV201");
    if (!grid) return;
    const query = plazaState.query.trim().toLowerCase();
    const entries = Object.entries(MODEL_META).filter(([id, meta]) => {
      if (plazaState.filter !== "all" && meta.category !== plazaState.filter) return false;
      if (!query) return true;
      return [id, meta.name, meta.kicker, meta.summary, ...meta.badges, ...meta.useCases, ...meta.endpoints].join(" ").toLowerCase().includes(query);
    });
    grid.innerHTML = entries.length ? entries.map(([id, meta]) => card(id, meta)).join("") : '<div class="modelPlazaEmpty">没有符合条件的模型。</div>';
    const onlineCount = Object.keys(MODEL_META).filter(id => (plazaState.live.get(id)?.clients || []).length > 0).length;
    const stat = document.getElementById("modelPlazaOnlineV201");
    if (stat) stat.textContent = String(onlineCount);
    grid.querySelectorAll("[data-copy-model-v201]").forEach(button => button.addEventListener("click", async () => navigator.clipboard.writeText(button.dataset.copyModelV201 || "")));
    grid.querySelectorAll("[data-copy-sample-v201]").forEach(button => button.addEventListener("click", async () => {
      const id = button.dataset.copySampleV201 || "";
      const meta = MODEL_META[id];
      if (!meta) return;
      const path = meta.category === "text" ? "/v1/chat/completions" : meta.category === "image" ? "/v1/images/generations" : "/v1/audio/speech";
      await navigator.clipboard.writeText(`POST ${path}\n${JSON.stringify(meta.sample, null, 2)}`);
    }));
  }

  async function loadPlaza() {
    try {
      const payload = await api("/api/admin/models");
      plazaState.live = new Map((payload.data || []).filter(row => row?.id && MODEL_META[row.id]).map(row => [String(row.id), row]));
    } catch (error) {
      plazaState.live = new Map();
      status("模型目录读取失败：" + String(error?.message || error), "bad");
    }
    renderPlaza();
  }

  function wirePlazaView() {
    const view = document.getElementById("view-models");
    if (!view) return;
    view.innerHTML = plazaTemplate();
    document.getElementById("modelPlazaSearchV201")?.addEventListener("input", event => { plazaState.query = event.target.value || ""; renderPlaza(); });
    document.querySelectorAll("#modelPlazaFiltersV201 .modelPlazaFilter").forEach(button => button.addEventListener("click", () => {
      plazaState.filter = button.dataset.filter || "all";
      document.querySelectorAll("#modelPlazaFiltersV201 .modelPlazaFilter").forEach(item => item.classList.toggle("active", item === button));
      renderPlaza();
    }));
    document.getElementById("modelPlazaRefreshV201")?.addEventListener("click", loadPlaza);
    renderPlaza();
  }

  function showPlaza() {
    document.querySelectorAll(".view").forEach(item => item.classList.toggle("active", item.id === "view-models"));
    document.querySelectorAll(".nav button").forEach(item => item.classList.toggle("active", item.dataset.view === "models"));
    const title = document.getElementById("pageTitle");
    if (title) title.textContent = "模型广场";
    location.hash = "models";
    loadPlaza();
  }

  wirePlazaView();
  const oldModelButton = document.querySelector('.nav button[data-view="models"]');
  if (oldModelButton) {
    const replacement = oldModelButton.cloneNode(true);
    oldModelButton.replaceWith(replacement);
    replacement.onclick = showPlaza;
  }
  if ((location.hash || "").slice(1) === "models") showPlaza();
})();
