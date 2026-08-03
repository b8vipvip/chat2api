const DEFAULTS = {
  serverUrl: "http://127.0.0.1:8765",
  clientId: "",
  clientToken: "",
  extensionName: "Chrome",
  boundTabId: null,
  socketState: "disconnected",
  socketError: "",
};
const CHATGPT_URLS = ["https://chatgpt.com/*", "https://www.chatgpt.com/*", "https://chat.openai.com/*"];
let socket = null;
let reconnectTimer = null;
let reconnectAttempt = 0;
let keepAliveTimer = null;

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function config() {
  return chrome.storage.local.get(DEFAULTS);
}

async function updateState(socketState, socketError = "") {
  await chrome.storage.local.set({ socketState, socketError, socketUpdatedAt: new Date().toISOString() });
}

function wsUrl(serverUrl, clientId, token) {
  const url = new URL(serverUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = `/ws/extensions/${encodeURIComponent(clientId)}`;
  url.search = `?token=${encodeURIComponent(token)}`;
  return url.toString();
}

function isChatGptUrl(value = "") {
  try {
    return ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(new URL(value).hostname);
  } catch (_) {
    return false;
  }
}

async function chatTabs() {
  const tabs = await chrome.tabs.query({ url: CHATGPT_URLS });
  return tabs.filter(tab => Number.isInteger(tab.id) && isChatGptUrl(tab.url || ""));
}

async function ensureContent(tabId) {
  try {
    const response = await chrome.tabs.sendMessage(tabId, { type: "chat2api.ping" });
    if (response?.ok) return;
  } catch (_) {}
  await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
  await sleep(150);
  const response = await chrome.tabs.sendMessage(tabId, { type: "chat2api.ping" });
  if (!response?.ok) throw new Error("ChatGPT page controller did not respond. Reload the tab.");
}

async function resolveTargetTab() {
  const settings = await config();
  if (Number.isInteger(settings.boundTabId)) {
    try {
      const tab = await chrome.tabs.get(settings.boundTabId);
      if (isChatGptUrl(tab.url || "")) return tab;
    } catch (_) {}
    await chrome.storage.local.set({ boundTabId: null });
  }
  const tabs = await chatTabs();
  if (!tabs.length) throw new Error("No ChatGPT tab is open.");
  if (tabs.length > 1) throw new Error("Multiple ChatGPT tabs are open. Bind the target tab in the extension popup.");
  return tabs[0];
}

async function sendSocket(payload) {
  if (!socket || socket.readyState !== WebSocket.OPEN) throw new Error("Server WebSocket is not connected");
  socket.send(JSON.stringify(payload));
}

async function sendExtensionStatus() {
  const settings = await config();
  const tabs = await chatTabs();
  let bound = null;
  if (Number.isInteger(settings.boundTabId)) {
    bound = tabs.find(tab => tab.id === settings.boundTabId) || null;
  }
  if (socket?.readyState === WebSocket.OPEN) {
    await sendSocket({
      type: "extension.status",
      metadata: {
        extension_version: chrome.runtime.getManifest().version,
        tab_count: tabs.length,
        bound_tab_id: bound?.id || null,
        bound_url: bound?.url || "",
        bound_title: bound?.title || "",
      },
    });
  }
}

async function handleServerMessage(message) {
  if (message.type === "heartbeat.ack" || message.type === "server.hello") return;
  if (message.type === "chat.request") {
    try {
      const tab = await resolveTargetTab();
      await ensureContent(tab.id);
      const response = await chrome.tabs.sendMessage(tab.id, {
        type: "chat2api.request",
        requestId: message.request_id,
        prompt: message.prompt,
        options: message.options || {},
      });
      if (!response?.ok) throw new Error(response?.error || "ChatGPT tab rejected the request");
    } catch (error) {
      await sendSocket({ type: "chat.error", request_id: message.request_id, error: String(error?.message || error) });
    }
    return;
  }
  if (message.type === "chat.cancel") {
    try {
      const tab = await resolveTargetTab();
      await ensureContent(tab.id);
      await chrome.tabs.sendMessage(tab.id, { type: "chat2api.cancel", requestId: message.request_id });
    } catch (error) {
      await sendSocket({ type: "chat.cancelled", request_id: message.request_id, reason: String(error?.message || error) });
    }
  }
}

async function connectSocket() {
  clearTimeout(reconnectTimer);
  clearInterval(keepAliveTimer);
  const settings = await config();
  if (!settings.clientId || !settings.clientToken) {
    await updateState("unpaired");
    return;
  }
  if (socket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(socket.readyState)) return;
  await updateState("connecting");
  try {
    socket = new WebSocket(wsUrl(settings.serverUrl, settings.clientId, settings.clientToken));
  } catch (error) {
    await updateState("error", String(error?.message || error));
    scheduleReconnect();
    return;
  }
  socket.onopen = async () => {
    reconnectAttempt = 0;
    await updateState("connected");
    await sendSocket({
      type: "extension.hello",
      metadata: {
        extension_version: chrome.runtime.getManifest().version,
        runtime_id: chrome.runtime.id,
      },
    });
    await sendExtensionStatus();
    keepAliveTimer = setInterval(() => {
      sendSocket({ type: "heartbeat", ts: Date.now() }).catch(() => {});
    }, 20000);
  };
  socket.onmessage = event => {
    try {
      handleServerMessage(JSON.parse(event.data)).catch(console.error);
    } catch (error) {
      console.warn("chat2api invalid server message", error);
    }
  };
  socket.onerror = () => updateState("error", "WebSocket connection error");
  socket.onclose = event => {
    clearInterval(keepAliveTimer);
    updateState("disconnected", event.reason || `WebSocket closed (${event.code})`);
    scheduleReconnect();
  };
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectAttempt += 1;
  const delay = Math.min(30000, 1000 * 2 ** Math.min(reconnectAttempt, 5));
  reconnectTimer = setTimeout(() => connectSocket().catch(console.error), delay);
}

async function pair({ serverUrl, pairingCode, extensionName }) {
  const cleanServer = String(serverUrl || DEFAULTS.serverUrl).replace(/\/$/, "");
  const response = await fetch(`${cleanServer}/api/extensions/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Pairing-Code": pairingCode || "" },
    body: JSON.stringify({
      name: extensionName || "Chrome",
      browser_name: "Chrome",
      version: chrome.runtime.getManifest().version,
      metadata: { runtime_id: chrome.runtime.id },
    }),
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  const result = await response.json();
  await chrome.storage.local.set({
    serverUrl: cleanServer,
    extensionName: extensionName || "Chrome",
    clientId: result.client_id,
    clientToken: result.token,
    pairedAt: new Date().toISOString(),
  });
  if (socket) socket.close(4000, "Re-pairing");
  await connectSocket();
  return result;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "chat2api.event") {
    sendSocket(message.event).catch(console.warn);
    sendResponse({ ok: true });
    return false;
  }
  if (message.type === "popup.status") {
    Promise.all([config(), chatTabs()]).then(([settings, tabs]) => sendResponse({ ok: true, settings, tabs }));
    return true;
  }
  if (message.type === "popup.pair") {
    pair(message).then(data => sendResponse({ ok: true, data })).catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }
  if (message.type === "popup.connect") {
    connectSocket().then(() => sendResponse({ ok: true })).catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }
  if (message.type === "popup.bind") {
    chrome.tabs.query({ active: true, currentWindow: true }).then(async tabs => {
      const tab = tabs[0];
      if (!tab?.id || !isChatGptUrl(tab.url || "")) throw new Error("The active tab is not ChatGPT");
      await ensureContent(tab.id);
      await chrome.storage.local.set({ boundTabId: tab.id });
      await sendExtensionStatus();
      sendResponse({ ok: true, tab: { id: tab.id, title: tab.title, url: tab.url } });
    }).catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }
  if (message.type === "popup.unbind") {
    chrome.storage.local.set({ boundTabId: null }).then(async () => {
      await sendExtensionStatus();
      sendResponse({ ok: true });
    });
    return true;
  }
  return false;
});

chrome.tabs.onRemoved.addListener(tabId => {
  config().then(settings => {
    if (settings.boundTabId === tabId) chrome.storage.local.set({ boundTabId: null });
  });
});
chrome.tabs.onUpdated.addListener(() => sendExtensionStatus().catch(() => {}));
chrome.alarms.create("chat2api-keepalive", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name !== "chat2api-keepalive") return;
  if (socket?.readyState === WebSocket.OPEN) sendSocket({ type: "heartbeat", ts: Date.now() }).catch(() => {});
  else connectSocket().catch(() => {});
});
chrome.runtime.onInstalled.addListener(() => connectSocket().catch(console.error));
chrome.runtime.onStartup.addListener(() => connectSocket().catch(console.error));
connectSocket().catch(console.error);
