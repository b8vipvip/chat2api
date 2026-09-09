(() => {
  "use strict";
  if (globalThis.__CHAT2API_RESPONSES_CONSOLE_V110__) return;
  globalThis.__CHAT2API_RESPONSES_CONSOLE_V110__ = {revision: 110};

  const $ = (id) => document.getElementById(id);
  const make = (html) => {
    const template = document.createElement("template");
    template.innerHTML = html.trim();
    return template.content.firstElementChild;
  };

  function installPlaygroundProtocol() {
    const form = document.querySelector("#view-playground .form");
    if (!form || $("playProtocol")) return;
    const modelLabel = $("playModel")?.closest("label");
    const protocol = make(`<label class="field">调用协议<select id="playProtocol"><option value="responses">Responses API（推荐）</option><option value="chat_completions">Chat Completions（兼容）</option></select></label>`);
    const tool = make(`<label class="field" id="playToolField">Responses 工具<select id="playTool"><option value="none">不使用工具</option><option value="web_search">Web Search（原生）</option></select></label>`);
    if (modelLabel) {
      form.insertBefore(protocol, modelLabel);
      form.insertBefore(tool, modelLabel);
    } else {
      form.prepend(tool);
      form.prepend(protocol);
    }
    const hint = document.querySelector("#view-playground .muted");
    if (hint) hint.textContent = "可选择 Responses API（推荐）或兼容的 Chat Completions。Responses 可直接验证原生 Web Search。";
    const sync = () => {
      const responses = $("playProtocol")?.value === "responses";
      $("playToolField")?.classList.toggle("hidden", !responses);
    };
    $("playProtocol")?.addEventListener("change", sync);
    sync();
  }

  async function runResponsesPlayground(event) {
    if ($("playProtocol")?.value !== "responses") return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const key_id = $("playKey")?.value || "";
    const model = $("playModel")?.value || "";
    const prompt = $("playPrompt")?.value.trim() || "";
    const tool = $("playTool")?.value || "none";
    if (!key_id || !model || !prompt) {
      alert("请选择 API Key、模型并输入测试内容");
      return;
    }
    const output = $("playResponse");
    if (output) output.textContent = "Responses 请求中…";
    try {
      const response = await fetch("/api/user/playground", {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({protocol: "responses", key_id, model, prompt, tool}),
      });
      let payload = {};
      try { payload = await response.json(); } catch (_) {}
      if (!response.ok) throw new Error(payload.detail || payload?.error?.message || `HTTP ${response.status}`);
      if (output) output.textContent = JSON.stringify(payload.response, null, 2);
    } catch (error) {
      if (output) output.textContent = `Responses 测试失败：${error.message}`;
    }
  }

  function installPlaygroundHandler() {
    const button = $("runTest");
    if (!button || button.dataset.responsesV110 === "1") return;
    button.dataset.responsesV110 = "1";
    button.addEventListener("click", runResponsesPlayground, true);
  }

  function installDocs() {
    const view = $("view-docs");
    if (!view || $("responsesDocsV110")) return;
    const panel = view.querySelector(".panel");
    if (!panel) return;
    const currentChat = panel.querySelector("#docChat")?.previousElementSibling;
    if (currentChat?.tagName === "H2") currentChat.textContent = "Chat Completions（兼容）";

    const section = make(`<section id="responsesDocsV110">
      <h2>Responses API（推荐）</h2>
      <p><b>v0.22.69 起已新增 <code>POST /v1/responses</code>，原 <code>/v1/chat/completions</code> 继续兼容。</b> 普通旧客户端无需迁移；新 Agent、Codex/FDEX、Web Search 和工具调用应优先使用 Responses。</p>
      <h3>基础调用</h3>
      <div class="codebox" id="docResponsesBasic"></div>
      <p>非流式结果读取顶层 <code>output_text</code>；完整结构化 output item 位于 <code>output[]</code>。</p>
      <h3>原生 Web Search</h3>
      <div class="codebox" id="docResponsesWeb"></div>
      <p>Web Search 会映射为结构化 <code>web_search_call</code>；不是把“正在搜索”文本伪装成工具调用。</p>
      <h3>Function / Namespace / Custom / Tool Search</h3>
      <div class="codebox" id="docResponsesFunction"></div>
      <p>出现 <code>function_call</code>、<code>custom_tool_call</code> 或 <code>tool_search_call</code> 后，由调用方真正执行工具。chat2api 负责协议传输，不伪造 shell、apply_patch 或 MCP 执行。</p>
      <h3>function_call_output continuation</h3>
      <div class="codebox" id="docResponsesContinue"></div>
      <p>保留 <code>call_id</code> 和上一轮 <code>response.id</code>，再通过 <code>previous_response_id</code> + <code>function_call_output</code> 继续工具循环。也支持 <code>custom_tool_call_output</code>、<code>mcp_tool_call_output</code>、<code>tool_search_output</code>。</p>
      <h3>兼容说明</h3>
      <div class="notice">Chat Completions 继续支持 <code>messages[]</code> 和原有 SSE，不会因为升级 Responses 而失效。但 Responses 的结构化工具 output 不会自动降级成旧 Chat Completions tool_calls；Agent/Codex/FDEX 应直接使用 <code>/v1/responses</code>。</div>
    </section>`);

    const baseNode = $("docBase");
    if (baseNode) {
      const baseHeading = baseNode.previousElementSibling;
      (baseNode.parentElement || panel).insertBefore(section, baseHeading || baseNode);
    } else {
      panel.appendChild(section);
    }
    renderDocs();
  }

  function renderDocs() {
    const base = location.origin;
    const basic = $("docResponsesBasic");
    if (basic) basic.textContent = `curl ${base}/v1/responses \\\n  -H "Authorization: Bearer YOUR_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{\n    "model":"gpt-5.6-sol",\n    "input":"你好",\n    "stream":false\n  }'`;
    const web = $("docResponsesWeb");
    if (web) web.textContent = `curl ${base}/v1/responses \\\n  -H "Authorization: Bearer YOUR_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{\n    "model":"gpt-5.6-sol",\n    "input":"搜索 OpenAI 今天的更新并总结",\n    "tools":[{"type":"web_search"}],\n    "tool_choice":"required",\n    "stream":false\n  }'`;
    const fn = $("docResponsesFunction");
    if (fn) fn.textContent = `POST ${base}/v1/responses\nAuthorization: Bearer YOUR_API_KEY\nContent-Type: application/json\n\n{\n  "model":"gpt-5.6-sol",\n  "input":"读取项目状态",\n  "tools":[{\n    "type":"function",\n    "name":"read_project_status",\n    "description":"Read project status",\n    "parameters":{"type":"object","properties":{},"additionalProperties":false}\n  }]\n}`;
    const cont = $("docResponsesContinue");
    if (cont) cont.textContent = `POST ${base}/v1/responses\nAuthorization: Bearer YOUR_API_KEY\nContent-Type: application/json\n\n{\n  "model":"gpt-5.6-sol",\n  "previous_response_id":"resp_...",\n  "input":[{\n    "type":"function_call_output",\n    "call_id":"call_...",\n    "output":"{\\"status\\":\\"ok\\"}"\n  }],\n  "tools":[{\n    "type":"function",\n    "name":"read_project_status",\n    "parameters":{"type":"object","properties":{}}\n  }]\n}`;
  }

  function install() {
    installPlaygroundProtocol();
    installPlaygroundHandler();
    installDocs();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install, {once: true});
  } else {
    install();
  }
  setTimeout(install, 200);
  setTimeout(install, 900);
})();
