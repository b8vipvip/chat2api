(() => {
  const KEY = "__CHAT2API_PLAYGROUND_CHAT_RECORDS_V3__";
  if (globalThis[KEY]) return;

  const baseFetch = globalThis.fetch.bind(globalThis);

  const normalizeUrl = input => {
    try {
      if (input instanceof Request) return new URL(input.url, location.href);
      return new URL(String(input || ""), location.href);
    } catch (_) {
      return null;
    }
  };

  const requestHeader = (input, init, name) => {
    try {
      const headers = new Headers(init?.headers || (input instanceof Request ? input.headers : undefined));
      return String(headers.get(name) || "").trim();
    } catch (_) {
      return "";
    }
  };

  const requestBody = async (input, init) => {
    const direct = init?.body;
    if (typeof direct === "string") return direct;
    if (input instanceof Request) {
      try { return await input.clone().text(); } catch (_) { return ""; }
    }
    return "";
  };

  const latestUserPrompt = body => {
    const messages = Array.isArray(body?.messages) ? body.messages : [];
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const item = messages[index];
      if (String(item?.role || "") !== "user") continue;
      if (typeof item.content === "string") return item.content;
      if (Array.isArray(item.content)) {
        const parts = item.content
          .filter(part => part && typeof part === "object" && ["text", "input_text"].includes(String(part.type || "")))
          .map(part => String(part.text || part.input_text || ""));
        return parts.join("\n");
      }
    }
    return "";
  };

  const selectedKey = () => {
    const select = document.getElementById("pgChatKey");
    const pasted = String(document.getElementById("pgChatKeyInput")?.value || "").trim();
    if (pasted) return {api_key_id: null, api_key_name: "手动粘贴"};
    const option = select?.selectedOptions?.[0];
    return {
      api_key_id: String(select?.value || "").trim() || null,
      api_key_name: String(option?.dataset?.keyName || option?.textContent || select?.value || "未解析").trim(),
    };
  };

  async function startChatRun(requestId, body) {
    const key = selectedKey();
    const response = await baseFetch("/api/admin/playground/chat-runs", {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        request_id: requestId,
        model: String(body?.model || "gpt-5.6-sol"),
        api_key_id: key.api_key_id,
        api_key_name: key.api_key_name,
        prompt: latestUserPrompt(body),
        attachments_count: Array.isArray(body?.attachments) ? body.attachments.length : 0,
      }),
    });
    if (!response.ok) {
      throw new Error(`测试记录创建失败：${response.status} ${await response.text()}`);
    }
    const payload = await response.json();
    if (!payload?.run?.run_id) throw new Error("测试记录创建失败：服务端未返回 run_id");
    if (typeof globalThis.loadTests === "function") {
      try { await globalThis.loadTests(); } catch (_) {}
    }
    return payload.run;
  }

  async function fetchWithChatRun(input, init = undefined) {
    const url = normalizeUrl(input);
    const method = String(init?.method || (input instanceof Request ? input.method : "GET") || "GET").toUpperCase();
    if (!url || url.origin !== location.origin || url.pathname !== "/v1/chat/completions" || method !== "POST") {
      return baseFetch(input, init);
    }

    const requestId = requestHeader(input, init, "X-Chat2API-Request-ID");
    if (!requestId.startsWith("req_")) return baseFetch(input, init);

    let body = {};
    try {
      const raw = await requestBody(input, init);
      body = raw ? JSON.parse(raw) : {};
    } catch (_) {
      body = {};
    }

    // Persist the running row before the production API request is dispatched.
    // Fail closed here: an operator should never have a real Playground chat
    // request in Request History without a matching Test History row.
    await startChatRun(requestId, body);
    return baseFetch(input, init);
  }

  fetchWithChatRun.__chat2apiPlaygroundChatRecordsV3 = true;
  globalThis.fetch = fetchWithChatRun;
  globalThis[KEY] = Object.freeze({version: 3, startChatRun});
})();
