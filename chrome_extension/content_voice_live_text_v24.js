(() => {
  const KEY = "__CHAT2API_VOICE_LIVE_TEXT_V24__";
  if (globalThis[KEY]) return;

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  function sliceCodePoints(value, start, end) {
    return Array.from(String(value || "")).slice(start, end).join("");
  }
  const normalize = value => String(value || "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "")
    .replace(/\s+/g, " ")
    .trim();

  function visible(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function composerRoot() {
    return [...document.querySelectorAll("form[data-type='unified-composer'], form")]
      .find(form => visible(form) && form.querySelector("#prompt-textarea,textarea,[contenteditable='true']")) || null;
  }

  function findComposer() {
    const root = composerRoot() || document;
    for (const selector of [
      "#prompt-textarea",
      "textarea[placeholder]",
      "div[contenteditable='true'][data-lexical-editor='true']",
      "div[contenteditable='true'].ProseMirror",
      "[contenteditable='true']",
    ]) {
      const found = [...root.querySelectorAll(selector)].find(visible);
      if (found) return found;
    }
    return null;
  }

  function composerText(element = findComposer()) {
    if (!element) return "";
    if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) return normalize(element.value || "");
    return normalize(element.innerText || element.textContent || "");
  }

  function assertComposerDraftSafe(element, target) {
    const current = composerText(element);
    if (current && current !== normalize(target)) {
      throw new Error("ChatGPT Voice composer contains an unsent draft; refusing to overwrite it");
    }
  }

  function setComposerText(element, text) {
    element.focus();
    if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) {
      const proto = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(element, text);
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(element);
    selection.removeAllRanges();
    selection.addRange(range);
    document.execCommand("insertText", false, text);
    if (text && !normalize(element.textContent || "")) {
      const p = document.createElement("p");
      p.textContent = text;
      element.replaceChildren(p);
    }
    try {
      element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
    } catch (_) {
      element.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  function labelOf(element) {
    return normalize(`${element?.dataset?.testid || ""} ${element?.getAttribute?.("aria-label") || ""} ${element?.title || ""} ${element?.innerText || element?.textContent || ""}`);
  }

  function sendButton() {
    const root = composerRoot() || document;
    for (const selector of [
      "button[data-testid='send-button']",
      "button[aria-label='Send prompt']",
      "button[aria-label*='发送提示']",
      "button[aria-label*='发送消息']",
      "button[type='submit']",
    ]) {
      const button = [...root.querySelectorAll(selector)].find(visible);
      if (button) return button;
    }
    return [...root.querySelectorAll("button")]
      .find(button => visible(button) && /send prompt|send message|发送提示|发送消息|发送$/i.test(labelOf(button))) || null;
  }

  function buttonReady(button) {
    return Boolean(button && visible(button) && !button.disabled && button.getAttribute("aria-disabled") !== "true");
  }

  function dispatchEnter(element) {
    if (!element) return;
    element.focus();
    for (const type of ["keydown", "keypress", "keyup"]) {
      element.dispatchEvent(new KeyboardEvent(type, {
        key: "Enter", code: "Enter", keyCode: 13, which: 13,
        bubbles: true, cancelable: true,
      }));
    }
  }

  async function waitFor(predicate, timeout = 6000, interval = 100) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      try {
        const value = predicate();
        if (value) return value;
      } catch (_) {}
      await delay(interval);
    }
    return null;
  }

  async function emitLive(active, liveEvent, data = {}) {
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "image.progress",
          kind: "voice-live",
          request_id: active.requestId,
          live_event: liveEvent,
          session_id: active.sessionId,
          ...data,
        },
      });
    } catch (_) {}
  }

  // Only one turn may own the ChatGPT composer at a time. Voice/audio
  // streaming continues independently while upload + send is in progress.
  let turnQueue = Promise.resolve();
  function queueTurn(task) {
    const next = turnQueue.catch(() => {}).then(task);
    turnQueue = next.catch(() => {});
    return next;
  }

  function assertLiveTurnOwner(active) {
    if (active.stopped || globalThis.__CHAT2API_VOICE_LIVE_CONTENT_V2__?.active !== active)
      throw new Error("Voice call ended before this input could be sent");
  }

  async function sendLiveText(active, itemId, text) {
    assertLiveTurnOwner(active);
    const target = normalize(text);
    if (!target) throw new Error("Live text input is empty");
    const composer = await waitFor(findComposer, 8000, 100);
    if (!composer) throw new Error("ChatGPT Voice text composer is not available");
    assertComposerDraftSafe(composer, target);
    const baselineUsers = [...document.querySelectorAll("[data-message-author-role='user']")].filter(visible).length;

    setComposerText(composer, text);
    const retained = await waitFor(() => {
      const current = findComposer();
      const value = composerText(current);
      return value === target || (target.length > 6 && value.includes(target));
    }, 3500, 80);
    if (!retained) throw new Error("Live text input could not be retained in the ChatGPT composer");
    assertLiveTurnOwner(active);

    const hasNewUserTurn = () => {
      const nodes = [...document.querySelectorAll("[data-message-author-role='user']")].filter(visible);
      return nodes.slice(baselineUsers).some(node =>
        normalize(node.innerText || node.textContent || "").includes(target));
    };
    const button = await waitFor(() => {
      const value = sendButton();
      return buttonReady(value) ? value : null;
    }, 5000, 80);
    let dispatchedEnter = false;
    if (button) button.click();
    else {
      dispatchEnter(findComposer());
      dispatchedEnter = true;
    }

    // Do not send a second Enter merely because the composer is slow to clear:
    // a confirmed user turn is authoritative even while the composer UI lags.
    let sent = await waitFor(
      () => !composerText(findComposer()) || hasNewUserTurn(),
      4000, 100,
    );
    if (!sent && !hasNewUserTurn() && !dispatchedEnter) {
      dispatchEnter(findComposer());
      dispatchedEnter = true;
      sent = await waitFor(
        () => !composerText(findComposer()) || hasNewUserTurn(),
        4000, 100,
      );
    }
    if (!sent) throw new Error("ChatGPT Voice did not confirm the live text send action");

    // Composer clearing only proves that a UI action happened; require the
    // actual new user turn before acknowledging delivery to the API caller.
    const confirmed = hasNewUserTurn() || await waitFor(hasNewUserTurn, 10000, 120);
    if (!confirmed) throw new Error("ChatGPT Voice did not confirm the live text in a user conversation turn");
    assertLiveTurnOwner(active);

    await emitLive(active, "input.text.sent", { item_id: itemId || "", text });
  }

  function postMain(type, active, data = {}) {
    window.postMessage({
      source: "chat2api-isolated",
      type,
      request_id: active.requestId,
      session_id: active.sessionId,
      ...data,
    }, "*");
  }

  function liveMainEvents(active) {
    const current = globalThis.__CHAT2API_VOICE_LIVE_CONTENT_V2__;
    return current?.active === active && Array.isArray(current.mainEvents)
      ? current.mainEvents : [];
  }

  function liveCameraButton() {
    const buttons = [...document.querySelectorAll("button")]
      .filter(button => visible(button) && !button.disabled);
    const enabled = buttons.find(button => {
      const label = labelOf(button);
      return /(turn on camera|start camera|share camera|share video|open camera|enable camera|打开摄像头|开启摄像头|开始视频|打开视频|开启视频)/i.test(label);
    });
    if (enabled) return enabled;
    return buttons.find(button => {
      const label = labelOf(button);
      return /(camera|video|摄像头|视频)/i.test(label) &&
        !/(turn off|stop|close|disable|关闭|停止|结束)/i.test(label);
    }) || null;
  }

  async function ensureLiveVideoTrack(active) {
    assertLiveTurnOwner(active);
    if (active.videoTrackReady === true) return true;
    if (active.videoTrackUnavailable === true) return false;
    const events = () => liveMainEvents(active);
    try {
      postMain("voice.live.video.prepare", active);
      const prepared = await waitFor(() => events().find(item =>
        item.type === "voice.live.video.prepared"), 1200, 50);
      const prepareError = events().find(item => item.type === "voice.live.video.error");
      if (!prepared) throw new Error(prepareError?.error || "Synthetic WebRTC video track could not be prepared");

      const alreadyAttached = events().find(item => item.type === "voice.live.video.sender.attached");
      if (!alreadyAttached) {
        const alreadyOn = [...document.querySelectorAll("button")]
          .filter(visible).some(button => /(turn off camera|turn off video|stop camera|stop video|关闭摄像头|关闭视频|停止视频|停止摄像头)/i.test(labelOf(button)));
        if (!alreadyOn) {
          const camera = await waitFor(liveCameraButton, 2000, 100);
          if (!camera) throw new Error("ChatGPT Voice camera control is unavailable");
          assertLiveTurnOwner(active);
          camera.click();
        }
      }
      const senderReady = await waitFor(() => {
        const provided = events().find(item => item.type === "voice.live.video.track.provided");
        const attached = events().find(item => item.type === "voice.live.video.sender.attached");
        return provided && attached ? { provided, attached } : null;
      }, 5500, 50);
      const videoError = events().find(item => item.type === "voice.live.video.error");
      if (!senderReady) {
        throw new Error(videoError?.error || "ChatGPT did not attach the synthetic WebRTC camera track");
      }
      assertLiveTurnOwner(active);
      active.videoTrackReady = true;
      active.videoTrackMode = "web_rtc";
      return true;
    } catch (error) {
      active.videoTrackUnavailable = true;
      active.videoTrackMode = "snapshot_fallback";
      active.videoTrackError = String(error?.message || error).slice(0, 240);
      return false;
    }
  }

  async function sendLiveVideoFrame(active, itemId, attachments, videoFrame) {
    assertLiveTurnOwner(active);
    if (!Array.isArray(attachments) || attachments.length !== 1) return false;
    if (!await ensureLiveVideoTrack(active)) return false;
    const fileId = String(attachments[0]?.file_id || "");
    if (!/^file_[A-Za-z0-9]{8,64}$/.test(fileId)) return false;
    const fetched = await chrome.runtime.sendMessage({
      type: "chat2api.attachment.fetch",
      fileId,
      requestId: active.requestId,
    });
    if (!fetched?.ok || !fetched.data?.base64) {
      throw new Error(fetched?.error || "Worker could not fetch the authorized live video frame");
    }
    assertLiveTurnOwner(active);
    const data = fetched.data;
    const mime = String(data.mime_type || "").toLowerCase();
    if (!["image/jpeg", "image/png", "image/webp"].includes(mime) ||
        Number(data.size || 0) <= 0 || Number(data.size || 0) > 2 * 1024 * 1024) {
      throw new Error("Live video frame failed the Worker image validation");
    }
    postMain("voice.live.video.frame", active, {
      base64: data.base64,
      mime_type: mime,
      sequence: Number(videoFrame.sequence),
      timestamp_ms: Number(videoFrame.timestamp_ms),
    });
    const events = () => liveMainEvents(active);
    const received = await waitFor(() => events().find(item =>
      item.type === "voice.live.video.frame.received" &&
      Number(item.sequence) === Number(videoFrame.sequence)), 2000, 40);
    const frameError = events().find(item =>
      item.type === "voice.live.video.error" &&
      Number(item.sequence) === Number(videoFrame.sequence));
    if (!received) throw new Error(frameError?.error || "Synthetic WebRTC video track did not accept the frame");
    assertLiveTurnOwner(active);
    await emitLive(active, "input.media.sent", {
      item_id: itemId || "",
      attachment_count: 1,
      video_track: true,
      sequence: Number(videoFrame.sequence),
      timestamp_ms: Number(videoFrame.timestamp_ms),
    });
    return true;
  }

  async function sendLiveMedia(active, itemId, text, attachments, videoFrame = null) {
    assertLiveTurnOwner(active);
    if (videoFrame) {
      try {
        if (await sendLiveVideoFrame(active, itemId, attachments, videoFrame)) return;
      } catch (error) {
        // Keep the existing composer-based still-image path as a safe fallback
        // when a ChatGPT build has no usable camera control or video sender.
        active.videoTrackUnavailable = true;
        active.videoTrackMode = "snapshot_fallback";
        active.videoTrackError = String(error?.message || error).slice(0, 240);
      }
    }
    if (!Array.isArray(attachments) || !attachments.length || attachments.length > 4)
      throw new Error("Live media message requires 1-4 uploaded attachments");
    if (!globalThis.__CHAT2API_MULTIMODAL_V4__?.attach)
      throw new Error("ChatGPT multimodal upload controller is not available");
    const instruction = String(text || "").trim()
      || "请直接理解我在通话中发送的图片或文件，并用语音回答，不要结束语音通话。";
    const composer = await waitFor(findComposer, 8000, 100);
    if (!composer) throw new Error("ChatGPT Voice text composer is not available");
    assertComposerDraftSafe(composer, instruction);
    // Reuse the canonical Worker v78 upload and settle evidence. No competing
    // uploader, fallback automation, or terminal decision is introduced.
    const upload = await globalThis.__CHAT2API_MULTIMODAL_V4__.attach(attachments);
    if (upload.attachment_verified !== true || upload.attachments_count !== attachments.length)
      throw new Error("ChatGPT did not confirm all live media attachments");
    assertLiveTurnOwner(active);
    await sendLiveText(active, itemId, instruction);
    await emitLive(active, "input.media.sent", {
      item_id: itemId || "",
      attachment_count: attachments.length,
    });
  }

  async function emitTerminal(requestId) {
    try {
      await chrome.runtime.sendMessage({
        type: "chat2api.event",
        event: {
          type: "image.completed",
          kind: "voice-live",
          request_id: requestId,
          images: [],
          live_terminal: true,
        },
      });
    } catch (_) {}
  }

  function stopButton() {
    for (const selector of [
      "button[data-testid='stop-button']",
      "button[aria-label='Stop streaming']",
      "button[aria-label='Stop generating']",
      "button[aria-label*='停止生成']",
    ]) {
      const button = [...document.querySelectorAll(selector)].find(item => visible(item) && !item.disabled);
      if (button) return button;
    }
    return null;
  }

  function messageIdentity(node) {
    const owner = node?.closest?.("[data-message-id]") || node;
    const messageId = String(owner?.getAttribute?.("data-message-id") || "").trim();
    return messageId ? "id:" + messageId : node;
  }

  async function waitForAssistantCompletion(active, baselineIdentity, baselineText, timeout = 45000) {
    const deadline = Date.now() + timeout;
    let lastIdentity = null;
    let lastText = "";
    let stableSince = 0;
    while (Date.now() < deadline) {
      assertLiveTurnOwner(active);
      const nodes = [...document.querySelectorAll("[data-message-author-role='assistant']")].filter(visible);
      const currentNode = nodes[nodes.length - 1] || null;
      const currentIdentity = messageIdentity(currentNode);
      const current = normalize(currentNode?.innerText || currentNode?.textContent || "");
      // Two consecutive assistant turns may legitimately have identical text
      // (the setup prompt explicitly asks for "已准备好"). Compare message
      // identity as well as text so a valid new acknowledgement is not
      // mistaken for the previous assistant answer and timed out.
      const isNewAssistantTurn = Boolean(currentNode) && currentIdentity !== baselineIdentity;
      if (current && (isNewAssistantTurn || current !== baselineText)) {
        if (currentIdentity !== lastIdentity || current !== lastText) {
          lastIdentity = currentIdentity;
          lastText = current;
          stableSince = Date.now();
        }
        if (!stopButton() && buttonReady(sendButton()) && stableSince && Date.now() - stableSince >= 1800) return current;
      } else {
        lastIdentity = null;
        lastText = "";
        stableSince = 0;
      }
      await delay(120);
    }
    throw new Error("ChatGPT did not finish the isolated Voice context setup turn");
  }

  async function prepareContext(active, text) {
    assertLiveTurnOwner(active);
    const baselineNodes = [...document.querySelectorAll("[data-message-author-role='assistant']")].filter(visible);
    const baselineNode = baselineNodes[baselineNodes.length - 1] || null;
    const baselineIdentity = messageIdentity(baselineNode);
    const baselineText = normalize(baselineNode?.innerText || baselineNode?.textContent || "");
    await queueTurn(() => sendLiveText(active,"session-context",String(text || "")));
    const responseText = await waitForAssistantCompletion(active,baselineIdentity,baselineText);
    return {acknowledged:true,responseText:sliceCodePoints(responseText,0,1200)};
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    const state = globalThis.__CHAT2API_VOICE_LIVE_CONTENT_V2__;
    if (message.type === "chat2api.voice.live.text") {
      const active = state?.active;
      if (!active || active.requestId !== message.requestId) {
        sendResponse({ ok: false, error: "Live session is not active" });
        return false;
      }
      queueTurn(() => sendLiveText(active, String(message.itemId || ""), String(message.text || "")))
        .then(() => sendResponse({ ok: true, controller: "voice-live-text-v24" }))
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error), controller: "voice-live-text-v24" }));
      return true;
    }
    if (message.type === "chat2api.voice.live.multimodal") {
      const active = state?.active;
      if (!active || active.requestId !== message.requestId) {
        sendResponse({ ok: false, error: "Live session is not active" });
        return false;
      }
      queueTurn(() => sendLiveMedia(active, String(message.itemId || ""),
        String(message.text || ""), message.attachments || [], message.videoFrame || null))
        .then(() => sendResponse({ ok: true, controller: "voice-live-multimodal-v195" }))
        .catch(error => sendResponse({ ok: false, error: String(error?.message || error), controller: "voice-live-multimodal-v195" }));
      return true;
    }
    if (message.type === "chat2api.voice.live.stop") {
      const requestId = String(message.requestId || state?.active?.requestId || "");
      if (requestId) setTimeout(() => emitTerminal(requestId), 180);
      return false;
    }
    return false;
  });

  globalThis[KEY] = Object.freeze({
    prepareContext,
    sendText:(active,itemId,text) => queueTurn(() => sendLiveText(active,itemId,text)),
  });
})();
