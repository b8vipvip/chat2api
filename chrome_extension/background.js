const DEFAULTS = {
  serverUrl: "https://chat2api.mv3.cn",
  pairingCode: "",
  clientId: "",
  clientToken: "",
  extensionName: "Chrome",
  boundTabId: null,
  autoBind: false,
  socketState: "disconnected",
  socketError: "",
  models: [],
  currentModel: "default",
  modelsUpdatedAt: 0,
};
const CHATGPT_URLS = ["https://chatgpt.com/*", "https://www.chatgpt.com/*", "https://chat.openai.com/*"];
const NATIVE_CAPACITY_CONTROL_VERSION = 36;
globalThis.__CHAT2API_NATIVE_CAPACITY_CONTROL_VERSION__ = NATIVE_CAPACITY_CONTROL_VERSION;
globalThis.__CHAT2API_NATIVE_CAPACITY_DISPATCH_V37__ = true;
let socket = null;
let reconnectTimer = null;
let reconnectAttempt = 0;
let keepAliveTimer = null;
let modelDiscoveryInFlight = null;

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function config() { return chrome.storage.local.get(DEFAULTS); }
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
  try { return ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(new URL(value).hostname); }
  catch (_) { return false; }
}
async function chatTabs() {
  const tabs = await chrome.tabs.query({ url: CHATGPT_URLS });
  return tabs.filter(tab => Number.isInteger(tab.id) && isChatGptUrl(tab.url || tab.pendingUrl || ""));
}
async function ensureContent(tabId) {
  try {
    const response = await chrome.tabs.sendMessage(tabId, { type: "chat2api.ping" });
    if (response?.ok) return;
  } catch (_) {}
  await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js", "content_multimodal.js"] });
  await sleep(180);
  const response = await chrome.tabs.sendMessage(tabId, { type: "chat2api.ping" });
  if (!response?.ok) throw new Error("ChatGPT page controller did not respond. Reload the tab.");
}
async function maybeAutoBind() {
  const settings = await config();
  if (Number.isInteger(settings.boundTabId) || !settings.autoBind) return null;
  const tabs = await chatTabs();
  if (tabs.length !== 1) return null;
  await ensureContent(tabs[0].id);
  await chrome.storage.local.set({ boundTabId: tabs[0].id });
  return tabs[0];
}
async function resolveTargetTab() {
  const settings = await config();
  if (Number.isInteger(settings.boundTabId)) {
    try {
      const tab = await chrome.tabs.get(settings.boundTabId);
      if (isChatGptUrl(tab.url || tab.pendingUrl || "")) return tab;
    } catch (_) {}
    await chrome.storage.local.set({ boundTabId: null });
  }
  await maybeAutoBind();
  const refreshed = await config();
  if (Number.isInteger(refreshed.boundTabId)) {
    const tab = await chrome.tabs.get(refreshed.boundTabId);
    if (isChatGptUrl(tab.url || tab.pendingUrl || "")) return tab;
  }
  const tabs = await chatTabs();
  if (!tabs.length) throw new Error("No ChatGPT tab is open.");
  if (tabs.length > 1) throw new Error("Multiple ChatGPT tabs are open. Bind the target tab in the extension popup.");
  return tabs[0];
}
function socketReady() { return Boolean(socket && socket.readyState === WebSocket.OPEN); }
async function sendSocket(payload) {
  if (!socketReady()) throw new Error("Server WebSocket is not connected");
  socket.send(JSON.stringify(payload));
}
async function trySendSocket(payload) {
  if (!socketReady()) return false;
  try { socket.send(JSON.stringify(payload)); return true; }
  catch (_) { return false; }
}
function nativeCapacityControlMetadata() {
  const controller = globalThis.__CHAT2API_CAPACITY_CONTROL_V35__;
  const ready = Boolean(controller && typeof controller.handle === "function" && typeof controller.snapshot === "function");
  return {
    extension_control_version: ready ? NATIVE_CAPACITY_CONTROL_VERSION : 0,
    extension_control_ready: ready,
    extension_control_transport: ready ? "background-native-dispatch-v37" : "background-native-controller-pending-v37",
    extension_control_last_error: ready ? null : "Capacity controller v35 is not ready",
    extension_control_capability_reporter: 37,
    extension_control_capability_reported_at: new Date().toISOString(),
  };
}

async function discoverModels(tab, force = false) {
  if (!tab?.id) return { models: [], current_model: "default" };
  const settings = await config();
  if (!force && settings.modelsUpdatedAt && Date.now() - Number(settings.modelsUpdatedAt) < 300000) {
    return { models: settings.models || [], current_model: settings.currentModel || "default" };
  }
  if (modelDiscoveryInFlight) return modelDiscoveryInFlight;
  modelDiscoveryInFlight = (async () => {
    try {
      await ensureContent(tab.id);
      const response = await chrome.tabs.sendMessage(tab.id, { type: "chat2api.models.discover" });
      if (!response?.ok) throw new Error(response?.error || "Model discovery failed");
      const data = response.data || {};
      await chrome.storage.local.set({
        models: Array.isArray(data.models) ? data.models : [],
        currentModel: data.current_model || "default",
        modelsUpdatedAt: Date.now(),
        modelDiscoveryError: "",
      });
      return data;
    } catch (error) {
      await chrome.storage.local.set({ modelDiscoveryError: String(error?.message || error) });
      return { models: settings.models || [], current_model: settings.currentModel || "default" };
    } finally { modelDiscoveryInFlight = null; }
  })();
  return modelDiscoveryInFlight;
}

