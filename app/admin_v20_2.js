(() => {
  const VERSION = "0.20.2";
  const LIVE_SAMPLE = {
    type: "session.start",
    model: "gpt-live",
    instructions: "请进行自然、简短的实时语音对话"
  };
  const LIVE_MINI_SAMPLE = {
    type: "session.start",
    model: "gpt-live-mini",
    instructions: "兼容调用；当前实际执行仍使用 GPT Live 浏览器语音链路"
  };

  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = `Server Console · v${VERSION}`;

  function patchLiveCard(id, { kicker, summary, badges, useCases, sample }) {
    const cards = [...document.querySelectorAll("#view-models .modelCard")];
    const card = cards.find(item => item.querySelector(".modelCardId")?.textContent.trim() === id);
    if (!card) return;
    const eyebrow = card.querySelector(".modelPlazaEyebrow");
    if (eyebrow) eyebrow.textContent = `语音 · ${kicker}`;
    const summaryNode = card.querySelector(".modelCardSummary");
    if (summaryNode) summaryNode.textContent = summary;
    const badgeNode = card.querySelector(".modelBadges");
    if (badgeNode) badgeNode.innerHTML = badges.map(text => `<span class="modelBadge">${esc(text)}</span>`).join("");
    const useNode = card.querySelector(".modelUseCases");
    if (useNode) useNode.innerHTML = useCases.map(text => `<span class="modelUseCase">${esc(text)}</span>`).join("");
    const apiList = card.querySelector(".modelApiList");
    if (apiList && ![...apiList.querySelectorAll(".modelEndpoint")].some(node => node.textContent.includes("/v1/audio/realtime"))) {
      apiList.insertAdjacentHTML("afterbegin", '<span class="modelEndpoint">WS /v1/audio/realtime</span>');
    }
    const pre = card.querySelector("details pre");
    if (pre) pre.textContent = `WS /v1/audio/realtime\n${JSON.stringify(sample, null, 2)}`;
  }

  function patchModelPlaza() {
    patchLiveCard("gpt-live", {
      kicker: "实时双向语音主模型",
      summary: "chat2api 的实时语音主模型 ID。通过 /v1/audio/realtime 持续发送麦克风 PCM，并实时接收 ChatGPT Voice 的语音、文字和会话事件。",
      badges: ["实时语音", "双向流", "WebSocket", "音频"],
      useCases: ["实时语音聊天", "语音助手", "可打断对话"],
      sample: LIVE_SAMPLE,
    });
    patchLiveCard("gpt-live-mini", {
      kicker: "兼容别名 · 与 GPT Live 同链路",
      summary: "兼容模型 ID。目前与 gpt-live 使用完全相同的 ChatGPT Voice / WebRTC 浏览器执行链路，不承诺更轻量、更快、更低成本或不同音质。",
      badges: ["兼容别名", "实时语音", "同一 Voice 链路"],
      useCases: ["旧客户端兼容", "模型 ID 分流", "迁移到 gpt-live"],
      sample: LIVE_MINI_SAMPLE,
    });
  }

  const modelView = document.getElementById("view-models");
  if (modelView) {
    const observer = new MutationObserver(() => patchModelPlaza());
    observer.observe(modelView, { childList: true, subtree: true });
    patchModelPlaza();
  }

  document.addEventListener("click", event => {
    const button = event.target?.closest?.("[data-copy-sample-v201]");
    if (!button) return;
    const id = button.dataset.copySampleV201 || "";
    if (id !== "gpt-live" && id !== "gpt-live-mini") return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const sample = id === "gpt-live" ? LIVE_SAMPLE : LIVE_MINI_SAMPLE;
    navigator.clipboard.writeText(`WS /v1/audio/realtime\n${JSON.stringify(sample, null, 2)}`).catch(() => {});
  }, true);

  function patchDocs() {
    const panel = document.querySelector("#view-docs .panel");
    if (!panel) return;
    const rows = [...panel.querySelectorAll("table tbody tr")];
    for (const row of rows) {
      const model = row.querySelector("code")?.textContent?.trim();
      if (model === "gpt-live") {
        const cells = row.querySelectorAll("td");
        if (cells[1]) cells[1].textContent = "实时双向语音主模型 / 语音生成 / 语音对话";
      } else if (model === "gpt-live-mini") {
        const cells = row.querySelectorAll("td");
        if (cells[1]) cells[1].textContent = "gpt-live 兼容别名；当前使用同一 ChatGPT Voice 实时链路";
      }
    }

    if (panel.querySelector("[data-chat2api-realtime-v202]")) return;
    const block = document.createElement("div");
    block.dataset.chat2apiRealtimeV202 = "1";
    const base = location.origin;
    const wsBase = base.replace(/^http/i, "ws");
    block.innerHTML = `
      <h2>实时语音 · GPT Live</h2>
      <p><code>gpt-live</code> 是当前推荐的实时双向语音模型。<code>gpt-live-mini</code> 仅保留为兼容别名，当前实际仍映射到 <code>gpt-live</code> 的同一 ChatGPT Voice / WebRTC 链路。</p>
      <p>实时协议为 <code>chat2api-live-v1</code>，使用 WebSocket <code>${esc(wsBase)}/v1/audio/realtime</code>。它是 chat2api 的实时语音桥协议，<b>不是 OpenAI Realtime API 的 wire-compatible 协议</b>；普通 OpenAI 文本接口不受影响。</p>
      <h3>Native / Android / iOS / Desktop</h3>
      <div class="codebox">WebSocket ${esc(wsBase)}/v1/audio/realtime\nAuthorization: Bearer YOUR_MANAGED_API_KEY\n\n第一帧 JSON：\n{\n  "type": "session.start",\n  "model": "gpt-live",\n  "instructions": "请自然聊天"\n}\n\n之后持续发送 binary PCM16LE：16000 Hz / mono\n服务端持续返回：JSON 事件 + binary PCM16LE 24000 Hz / mono</div>
      <h3>浏览器 Web App</h3>
      <p>浏览器原生 <code>WebSocket</code> 不能自定义 <code>Authorization</code> Header，因此先用 managed API Key 创建 60 秒、一次性的 realtime session token，再建立 WebSocket。不要把长期业务 API Key 放在 WebSocket URL 中。</p>
      <div class="codebox">POST ${esc(base)}/v1/audio/realtime/sessions\nAuthorization: Bearer YOUR_MANAGED_API_KEY\n\n返回：\n{\n  "session_token": "rt-chat2api-...",\n  "expires_in": 60,\n  "websocket_path": "/v1/audio/realtime?session_token=..."\n}\n\n然后：\nnew WebSocket("${esc(wsBase)}/v1/audio/realtime?session_token=...")</div>
      <h3>实时事件</h3>
      <div class="codebox">session.ready\ninput_audio_buffer.speech_started\ninput_audio_buffer.speech_stopped\ntranscript.final\nresponse.created\nresponse.text.delta\nresponse.audio.started\n(binary PCM audio frames)\nresponse.audio.done\nresponse.interrupted\nresponse.done\nsession.closed</div>
      <p>客户端可发送 <code>{"type":"response.cancel"}</code> 取消当前桥接输出，发送 <code>{"type":"session.finish"}</code> 结束会话，或发送 <code>{"type":"ping"}</code> 获取 <code>pong</code>。</p>`;
    panel.appendChild(block);
  }

  if (typeof renderDocs === "function") {
    const baseRenderDocs = renderDocs;
    renderDocs = function renderDocsV202() {
      baseRenderDocs();
      patchDocs();
    };
  }
  if (document.getElementById("view-docs")?.classList.contains("active")) patchDocs();
})();
