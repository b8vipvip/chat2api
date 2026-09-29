const $ = id => document.getElementById(id);
const DEFAULT_SERVER_URL = "https://chat2api.mv3.cn";
const TEXT_MODELS = ["gpt-5.6-sol", "gpt-5.5", "gpt-5.5-mini"];
const REASONING_LABELS = { instant: "极速", medium: "中", high: "高", low: "极速" };
const EXTENSION_VERSION = chrome.runtime.getManifest().version;
let formInitialized = false;

async function send(message) { return chrome.runtime.sendMessage(message); }
async function persistForm() {
  await chrome.storage.local.set({
    serverUrl: $("serverUrl").value.trim(),
    pairingCode: $("pairingCode").value,
    extensionName: $("extensionName").value.trim(),
  });
}
async function runtimeStatus() {
  return chrome.storage.local.get({
    platformOs: "",
    platformArch: "",
    networkProbeStatus: "unknown",
    networkCountryCode: "",
    networkProbeError: "",
    chatgptLoginState: "unknown",
    chatgptLoginConfidence: "low",
    chatgptLoginStrategy: "unknown",
    chatgptLoginComposerReady: false,
    chatgptLoginCheckedAt: 0,
  });
}
function renderModels(settings) {
  const models = Array.isArray(settings.models) && settings.models.length
    ? settings.models.filter(item => TEXT_MODELS.includes(item?.id))
    : [];
  const current = TEXT_MODELS.includes(settings.currentModel) ? settings.currentModel : null;
  const reasoning = REASONING_LABELS[settings.currentReasoning] || settings.currentReasoning || "自动";
  if (!models.length && !current) {
    $("models").textContent = "模型状态将在请求和页面探测时自动更新，无需手动刷新或打开模型菜单。";
    return;
  }
  const labels = models.map(item => `${item.id === current || item.selected ? "✓ " : ""}${item.id}`);
  $("models").textContent = `模型：${labels.join("、") || current || "自动"} · 当前：${current || "自动"} · 推理：${reasoning}`;
}
function platformLabel(settings) {
  const os = String(settings.platformOs || "").toLowerCase();
  const arch = String(settings.platformArch || "").toLowerCase();
  const names = { win: "Windows", linux: "Linux", mac: "macOS", cros: "ChromeOS", openbsd: "OpenBSD" };
  return `${names[os] || os || "Windows"}${arch ? `/${arch}` : ""}`;
}
function networkLabel(settings) {
  const status = String(settings.networkProbeStatus || "unknown");
  const country = String(settings.networkCountryCode || "").toUpperCase();
  if (status === "external") return `外网${country ? ` ${country}` : ""}`;
  if (status === "china-mainland") return "中国大陆网络";
  if (status === "offline") return "浏览器离线";
  if (status === "error") return "网络检测异常";
  return "网络待检测";
}
function renderLogin(settings) {
  const state = String(settings.chatgptLoginState || "unknown");
  const strategy = String(settings.chatgptLoginStrategy || "unknown");
  const ready = state === "ready" && settings.chatgptLoginComposerReady === true;
  if (ready) $("loginStatus").textContent = "已登录 · Composer 可用";
  else if (state === "login_required") $("loginStatus").textContent = "需要登录 · 请在可见 ChatGPT 窗口完成认证";
  else if (state === "checking") $("loginStatus").textContent = "正在检测登录状态…";
  else $("loginStatus").textContent = `登录状态待确认${strategy === "no-chatgpt-tab" ? " · 当前没有 ChatGPT 页面" : ""}`;
  $("openLogin").hidden = ready || state === "checking";
}
function humanSocketError(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (/WebSocket closed \(1006\)/i.test(text)) {
    return "连接异常断开（1006）。Worker 会自动重连；如果你刚更换设备码，请点击“绑定 / 更换设备码”重新注册新身份。";
  }
  if (/worker_active_request_lease|still has active requests/i.test(text)) {
    return "当前 Worker 仍有请求未进入终态，暂不能更换设备码。请等待正在处理的请求结束后重试。";
  }
  return text;
}
function setMessage(text, kind = "") {
  const node = $("message");
  node.textContent = text || "";
  node.className = kind ? `message ${kind}` : "message";
}
async function refresh() {
  const [response, localRuntime] = await Promise.all([
    send({ type: "popup.status" }),
    runtimeStatus(),
  ]);
  if (!response?.ok) return;
  const settings = { ...(response.settings || {}), ...(localRuntime || {}) };
  const tabs = response.tabs || [];
  $("versionInfo").textContent = `v${EXTENSION_VERSION} · ${platformLabel(settings)}`;
  if (!formInitialized) {
    $("serverUrl").value = settings.serverUrl || DEFAULT_SERVER_URL;
    $("pairingCode").value = settings.pairingCode || "";
    $("extensionName").value = settings.extensionName || "Windows Worker";
    formInitialized = true;
  }
  const socketState = String(settings.socketState || "disconnected");
  const status = $("status");
  const worker = settings.clientId ? `Worker ${settings.clientId}` : "尚未绑定设备码";
  status.textContent = `${socketState === "connected" ? "已连接" : socketState === "connecting" ? "连接中" : socketState === "unpaired" ? "未绑定" : "未连接"} · ${worker} · ${networkLabel(settings)}`;
  status.className = `status ${socketState === "connected" ? "connected" : socketState === "error" ? "error" : ""}`;
  $("statusDot").className = `status-dot ${socketState === "connected" ? "connected" : socketState === "connecting" ? "connecting" : ""}`;
  renderLogin(settings);
  $("binding").textContent = `窗口自动管理 · 当前检测到 ${tabs.length} 个 ChatGPT 页面${Number.isInteger(settings.boundTabId) ? " · 已有活动路由页面" : ""}`;
  renderModels(settings);
  const socketError = humanSocketError(settings.socketError);
  if (socketError) setMessage(socketError, socketState === "connected" ? "" : "error");
  else if (settings.networkProbeError && settings.networkProbeStatus === "error") setMessage(`网络检测：${settings.networkProbeError}`, "error");
  else if (settings.lastModelSelectionError) setMessage(`上次模型选择失败：${settings.lastModelSelectionError}`, "error");
  else if (socketState === "connected" && $("message").classList.contains("error")) setMessage("");
}
for (const id of ["serverUrl", "pairingCode", "extensionName"]) {
  $(id).addEventListener("input", () => persistForm().catch(error => setMessage(`保存配置失败：${String(error?.message || error)}`, "error")));
}
$("openLogin").addEventListener("click", async () => {
  setMessage("正在打开 ChatGPT 登录窗口…");
  const response = await send({ type: "popup.login.open" });
  if (!response?.ok) setMessage(response?.error || "打开登录窗口失败", "error");
  else setMessage(response.data?.existing ? "已切换到现有 ChatGPT 登录窗口，请完成登录。" : "已打开 ChatGPT 登录窗口，请完成登录。");
  await refresh();
});
$("refreshLogin").addEventListener("click", async () => {
  setMessage("正在检测 ChatGPT 登录状态…");
  const response = await send({ type: "popup.login.refresh" });
  if (!response?.ok) setMessage(response?.error || "登录状态检测失败", "error");
  else if (response.data?.state === "ready") setMessage("ChatGPT 登录状态正常，Composer 已确认可用。", "success");
  else if (response.data?.state === "login_required") setMessage("ChatGPT 需要登录，请打开登录窗口完成认证。", "error");
  else setMessage("暂未确认 ChatGPT 登录状态，可打开登录窗口继续检查。");
  await refresh();
});
$("pair").addEventListener("click", async () => {
  const pairingCode = $("pairingCode").value.trim();
  if (!pairingCode) {
    setMessage("请输入新的设备码。", "error");
    return;
  }
  setMessage("正在使用设备码注册 Windows Worker…");
  await persistForm();
  // Manual Windows pairing is an explicit identity replacement. Always force a
  // fresh registration so a newly entered device code cannot silently reuse the
  // old clientId/clientToken and then surface the old socket's 1006 close event.
  const response = await send({
    type: "popup.pair",
    serverUrl: $("serverUrl").value.trim(),
    pairingCode,
    extensionName: $("extensionName").value.trim(),
    force: true,
    autoBind: false,
  });
  if (!response?.ok) setMessage(humanSocketError(response?.error) || "设备码绑定失败", "error");
  else setMessage("设备码绑定成功，已注册新 Worker 身份并开始连接。", "success");
  await refresh();
});
$("connect").addEventListener("click", async () => {
  await persistForm();
  setMessage("正在重新连接…");
  const response = await send({ type: "popup.connect" });
  if (!response?.ok) setMessage(humanSocketError(response?.error) || "连接失败", "error");
  await refresh();
});
$("versionInfo").textContent = `v${EXTENSION_VERSION}`;
refresh().catch(error => setMessage(String(error?.message || error), "error"));
setInterval(() => refresh().catch(() => {}), 2000);
