(() => {
  const baseHandleServerMessage = handleServerMessage;
  const baseResolveTargetTabForImages = resolveTargetTab;
  const IMAGES_URL = "https://chatgpt.com/images/";
  const imageSessions = new Map();
  let imageRestorePromise = null;

  async function activateTabInOwnWindow(tab) {
    if (!tab?.id) return tab;
    try { await chrome.tabs.update(tab.id, { active: true }); } catch (_) {}
    try { return await chrome.tabs.get(tab.id); } catch (_) { return tab; }
  }

  async function ensureImageController(tabId) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, { type: "chat2api.image.ping.v2" });
      if (response?.ok && response?.controller === "image-v2") return;
    } catch (_) {}
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content_multimodal.js", "content_image.js"] });
    await sleep(220);
    const response = await chrome.tabs.sendMessage(tabId, { type: "chat2api.image.ping.v2" });
    if (!response?.ok || response?.controller !== "image-v2") throw new Error("ChatGPT Images v2 controller did not respond");
  }

  async function waitForImages(tabId, timeoutMs = 30000) {
    const deadline = Date.now() + timeoutMs;
    let lastError = null;
    while (Date.now() < deadline) {
      try {
        const current = await chrome.tabs.get(tabId);
        const url = current.url || current.pendingUrl || "";
        if (url.includes("/images")) {
          await ensureImageController(tabId);
          return current;
        }
      } catch (error) { lastError = error; }
      await sleep(250);
    }
    throw lastError || new Error("Timed out waiting for ChatGPT Images");
  }

  async function imageTab(requestId) {
    const tab = await baseResolveTargetTabForImages();
    if (!tab?.id) throw new Error("No bound ChatGPT tab is available for image generation");
    const originalUrl = tab.url || tab.pendingUrl || "https://chatgpt.com/";
    await activateTabInOwnWindow(tab);
    if (!(originalUrl || "").includes("/images")) {
      await chrome.tabs.update(tab.id, { url: IMAGES_URL, active: true });
    }
    const current = await waitForImages(tab.id);
    imageSessions.set(requestId, {
      requestId,
      tabId: tab.id,
      windowId: tab.windowId,
      originalUrl: originalUrl.includes("/images") ? "https://chatgpt.com/" : originalUrl,
      startedAt: Date.now(),
    });
    return current;
  }

  async function restoreImageSession(requestId, reason = "completed") {
    const session = imageSessions.get(requestId);
    if (!session) return;
    imageSessions.delete(requestId);
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
          await sleep(250);
        }
        if (!ready) throw new Error("Timed out restoring the ChatGPT conversation after image generation");
        await chrome.storage.local.set({ boundTabId: session.tabId, autoBind: false, modelsUpdatedAt: 0 });
        await sendExtensionStatus(false);
        await trySendSocket({
          type: "image.diagnostics",
          request_id: requestId,
          diagnostics: {
            image_tab_strategy: "reuse-bound-tab",
            image_window_focus_strategy: "tab-active-only",
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
            image_tab_strategy: "reuse-bound-tab",
            image_window_focus_strategy: "tab-active-only",
            image_restore_reason: reason,
            image_restore_ok: false,
            image_restore_error: String(error?.message || error),
            restored_tab_id: session.tabId,
          },
        });
      }
    })();
    let wrapped = null;
    wrapped = task.finally(() => {
      if (imageRestorePromise === wrapped) imageRestorePromise = null;
    });
    imageRestorePromise = wrapped;
    return wrapped;
  }

  resolveTargetTab = async function resolveAfterImages() {
    if (imageRestorePromise) await imageRestorePromise;
    return baseResolveTargetTabForImages();
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
    if (!event.request_id || !imageSessions.has(event.request_id)) return false;
    restoreImageSession(event.request_id, event.type).catch(() => {});
    return false;
  });

  handleServerMessage = async function handleImageRouting(message) {
    if (message.type !== "image.request" && message.type !== "image.cancel") return baseHandleServerMessage(message);
    if (message.type === "image.cancel") {
      try {
        const session = imageSessions.get(message.request_id);
        const tab = session ? await chrome.tabs.get(session.tabId) : await imageTab(message.request_id);
        await chrome.tabs.sendMessage(tab.id, { type: "chat2api.image.cancel.v2", requestId: message.request_id });
      } catch (error) {
        await trySendSocket({ type: "image.cancelled", request_id: message.request_id, reason: String(error?.message || error) });
        await restoreImageSession(message.request_id, "cancel-error");
      }
      return;
    }

    const started = Date.now();
    try {
      const tab = await imageTab(message.request_id);
      const refs = await prepareReferences(tab.id, message.attachments || []);
      const diagnostics = {
        route: "chatgpt-images",
        tab_id: tab.id,
        tab_ready_ms: Date.now() - started,
        image_tab_strategy: "reuse-bound-tab",
        image_window_focus_strategy: "tab-active-only",
        ...refs,
      };
      await trySendSocket({ type: "image.diagnostics", request_id: message.request_id, diagnostics });
      const response = await chrome.tabs.sendMessage(tab.id, {
        type: "chat2api.image.request.v2",
        requestId: message.request_id,
        prompt: message.prompt,
        options: message.options || {},
      });
      if (!response?.ok || response?.controller !== "image-v2") throw new Error(response?.error || "ChatGPT Images v2 controller rejected the request");
    } catch (error) {
      await trySendSocket({ type: "image.error", request_id: message.request_id, error: String(error?.message || error) });
      await restoreImageSession(message.request_id, "request-error");
    }
  };
})();
