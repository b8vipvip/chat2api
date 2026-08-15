(() => {
  const VERSION = "0.21.0";
  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = `Server Console · v${VERSION}`;

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function codeBlock(text) {
    return `<pre>${esc(text)}</pre>`;
  }

  function patchDocs() {
    const panel = document.querySelector("#view-docs .panel");
    if (!panel || panel.querySelector("[data-concurrency-live-text-v21]")) return;
    const block = document.createElement("div");
    block.dataset.concurrencyLiveTextV21 = "1";
    block.className = "docSection";

    const nativeExample = `// Native / Android / iOS / Desktop\nconst ws = new WebSocket(\"wss://YOUR_HOST/v1/audio/realtime\", {\n  headers: { Authorization: \"Bearer YOUR_MANAGED_API_KEY\" }\n});\n\nws.send(JSON.stringify({\n  type: \"session.start\",\n  model: \"gpt-live\",\n  instructions: \"请自然对话\"\n}));\n\n// 通话过程中可以继续发送 PCM binary frame\n// 也可以随时插入文字，不需要关闭 Voice session\nws.send(JSON.stringify({\n  type: \"input_text\",\n  text: \"把你刚才说的内容整理成三点\"\n}));`;

    const itemExample = `// OpenAI 风格的文本 item 也接受\nws.send(JSON.stringify({\n  type: \"conversation.item.create\",\n  item: {\n    type: \"message\",\n    role: \"user\",\n    content: [\n      { type: \"input_text\", text: \"继续讲，但控制在30秒内\" }\n    ]\n  }\n}));\n\n// 服务端先回：input_text.queued\n// ChatGPT 页面真实发送成功后回：input_text.sent`;

    const browserExample = `// Browser Web App\nconst tokenRes = await fetch(\"/v1/audio/realtime/sessions\", {\n  method: \"POST\",\n  headers: { Authorization: \"Bearer YOUR_MANAGED_API_KEY\" }\n});\nconst token = await tokenRes.json();\nconst ws = new WebSocket(\n  \`wss://YOUR_HOST\${token.websocket_path}\`\n);\n\nws.addEventListener(\"open\", () => {\n  ws.send(JSON.stringify({ type: \"session.start\", model: \"gpt-live\" }));\n});`;

    block.innerHTML = `
      <h3>单扩展并发与容量</h3>
      <p>从 v0.21.0 起，一个配对扩展可以同时承载多个请求。服务端不再把整个 <code>client_id</code> 当成单一 busy 锁，而是使用 3 个容量单位；请求最多等待约 1.5 秒获取容量，仍无空位时返回 <code>429 extension_capacity_exhausted</code>。</p>
      <table>
        <thead><tr><th>请求类型</th><th>权重</th><th>单扩展默认行为</th></tr></thead>
        <tbody>
          <tr><td>文本 / Vision / 文件理解</td><td><code>1</code></td><td>最多可同时占满 3 个文本 Worker</td></tr>
          <tr><td>图片生成</td><td><code>2</code></td><td>默认最多 1 个图片任务，可再并行 1 个文本任务</td></tr>
          <tr><td>普通语音</td><td><code>2</code></td><td>默认最多 1 个语音任务，可再并行 1 个文本任务</td></tr>
          <tr><td>GPT Live</td><td><code>2</code></td><td>默认最多 1 个实时语音 Session，可再并行 1 个文本任务</td></tr>
        </tbody>
      </table>
      <p>Chrome Bridge 对同一 API Key 最多维护 3 个会话 Worker。第 1 个优先复用主会话，第 2/3 个在并发时使用独立 ChatGPT 窗口；已有的两个“常用模型 + 推理强度”备用槽继续优先供 Worker 命中。</p>

      <h3>GPT Live：边通话边发文字</h3>
      <p><code>WS /v1/audio/realtime</code> 现在支持音频 binary frame 与文字控制帧交错发送。Voice/WebRTC 不需要停止：可以一边持续推送麦克风 PCM，一边发送 <code>input_text</code>，文字会进入同一个正在进行的 ChatGPT Voice 会话。</p>
      ${codeBlock(nativeExample)}
      <p>也支持更接近 OpenAI Realtime 形态的 <code>conversation.item.create</code>：</p>
      ${codeBlock(itemExample)}
      <p>浏览器 Web App 仍使用一次性 realtime session token，长期业务 Key 不进入 WebSocket URL：</p>
      ${codeBlock(browserExample)}
      <p>关键服务端事件包括：<code>session.ready</code>、<code>input_audio_buffer.speech_started</code>、<code>input_audio_buffer.speech_stopped</code>、<code>input_text.queued</code>、<code>input_text.sent</code>、<code>transcript.final</code>、<code>response.text.delta</code>、binary PCM、<code>response.done</code>。</p>
      <p><strong>协议边界：</strong>实时语音仍是 <code>chat2api-live-v1</code>，不是 OpenAI 官方 Realtime wire-compatible 协议；外部应用需按本页 JSON event + PCM 协议接入。</p>
    `;
    panel.appendChild(block);
  }

  patchDocs();
  const docs = document.getElementById("view-docs");
  if (docs) new MutationObserver(() => patchDocs()).observe(docs, { childList: true, subtree: true });
})();