async function sendExtensionStatus(forceModelDiscovery = false) {
  const settings = await config();
  const tabs = await chatTabs();
  let bound = null;
  if (Number.isInteger(settings.boundTabId)) bound = tabs.find(tab => tab.id === settings.boundTabId) || null;
  if (!bound && settings.autoBind && tabs.length === 1) bound = await maybeAutoBind();
  let modelData = { models: settings.models || [], current_model: settings.currentModel || "default" };
  if (bound && forceModelDiscovery) modelData = await discoverModels(bound, true);
  await trySendSocket({
    type: "extension.status",
    metadata: {
      extension_version: chrome.runtime.getManifest().version,
      tab_count: tabs.length,
      bound_tab_id: bound?.id || null,
      bound_url: bound?.url || "",
      bound_title: bound?.title || "",
      models: modelData.models || [],
      current_model: modelData.current_model || "default",
      capabilities: ["text", "vision", "file-understanding", "image-generation", "model-selection", "diagnostics", "estimated-token-usage"],
      ...nativeCapacityControlMetadata(),
    },
  });
}

async function handleServerMessage(message) {
  if (message.type === "heartbeat.ack" || message.type === "server.hello") return;
  if (message.type === "extension.control") {
    const controller = globalThis.__CHAT2API_CAPACITY_CONTROL_V35__;
    if (controller && typeof controller.handle === "function") {
      return controller.handle(message);
    }
    const error = "Native capacity dispatcher is ready but Capacity controller v35 is unavailable";
    await trySendSocket({
      type: "extension.control.result",
      control_id: String(message?.control_id || ""),
      action: String(message?.action || ""),
      ok: false,
      data: {},
      error,
      metadata: {
        ...nativeCapacityControlMetadata(),
        extension_control_last_error: error,
      },
    });
    return;
  }
  if (message.type === "chat.request") {
    try {
      const tab = await resolveTargetTab();
      await ensureContent(tab.id);
      const response = await chrome.tabs.sendMessage(tab.id, {
        type: "chat2api.request",
        requestId: message.request_id,
        prompt: message.prompt,
        attachments: message.attachments || [],
        options: message.options || {},
      });
      if (!response?.ok) throw new Error(response?.error || "ChatGPT tab rejected the request");
    } catch (error) {
      await trySendSocket({ type: "chat.error", request_id: message.request_id, error: String(error?.message || error) });
    }
    return;
  }
  if (message.type === "chat.cancel") {
    try {
      const tab = await resolveTargetTab();
      await ensureContent(tab.id);
      await chrome.tabs.sendMessage(tab.id, { type: "chat2api.cancel", requestId: message.request_id });
    } catch (error) {
      await trySendSocket({ type: "chat.cancelled", request_id: message.request_id, reason: String(error?.message || error) });
    }
  }
}

