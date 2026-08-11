(() => {
  const VERSION = "0.16.0";
  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = `Server Console · v${VERSION}`;

  const MODEL_META = {
    "gpt-5.6-sol": {
      name: "GPT-5.6 Sol",
      category: "text",
      kicker: "旗舰推理 · 多模态",
      summary: "面向复杂分析、代码、视觉与文件理解的主力文本模型。适合需要更强推理质量和稳定执行的任务。",
      badges: ["文本", "视觉", "文件", "推理"],
      input: "文本 / 图片 / 文件",
      output: "文本",
      api: ["Responses", "Chat Completions", "Legacy Completions"],
      endpoints: ["POST /v1/responses", "POST /v1/chat/completions", "POST /v1/completions"],
      useCases: ["复杂问答与分析", "代码与工程任务", "视觉 / 文件理解"],
      recommended: true,
      sample: {model: "gpt-5.6-sol", reasoning_effort: "medium", messages: [{role: "user", content: "你好"}]},
    },
    "gpt-5.5": {
      name: "GPT-5.5",
      category: "text",
      kicker: "通用推理 · 多模态",
      summary: "稳定的通用文本与多模态模型，支持同一套推理强度、视觉和文件理解能力，适合作为主模型或备用模型。",
      badges: ["文本", "视觉", "文件", "推理"],
      input: "文本 / 图片 / 文件",
      output: "文本",
      api: ["Responses", "Chat Completions", "Legacy Completions"],
      endpoints: ["POST /v1/responses", "POST /v1/chat/completions", "POST /v1/completions"],
      useCases: ["日常问答", "结构化分析", "视觉 / 文件理解"],
      recommended: false,
      sample: {model: "gpt-5.5", reasoning_effort: "medium", messages: [{role: "user", content: "你好"}]},
    },
    "gpt-image": {
      name: "GPT Image",
      category: "image",
      kicker: "图片生成 · 参考图",
      summary: "在当前 ChatGPT 会话中直接执行图片生成。支持纯文本提示词，也支持先上传参考图再生成新图片。",
      badges: ["图片生成", "参考图", "Base64"],
      input: "文本 / 参考图片",
      output: "图片",
      api: ["Images API"],
      endpoints: ["POST /v1/images/generations"],
      useCases: ["文生图", "参考图再创作", "视觉素材生成"],
      recommended: false,
      sample: {model: "gpt-image", prompt: "生成一张简洁的蓝天白云测试图片。", response_format: "b64_json"},
    },
    "gpt-live": {
      name: "GPT Live",
      category: "audio",
      kicker: "语音生成 · 语音对话",
      summary: "通过 ChatGPT Voice 链路生成语音，或把上传的音频作为输入完成一轮语音对话，并返回捕获到的音频与转写。",
      badges: ["语音生成", "语音对话", "音频"],
      input: "文本 / 音频",
      output: "音频 / 转写",
      api: ["Audio Speech", "Audio Conversations"],
      endpoints: ["POST /v1/audio/speech", "POST /v1/audio/conversations"],
      useCases: ["TTS 风格输出", "语音问答", "实时语音链路测试"],
      recommended: true,
      sample: {model: "gpt-live", input: "请用一句简短中文回复。", response_format: "b64_json"},
    },
    "gpt-live-mini": {
      name: "GPT Live Mini",
      category: "audio",
      kicker: "语音模型变体",
      summary: "与 GPT Live 共用 chat2api 音频入口的语音模型 ID，便于客户端按模型策略区分语音请求。实际能力以当前在线 Chrome / ChatGPT Voice 链路为准。",
      badges: ["语音", "Audio API"],
      input: "文本 / 音频",
      output: "音频 / 转写",
      api: ["Audio Speech", "Audio Conversations"],
      endpoints: ["POST /v1/audio/speech", "POST /v1/audio/conversations"],
      useCases: ["语音模型策略", "语音链路兼容", "客户端模型分流"],
      recommended: false,
      sample: {model: "gpt-live-mini", input: "请说：语音测试成功", response_format: "b64_json"},
    },
  };

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

  const style = document.createElement("style");
  style.textContent = `
    .modelPlazaHero{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(280px,.65fr);gap:16px;margin-bottom:16px}
    .modelPlazaHeroMain{padding:24px;border:1px solid var(--line);border-radius:18px;background:linear-gradient(135deg,#111d34,#101928 60%,#14233d)}
    .modelPlazaHeroMain h1{font-size:30px;line-height:1.15;margin:6px 0 10px}.modelPlazaHeroMain p{max-width:820px;color:#b7c4da;margin:0}
    .modelPlazaEyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#8fb7ff;font-weight:800}.modelPlazaStats{display:grid;grid-template-columns:1fr 1fr;gap:10px}
    .modelPlazaStat{padding:16px;border:1px solid var(--line);border-radius:14px;background:var(--panel)}.modelPlazaStat strong{display:block;font-size:23px;margin-top:3px}.modelPlazaStat span{color:var(--muted);font-size:12px}
    .modelPlazaToolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 14px}.modelPlazaSearch{flex:1;min-width:240px}.modelPlazaFilters{display:flex;gap:6px;flex-wrap:wrap}
    .modelPlazaFilter{border:1px solid var(--line);background:var(--panel2);color:var(--muted);border-radius:999px;padding:7px 12px;cursor:pointer}.modelPlazaFilter.active{background:#19345e;color:#eef4ff;border-color:#355f96}
    .modelPlazaGrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.modelCard{position:relative;overflow:hidden;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px;display:flex;flex-direction:column;gap:14px}
    .modelCard::before{content:"";position:absolute;inset:0 0 auto 0;height:2px;background:linear-gradient(90deg,#72a7ff,#7ddec4);opacity:.8}.modelCardTop{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
    .modelCardTitle{font-size:21px;font-weight:850;margin:2px 0}.modelCardId{font:12px ui-monospace,SFMono-Regular,Consolas,monospace;color:#a9bbd8}.modelCardSummary{color:#bac6d9;margin:0;min-height:44px}
    .modelBadges{display:flex;gap:6px;flex-wrap:wrap}.modelBadge{border:1px solid #2d405f;background:#101b2e;color:#bcd1f2;border-radius:999px;padding:3px 8px;font-size:11px}.modelBadge.recommended{color:#9ef0d5;border-color:#285b4d;background:#102921}
    .modelStatus{display:inline-flex;align-items:center;gap:6px;font-size:12px;white-space:nowrap}.modelStatusDot{width:8px;height:8px;border-radius:50%;background:#66738a}.modelStatus.online .modelStatusDot{background:var(--ok);box-shadow:0 0 0 3px rgba(57,214,161,.12)}
    .modelFacts{display:grid;grid-template-columns:1fr 1fr;gap:8px}.modelFact{padding:10px;border-radius:10px;background:#0c1526;border:1px solid #202f48}.modelFact span{display:block;color:var(--muted);font-size:11px}.modelFact strong{display:block;margin-top:3px;font-size:13px}
    .modelSectionLabel{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px}.modelUseCases{display:flex;gap:6px;flex-wrap:wrap}.modelUseCase{background:#17243a;border-radius:8px;padding:5px 8px;color:#c6d2e6;font-size:12px}
    .modelApiList{display:flex;gap:7px;flex-wrap:wrap}.modelEndpoint{font:11px ui-monospace,SFMono-Regular,Consolas,monospace;background:#07101e;border:1px solid var(--line);border-radius:7px;padding:5px 7px;color:#c8d9ff}
    .modelCardActions{display:flex;gap:8px;margin-top:auto}.modelCardActions button{flex:1}.modelCard details{border-top:1px solid var(--line);padding-top:10px}.modelCard summary{cursor:pointer;color:#a9b8d0}.modelCard pre{margin:10px 0 0;white-space:pre-wrap;word-break:break-word;font-size:11px;background:#07101e;border:1px solid var(--line);border-radius:9px;padding:10px}
    .modelPlazaNotice{margin-top:14px;padding:12px 14px;border:1px solid #3a4c6a;border-radius:12px;background:#0d1728;color:#aebbd1}.modelPlazaEmpty{grid-column:1/-1;text-align:center;color:var(--muted);padding:38px;border:1px dashed var(--line);border-radius:14px}
    @media(max-width:1050px){.modelPlazaHero{grid-template-columns:1fr}.modelPlazaGrid{grid-template-columns:1fr}}@media(max-width:620px){.modelPlazaStats{grid-template-columns:1fr 1fr}.modelFacts{grid-template-columns:1fr}.modelPlazaHeroMain{padding:18px}}
  `;
  document.head.appendChild(style);

  function ensureView() {
    const nav = document.querySelector(".nav");
    const content = document.querySelector(".content");
    if (!nav || !content) return null;

    let button = nav.querySelector('[data-view="models"]');
    if (!button) {
      button = document.createElement("button");
      button.dataset.view = "models";
      button.textContent = "模型广场";
      const docsButton = nav.querySelector('[data-view="docs"]');
      nav.insertBefore(button, docsButton || null);
      button.addEventListener("click", () => showModelPlaza());
    }

    let view = document.getElementById("view-models");
    if (!view) {
      view = document.createElement("section");
      view.id = "view-models";
      view.className = "view";
      view.innerHTML = `
        <div class="modelPlazaHero">
          <div class="modelPlazaHeroMain">
            <div class="modelPlazaEyebrow">Model catalog · chat2api</div>
            <h1>模型广场</h1>
            <p>浏览 chat2api 当前支持的文本、多模态、图片和语音模型。模型卡片会结合实时 <code>/v1/models</code> 结果显示在线可用状态，并给出能力、默认参数、接口和最小调用示例。</p>
          </div>
          <div class="modelPlazaStats">
            <div class="modelPlazaStat"><span>已发布模型</span><strong id="modelPlazaPublished">${Object.keys(MODEL_META).length}</strong></div>
            <div class="modelPlazaStat"><span>当前可用</span><strong id="modelPlazaOnline">-</strong></div>
            <div class="modelPlazaStat"><span>文本 / 多模态</span><strong>2</strong></div>
            <div class="modelPlazaStat"><span>图片 / 语音</span><strong>3</strong></div>
          </div>
        </div>
        <div class="modelPlazaToolbar">
          <input class="field modelPlazaSearch" id="modelPlazaSearch" placeholder="搜索模型 ID、能力或使用场景…">
          <div class="modelPlazaFilters" id="modelPlazaFilters">
            <button class="modelPlazaFilter active" data-filter="all">全部</button>
            <button class="modelPlazaFilter" data-filter="text">文本 / 多模态</button>
            <button class="modelPlazaFilter" data-filter="image">图片</button>
            <button class="modelPlazaFilter" data-filter="audio">语音</button>
          </div>
          <button class="action" id="modelPlazaRefresh">刷新状态</button>
        </div>
        <div class="modelPlazaGrid" id="modelPlazaGrid"><div class="modelPlazaEmpty">正在读取模型目录…</div></div>
        <div class="modelPlazaNotice">说明：chat2api 是浏览器桥接服务，模型可用性取决于 Chrome Bridge 与 ChatGPT 页面是否在线。模型广场只展示 chat2api 已声明或当前扩展报告的能力，不虚构价格、上下文窗口或速率限制。</div>`;
      const docs = document.getElementById("view-docs");
      content.insertBefore(view, docs || null);

      view.querySelector("#modelPlazaSearch")?.addEventListener("input", () => render());
      view.querySelector("#modelPlazaRefresh")?.addEventListener("click", () => loadLiveModels(true));
      for (const filter of view.querySelectorAll(".modelPlazaFilter")) {
        filter.addEventListener("click", () => {
          state.filter = filter.dataset.filter || "all";
          for (const item of view.querySelectorAll(".modelPlazaFilter")) item.classList.toggle("active", item === filter);
          render();
        });
      }
    }
    return view;
  }

  const state = {filter: "all", live: new Map(), loaded: false, error: ""};

  function categoryLabel(category) {
    return category === "text" ? "文本 / 多模态" : category === "image" ? "图片" : "语音";
  }

  function modelCard(id, meta) {
    const live = state.live.get(id) || null;
    const isOnline = Boolean(live);
    const efforts = live?.reasoning_efforts || (meta.category === "text" ? ["low", "medium", "high"] : []);
    const defaultEffort = live?.default_reasoning_effort || (meta.category === "text" ? "medium" : "—");
    const clients = Array.isArray(live?.clients) ? live.clients.length : 0;
    const runtimeCapabilities = Array.isArray(live?.capabilities) && live.capabilities.length ? live.capabilities : meta.badges;
    const badges = [...runtimeCapabilities.map(item => `<span class="modelBadge">${escapeHtml(item)}</span>`), meta.recommended ? '<span class="modelBadge recommended">推荐</span>' : ""].join("");
    const reasoningText = efforts.length ? `${efforts.join(" / ")} · 默认 ${defaultEffort}` : "不适用";
    const samplePath = meta.category === "text" ? "/v1/chat/completions" : meta.category === "image" ? "/v1/images/generations" : "/v1/audio/speech";
    const sample = `POST ${samplePath}\n${JSON.stringify(meta.sample, null, 2)}`;
    return `
      <article class="modelCard" data-category="${meta.category}" data-search="${escapeHtml([id, meta.name, meta.kicker, meta.summary, ...meta.badges, ...meta.useCases].join(" ").toLowerCase())}">
        <div class="modelCardTop">
          <div><div class="modelPlazaEyebrow">${escapeHtml(categoryLabel(meta.category))} · ${escapeHtml(meta.kicker)}</div><div class="modelCardTitle">${escapeHtml(meta.name)}</div><div class="modelCardId">${escapeHtml(id)}</div></div>
          <div class="modelStatus ${isOnline ? "online" : ""}" title="${isOnline ? "当前 /v1/models 已报告该模型" : "当前 /v1/models 未报告该模型"}"><span class="modelStatusDot"></span>${isOnline ? "当前可用" : "未在线"}</div>
        </div>
        <p class="modelCardSummary">${escapeHtml(meta.summary)}</p>
        <div class="modelBadges">${badges}</div>
        <div class="modelFacts">
          <div class="modelFact"><span>输入</span><strong>${escapeHtml(meta.input)}</strong></div>
          <div class="modelFact"><span>输出</span><strong>${escapeHtml(meta.output)}</strong></div>
          <div class="modelFact"><span>推理强度</span><strong>${escapeHtml(reasoningText)}</strong></div>
          <div class="modelFact"><span>在线 Bridge</span><strong>${isOnline ? String(clients || "已连接") : "—"}</strong></div>
        </div>
        <div><div class="modelSectionLabel">适用场景</div><div class="modelUseCases">${meta.useCases.map(item => `<span class="modelUseCase">${escapeHtml(item)}</span>`).join("")}</div></div>
        <div><div class="modelSectionLabel">支持接口</div><div class="modelApiList">${meta.endpoints.map(item => `<span class="modelEndpoint">${escapeHtml(item)}</span>`).join("")}</div></div>
        <details><summary>最小调用示例</summary><pre>${escapeHtml(sample)}</pre></details>
        <div class="modelCardActions"><button class="action" data-copy-model="${escapeHtml(id)}">复制模型 ID</button><button class="action" data-copy-sample="${escapeHtml(id)}">复制示例</button></div>
      </article>`;
  }

  function render() {
    const view = ensureView();
    if (!view) return;
    const grid = view.querySelector("#modelPlazaGrid");
    const query = String(view.querySelector("#modelPlazaSearch")?.value || "").trim().toLowerCase();
    const entries = Object.entries(MODEL_META).filter(([id, meta]) => {
      if (state.filter !== "all" && meta.category !== state.filter) return false;
      if (!query) return true;
      return [id, meta.name, meta.kicker, meta.summary, ...meta.badges, ...meta.useCases, ...meta.endpoints].join(" ").toLowerCase().includes(query);
    });
    grid.innerHTML = entries.length ? entries.map(([id, meta]) => modelCard(id, meta)).join("") : '<div class="modelPlazaEmpty">没有符合当前筛选条件的模型。</div>';
    view.querySelector("#modelPlazaOnline").textContent = state.loaded ? String([...state.live.keys()].filter(id => MODEL_META[id]).length) : "-";

    for (const button of grid.querySelectorAll("[data-copy-model]")) {
      button.addEventListener("click", async () => {
        const id = button.dataset.copyModel || "";
        await navigator.clipboard.writeText(id);
        const old = button.textContent; button.textContent = "已复制"; setTimeout(() => button.textContent = old, 900);
      });
    }
    for (const button of grid.querySelectorAll("[data-copy-sample]")) {
      button.addEventListener("click", async () => {
        const id = button.dataset.copySample || ""; const meta = MODEL_META[id]; if (!meta) return;
        const path = meta.category === "text" ? "/v1/chat/completions" : meta.category === "image" ? "/v1/images/generations" : "/v1/audio/speech";
        await navigator.clipboard.writeText(`POST ${path}\n${JSON.stringify(meta.sample, null, 2)}`);
        const old = button.textContent; button.textContent = "已复制"; setTimeout(() => button.textContent = old, 900);
      });
    }
  }

  async function loadLiveModels(force = false) {
    if (state.loaded && !force) return render();
    const view = ensureView();
    if (!view) return;
    const token = String(document.getElementById("adminKey")?.value || "").trim();
    try {
      const response = await fetch("/v1/models", {headers: token ? {Authorization: `Bearer ${token}`} : {}});
      if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
      const payload = await response.json();
      state.live = new Map((Array.isArray(payload?.data) ? payload.data : []).filter(row => row?.id).map(row => [String(row.id), row]));
      state.loaded = true;
      state.error = "";
    } catch (error) {
      state.live = new Map();
      state.loaded = true;
      state.error = String(error?.message || error);
    }
    render();
  }

  function showModelPlaza() {
    const view = ensureView();
    if (!view) return;
    for (const item of document.querySelectorAll(".view")) item.classList.toggle("active", item === view);
    for (const item of document.querySelectorAll(".nav button")) item.classList.toggle("active", item.dataset.view === "models");
    const title = document.getElementById("pageTitle"); if (title) title.textContent = "模型广场";
    const status = document.getElementById("status"); if (status && state.error) status.textContent = `模型目录读取失败：${state.error}`;
    loadLiveModels(false);
  }

  ensureView();
})();
