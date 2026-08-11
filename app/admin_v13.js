(() => {
  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = "Server Console · v0.13";

  const TEXT_MODELS = ["gpt-5.6-sol", "gpt-5.5"];
  const modelSelect = document.getElementById("testModel");
  const modelBlock = modelSelect?.parentElement || null;

  function renderCanonicalModels() {
    if (!modelSelect) return;
    const current = TEXT_MODELS.includes(modelSelect.value) ? modelSelect.value : TEXT_MODELS[0];
    modelSelect.innerHTML = TEXT_MODELS.map(id => `<option value="${id}">${id}</option>`).join("");
    modelSelect.value = current;
  }

  renderCanonicalModels();
  fillModels = renderCanonicalModels;

  let reasoningSelect = document.getElementById("testReasoning");
  if (!reasoningSelect && modelBlock) {
    const label = document.createElement("label");
    label.textContent = "推理强度";
    label.style.marginTop = "7px";
    reasoningSelect = document.createElement("select");
    reasoningSelect.id = "testReasoning";
    reasoningSelect.innerHTML = `
      <option value="low">极速</option>
      <option value="medium" selected>中</option>
      <option value="high">高</option>`;
    modelBlock.appendChild(label);
    modelBlock.appendChild(reasoningSelect);
  }

  async function streamChatV13(model, prompt, attachments = [], testToken = key()) {
    const started = performance.now();
    let first = null;
    let totalText = 0;
    let meta = null;
    let rawError = "";
    const body = {
      model: TEXT_MODELS.includes(model) ? model : TEXT_MODELS[0],
      messages: [{ role: "user", content: prompt }],
      attachments,
      stream: true,
      timeout: 300,
    };
    const effort = document.getElementById("testReasoning")?.value || "";
    if (effort) body.reasoning_effort = effort;

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
          if (data.error) {
            rawError = data.error.message || "stream error";
            continue;
          }
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
      requested_reasoning_effort: effort || null,
    };
  }

  streamChat = streamChatV13;

  const docs = document.querySelector("#view-docs .panel");
  if (docs && !docs.querySelector("[data-chat2api-v13-docs]")) {
    for (const paragraph of docs.querySelectorAll("p")) {
      if (paragraph.textContent.includes("/images/")) {
        paragraph.innerHTML = `<code>gpt-image</code> 会在当前 ChatGPT 会话中直接生成图片，不再导航到 <code>/images/</code>。参考图继续使用 <code>attachments</code>；当前 <code>n</code> 固定为 1，推荐 <code>response_format=b64_json</code>。`;
      }
    }

    const block = document.createElement("div");
    block.dataset.chat2apiV13Docs = "1";
    block.innerHTML = `
      <h2>文本模型与推理强度</h2>
      <p>文本/视觉/文件理解统一使用 <code>gpt-5.6-sol</code> 或 <code>gpt-5.5</code>。旧的 <code>default</code>、<code>chatgpt-web</code> 已移除。推理强度使用 OpenAI 风格 <code>reasoning_effort</code>：<code>low</code>=极速、<code>medium</code>=中、<code>high</code>=高；省略该参数时保留当前 ChatGPT 页面强度。</p>
      <h2>Chat Completions</h2>
      <div class="codebox">POST /v1/chat/completions\n{\n  "model": "gpt-5.6-sol",\n  "reasoning_effort": "high",\n  "messages": [{"role":"user","content":"你好"}],\n  "stream": true\n}</div>
      <h2>Responses API</h2>
      <div class="codebox">POST /v1/responses\n{\n  "model": "gpt-5.6-sol",\n  "reasoning": {"effort":"medium"},\n  "input": "你好",\n  "stream": true\n}</div>
      <p>同时保留 <code>POST /v1/completions</code> 作为旧客户端兼容入口；新项目优先使用 Responses API 或 Chat Completions。</p>`;
    docs.appendChild(block);
  }
})();
