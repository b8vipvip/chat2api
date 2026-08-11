(() => {
  const baseHandleServerMessage = handleServerMessage;
  const sessions = new Map();

  async function ensureV3(tabId) {
    try {
      const ping = await chrome.tabs.sendMessage(tabId, { type: "chat2api.image.ping.v3" });
      if (ping?.ok && ping.controller === "image-v3") return;
    } catch (_) {}
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content_multimodal.js", "content_multimodal_v4.js", "content_image_v3.js", "content_runtime_log.js"],
    });
    await sleep(240);
    const ping = await chrome.tabs.sendMessage(tabId, { type: "chat2api.image.ping.v3" });
    if (!ping?.ok || ping.controller !== "image-v3") throw new Error("ChatGPT image controller v3 did not respond in the current chat");
  }

  async function waitForCurrentChat(tabId, timeoutMs = 30000) {
    const deadline = Date.now() + timeoutMs;
    let lastError = null;
    while (Date.now() < deadline) {
      try {
        const tab = await chrome.tabs.get(tabId);
        const url = tab.url || tab.pendingUrl || "";
        if (!isChatGptUrl(url)) {
          await sleep(220);
          continue;
        }
        if (url.includes("/images")) {
          throw new Error("The bound ChatGPT tab is on the Images gallery; open a normal chat before using same-tab image generation");
        }
        if (tab.status && tab.status !== "complete") {
          await sleep(220);
          continue;
        }
        await ensureContent(tabId);
        await ensureV3(tabId);
        return tab;
      } catch (error) {
        lastError = error;
        if (String(error?.message || error).includes("Images gallery")) throw error;
      }
      await sleep(220);
    }
    throw lastError || new Error("Timed out waiting for the current ChatGPT chat");
  }

  async function currentChatTab(requestId) {
    // Resolve dynamically so conversation_dispatch can provide the per-API-key
    // routed tab while its request scope is active.
    const tab = await resolveTargetTab();
    if (!tab?.id) throw new Error("No bound ChatGPT chat is available for image generation");
    try { await chrome.tabs.update(tab.id, { active: true }); } catch (_) {}
    const current = await waitForCurrentChat(tab.id);
    sessions.set(requestId, {
      requestId,
      tabId: current.id,
      windowId: current.windowId,
      chatUrl: current.url || current.pendingUrl || "",
      startedAt: Date.now(),
    });
    return current;
  }

  async function prepareReferences(tabId, attachments, requestId) {
    if (!Array.isArray(attachments) || !attachments.length) return {};
    const response = await chrome.tabs.sendMessage(tabId, { type: "chat2api.attach.prepare.v4", attachments, requestId });
    if (!response?.ok) throw new Error(response?.error || "Unable to attach reference files in the current ChatGPT chat");
    return response.data || {};
  }

  function clearSession(requestId) {
    if (requestId) sessions.delete(requestId);
  }

  chrome.runtime.onMessage.addListener(message => {
    if (message.type !== "chat2api.event") return false;
    const event = message.event || {};
    if (!["image.completed", "image.error", "image.cancelled"].includes(event.type)) return false;
    if (event.request_id) clearSession(event.request_id);
    return false;
  });

  handleServerMessage = async function handleSameTabImageRoutingV3(message) {
    if (message.type !== "image.request" && message.type !== "image.cancel") return baseHandleServerMessage(message);

    if (message.type === "image.cancel") {
      const session = sessions.get(message.request_id);
      if (!session) return;
      try {
        await ensureV3(session.tabId);
        await chrome.tabs.sendMessage(session.tabId, { type: "chat2api.image.cancel.v3", requestId: message.request_id });
      } catch (error) {
        clearSession(message.request_id);
        await trySendSocket({ type: "image.cancelled", request_id: message.request_id, reason: String(error?.message || error) });
      }
      return;
    }

    const started = Date.now();
    try {
      const tab = await currentChatTab(message.request_id);
      const refs = await prepareReferences(tab.id, message.attachments || [], message.request_id);
      await trySendSocket({
        type: "image.diagnostics",
        request_id: message.request_id,
        diagnostics: {
          route: "chatgpt-current-chat",
          image_router: "image-routing-v3",
          image_same_tab: true,
          images_page: false,
          tab_id: tab.id,
          tab_ready_ms: Date.now() - started,
          image_tab_strategy: "reuse-current-chat",
          image_window_focus_strategy: "tab-active-only",
          chat_url: tab.url || tab.pendingUrl || "",
          ...refs,
        },
      });
      const response = await chrome.tabs.sendMessage(tab.id, {
        type: "chat2api.image.request.v3",
        requestId: message.request_id,
        prompt: message.prompt,
        options: {
          ...(message.options || {}),
          chat2api_diagnostics: {
            ...(message.options?.chat2api_diagnostics || {}),
            image_same_tab: true,
            images_page: false,
          },
        },
      });
      if (!response?.ok || response.controller !== "image-v3") {
        throw new Error(response?.error || "ChatGPT image controller v3 rejected the same-tab request");
      }
    } catch (error) {
      clearSession(message.request_id);
      await trySendSocket({ type: "image.error", request_id: message.request_id, error: String(error?.message || error) });
    }
  };
})();
