(() => {
  const baseHandleServerMessage = handleServerMessage;
  const baseResolveTargetTab = resolveTargetTab;
  const IMAGES_URL = "https://chatgpt.com/images/";
  const sessions = new Map();
  let restorePromise = null;

  async function ensureV3(tabId) {
    try {
      const ping = await chrome.tabs.sendMessage(tabId, { type: "chat2api.image.ping.v3" });
      if (ping?.ok && ping.controller === "image-v3") return;
    } catch (_) {}
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content_multimodal.js", "content_image_v3.js", "content_runtime_log.js"] });
    await sleep(240);
    const ping = await chrome.tabs.sendMessage(tabId, { type: "chat2api.image.ping.v3" });
    if (!ping?.ok || ping.controller !== "image-v3") throw new Error("ChatGPT Images v3 controller did not respond");
  }

  async function waitForImages(tabId, timeoutMs = 30000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const tab = await chrome.tabs.get(tabId);
      const url = tab.url || tab.pendingUrl || "";
      if (url.includes("/images")) {
        await ensureV3(tabId);
        return tab;
      }
      await sleep(220);
    }
    throw new Error("Timed out waiting for ChatGPT Images");
  }

  async function imageTab(requestId) {
    const tab = await baseResolveTargetTab();
    if (!tab?.id) throw new Error("No bound ChatGPT tab is available for image generation");
    const originalUrl = tab.url || tab.pendingUrl || "https://chatgpt.com/";
    try { await chrome.tabs.update(tab.id, { active: true }); } catch (_) {}
    if (!originalUrl.includes("/images")) await chrome.tabs.update(tab.id, { url: IMAGES_URL, active: true });
    const current = await waitForImages(tab.id);
    sessions.set(requestId, {
      requestId,
      tabId: tab.id,
      windowId: tab.windowId,
      originalUrl: originalUrl.includes("/images") ? "https://chatgpt.com/" : originalUrl,
      startedAt: Date.now(),
    });
    return current;
  }

  async function restore(requestId, reason = "completed") {
    const session = sessions.get(requestId);
    if (!session) return;
    sessions.delete(requestId);
    const task = (async () => {
      const restoreUrl = session.originalUrl && isChatGptUrl(session.originalUrl) ? session.originalUrl : "https://chatgpt.com/";
      try {
        await chrome.tabs.update(session.tabId, { url: restoreUrl, active: true });
        const deadline = Date.now() + 30000;
        let ready = null;
        while (Date.now() < deadline) {
          try {
            const tab = await chrome.tabs.get(session.tabId);
            const url = tab.url || tab.pendingUrl || "";
            if (isChatGptUrl(url) && !url.includes("/images")) {
              await ensureContent(session.tabId);
              ready = tab;
              break;
            }
          } catch (_) {}
          await sleep(220);
        }
        if (!ready) throw new Error("Timed out restoring the ChatGPT conversation after image generation");
        await chrome.storage.local.set({ boundTabId: session.tabId, autoBind: false, modelsUpdatedAt: 0 });
        await sendExtensionStatus(false);
        await trySendSocket({
          type: "image.diagnostics",
          request_id: requestId,
          diagnostics: {
            image_router: "image-routing-v3",
            image_restore_reason: reason,
            image_restore_ok: true,
            image_restore_ms: Date.now() - session.startedAt,
            restored_tab_id: session.tabId,
          },
        });
      } catch (error) {
        await trySendSocket({
          type: "image.diagnostics",
          request_id: requestId,
          diagnostics: {
            image_router: "image-routing-v3",
            image_restore_reason: reason,
            image_restore_ok: false,
            image_restore_error: String(error?.message || error),
            restored_tab_id: session.tabId,
          },
        });
      }
    })();
    const wrapped = task.finally(() => { if (restorePromise === wrapped) restorePromise = null; });
    restorePromise = wrapped;
    return wrapped;
  }

  resolveTargetTab = async function resolveAfterImagesV3() {
    if (restorePromise) await restorePromise;
    return baseResolveTargetTab();
  };

  async function prepareReferences(tabId, attachments) {
    if (!Array.isArray(attachments) || !attachments.length) return {};
    const response = await chrome.tabs.sendMessage(tabId, { type: "chat2api.attach.prepare", attachments });
    if (!response?.ok) throw new Error(response?.error || "Unable to attach reference files on ChatGPT Images");
    return response.data || {};
  }

  chrome.runtime.onMessage.addListener(message => {
    if (message.type !== "chat2api.event") return false;
    const event = message.event || {};
    if (!["image.completed", "image.error", "image.cancelled"].includes(event.type)) return false;
    if (!event.request_id || !sessions.has(event.request_id)) return false;
    restore(event.request_id, event.type).catch(() => {});
    return false;
  });

  handleServerMessage = async function handleImageRoutingV3(message) {
    if (message.type !== "image.request" && message.type !== "image.cancel") return baseHandleServerMessage(message);

    if (message.type === "image.cancel") {
      try {
        const session = sessions.get(message.request_id);
        if (!session) return;
        await ensureV3(session.tabId);
        await chrome.tabs.sendMessage(session.tabId, { type: "chat2api.image.cancel.v3", requestId: message.request_id });
      } catch (error) {
        await trySendSocket({ type: "image.cancelled", request_id: message.request_id, reason: String(error?.message || error) });
        await restore(message.request_id, "cancel-error");
      }
      return;
    }

    const started = Date.now();
    try {
      const tab = await imageTab(message.request_id);
      const refs = await prepareReferences(tab.id, message.attachments || []);
      await trySendSocket({
        type: "image.diagnostics",
        request_id: message.request_id,
        diagnostics: {
          route: "chatgpt-images",
          image_router: "image-routing-v3",
          tab_id: tab.id,
          tab_ready_ms: Date.now() - started,
          image_tab_strategy: "reuse-bound-tab",
          image_window_focus_strategy: "tab-active-only",
          ...refs,
        },
      });
      const response = await chrome.tabs.sendMessage(tab.id, {
        type: "chat2api.image.request.v3",
        requestId: message.request_id,
        prompt: message.prompt,
        options: message.options || {},
      });
      if (!response?.ok || response.controller !== "image-v3") throw new Error(response?.error || "ChatGPT Images v3 controller rejected the request");
    } catch (error) {
      await trySendSocket({ type: "image.error", request_id: message.request_id, error: String(error?.message || error) });
      await restore(message.request_id, "request-error");
    }
  };
})();