async function pair({ serverUrl, pairingCode, extensionName, force = false, autoBind = undefined }) {
  const cleanServer = String(serverUrl || DEFAULTS.serverUrl).trim().replace(/\/$/, "");
  const savedPairingCode = String(pairingCode || "");
  const savedExtensionName = String(extensionName || "Chrome").trim() || "Chrome";
  const existing = await config();
  const sameServer = cleanServer === String(existing.serverUrl || "").replace(/\/$/, "");
  if (!force && sameServer && existing.clientId && existing.clientToken) {
    await chrome.storage.local.set({
      serverUrl: cleanServer,
      pairingCode: savedPairingCode || existing.pairingCode,
      extensionName: savedExtensionName,
      autoBind: autoBind === undefined ? existing.autoBind : Boolean(autoBind),
    });
    if (socket) socket.close(4000, "Reconnect requested");
    await connectSocket();
    return { client_id: existing.clientId, reused: true };
  }
  const response = await fetch(`${cleanServer}/api/extensions/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Pairing-Code": savedPairingCode },
    body: JSON.stringify({ name: savedExtensionName, browser_name: "Chrome", version: chrome.runtime.getManifest().version, metadata: { runtime_id: chrome.runtime.id } }),
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  const result = await response.json();
  await chrome.storage.local.set({
    serverUrl: cleanServer,
    pairingCode: savedPairingCode,
    extensionName: savedExtensionName,
    clientId: result.client_id,
    clientToken: result.token,
    autoBind: autoBind === undefined ? existing.autoBind : Boolean(autoBind),
    pairedAt: new Date().toISOString(),
  });
  if (socket) socket.close(4000, "Re-pairing");
  await connectSocket();
  return result;
}

async function connectSocket() {
  clearTimeout(reconnectTimer);
  clearInterval(keepAliveTimer);
  const settings = await config();
  if (!settings.clientId || !settings.clientToken) { await updateState("unpaired"); return; }
  if (socket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(socket.readyState)) return;
  await updateState("connecting");
  try { socket = new WebSocket(wsUrl(settings.serverUrl, settings.clientId, settings.clientToken)); }
  catch (error) { await updateState("error", String(error?.message || error)); scheduleReconnect(); return; }
  socket.onopen = async () => {
    reconnectAttempt = 0;
    await updateState("connected");
    await sendSocket({
      type: "extension.hello",
      metadata: {
        extension_version: chrome.runtime.getManifest().version,
        runtime_id: chrome.runtime.id,
        ...nativeCapacityControlMetadata(),
      },
    });
    await maybeAutoBind();
    await sendExtensionStatus(false);
    keepAliveTimer = setInterval(() => trySendSocket({ type: "heartbeat", ts: Date.now() }), 20000);
  };
  socket.onmessage = event => {
    try { handleServerMessage(JSON.parse(event.data)).catch(console.error); }
    catch (error) { console.warn("chat2api invalid server message", error); }
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
  reconnectTimer = setTimeout(() => connectSocket().catch(console.error), Math.min(30000, 1000 * 2 ** Math.min(reconnectAttempt, 5)));
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  return btoa(binary);
}
async function fetchAttachment(fileId) {
  const settings = await config();
  if (!settings.clientId || !settings.clientToken) throw new Error("Extension is not paired");
  const url = `${String(settings.serverUrl).replace(/\/$/, "")}/api/extensions/files/${encodeURIComponent(fileId)}?client_id=${encodeURIComponent(settings.clientId)}&token=${encodeURIComponent(settings.clientToken)}`;
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`Attachment ${fileId} download failed: HTTP ${response.status}`);
  const filename = response.headers.get("X-Chat2API-Filename") || fileId;
  const mimeType = response.headers.get("Content-Type") || "application/octet-stream";
  const buffer = await response.arrayBuffer();
  return { file_id: fileId, filename, mime_type: mimeType, size: buffer.byteLength, base64: arrayBufferToBase64(buffer) };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "chat2api.event") { trySendSocket(message.event).then(sent => sendResponse({ ok: sent })); return true; }
  if (message.type === "chat2api.attachment.fetch") {
    fetchAttachment(message.fileId).then(data => sendResponse({ ok: true, data })).catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }
  if (message.type === "popup.status") { Promise.all([config(), chatTabs()]).then(([settings, tabs]) => sendResponse({ ok: true, settings, tabs })); return true; }
  if (message.type === "popup.pair") { pair(message).then(data => sendResponse({ ok: true, data })).catch(error => sendResponse({ ok: false, error: String(error?.message || error) })); return true; }
  if (message.type === "popup.connect") { connectSocket().then(() => sendResponse({ ok: true })).catch(error => sendResponse({ ok: false, error: String(error?.message || error) })); return true; }
  if (message.type === "popup.discoverModels") {
    resolveTargetTab().then(tab => discoverModels(tab, true)).then(data => sendExtensionStatus(false).then(() => sendResponse({ ok: true, data }))).catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }
  if (message.type === "popup.bind") {
    chrome.tabs.query({ active: true, currentWindow: true }).then(async tabs => {
      const tab = tabs[0];
      if (!tab?.id || !isChatGptUrl(tab.url || "")) throw new Error("The active tab is not ChatGPT");
      await ensureContent(tab.id);
      await chrome.storage.local.set({ boundTabId: tab.id, autoBind: false, modelsUpdatedAt: 0 });
      await sendExtensionStatus(false);
      sendResponse({ ok: true, tab: { id: tab.id, title: tab.title, url: tab.url } });
    }).catch(error => sendResponse({ ok: false, error: String(error?.message || error) }));
    return true;
  }
  if (message.type === "popup.unbind") {
    chrome.storage.local.set({ boundTabId: null }).then(async () => { await sendExtensionStatus(false); sendResponse({ ok: true }); });
    return true;
  }
  return false;
});
chrome.tabs.onRemoved.addListener(tabId => { config().then(settings => { if (settings.boundTabId === tabId) chrome.storage.local.set({ boundTabId: null }); }); });
chrome.tabs.onUpdated.addListener((_tabId, changeInfo) => { if (changeInfo.status === "complete") maybeAutoBind().then(() => sendExtensionStatus(false)).catch(() => {}); });
chrome.alarms.create("chat2api-keepalive", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name !== "chat2api-keepalive") return;
  if (socketReady()) { trySendSocket({ type: "heartbeat", ts: Date.now() }); sendExtensionStatus(false).catch(() => {}); }
  else connectSocket().catch(() => {});
});
chrome.runtime.onInstalled.addListener(() => connectSocket().catch(console.error));
chrome.runtime.onStartup.addListener(() => connectSocket().catch(console.error));
connectSocket().catch(console.error);
