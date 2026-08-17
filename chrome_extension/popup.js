const $ = id => document.getElementById(id);
const DEFAULT_SERVER_URL = "https://chat2api.mv3.cn";
const TEXT_MODELS = ["gpt-5.6-sol", "gpt-5.5"];
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
    : TEXT_MODELS.map(id => ({ id }));
  const current = TEXT_MODELS.includes(settings.currentModel) ? settings.currentModel : null;
  const reasoning = REASONING_LABELS[settings.currentReasoning] || settings.currentReasoning || "未知";
  const labels = models.map(item => `${item.id === current || item.selected ? "✓ " : ""}${item.id}`);
  $("models").textContent = `文本模型：${labels.join("、")}。当前模型：${current || "尚未被动确认"}；推理强度：${reasoning}。API 请求会先被动识别页面状态，匹配时零切换直接执行。`;
}
function platformLabel(settings) {
  const os = String(settings.platformOs || "").toLowerCase();
  const arch = String(settings.platformArch || "").toLowerCase();
  const names = { win: "Windows", linux: "Linux", mac: "macOS", cros: "ChromeOS", openbsd: "OpenBSD" };
  return `${names[os] || os || "未知平台"}${arch ? `/${arch}` : ""}`;
}
function networkLabel(settings) {
  const status = String(settings.networkProbeStatus || "unknown");
  const country = String(settings.networkCountryCode || "").toUpperCase();
  if (status === "external") return `外网${country ? `(${country})` : ""} · 已允许主动预热`;
  if (status === "china-mainland") return "中国大陆网络(CN) · 禁止主动预热";
  if (status === "offline") return "浏览器离线";
  if (status === "error") return "外网检测失败 · 请求时仍可按需创建窗口";
  return "网络区域待检测";
}
function renderLogin(settings) {
  const state = String(settings.chatgptLoginState || "unknown");
  const strategy = String(settings.chatgptLoginStrategy || "unknown");
  const ready = state === "ready" && settings.chatgptLoginComposerReady === true;
  if (ready) $("loginStatus").textContent = "ChatGPT：已登录，可用 · Composer 已确认";
  else if (state === "login_required") $("loginStatus").textContent = "ChatGPT：需要登录 · 请在可见窗口完成登录/CAPTCHA/2FA";
  else if (state === "checking") $("loginStatus").textContent = "ChatGPT：正在检测登录状态…";
  else $("loginStatus").textContent = `ChatGPT：登录状态未确认${strategy === "no-chatgpt-tab" ? " · 当前没有 ChatGPT 页面" : ""}`;
  $("openLogin").hidden = ready || state === "checking";
}
async function refresh() {
  const [response, localRuntime] = await Promise.all([
    send({ type: "popup.status" }),
    runtimeStatus(),
  ]);
  if (!response?.ok) return;
  const settings = { ...(response.settings || {}), ...(localRuntime || {}) };
  const tabs = response.tabs || [];
  $("versionInfo").textContent = `Chrome Bridge · v${EXTENSION_VERSION} · ${platformLabel(settings)}`;
  if (!formInitialized) {
    $("serverUrl").value = settings.serverUrl || DEFAULT_SERVER_URL;
    $("pairingCode").value = settings.pairingCode || "";
    $("extensionName").value = settings.extensionName || "Chrome";
    formInitialized = true;
  }
  const status = $("status");
  status.textContent = `${settings.socketState || "disconnected"}${settings.clientId ? ` · ${settings.clientId}` : " · 未配对"} · ${networkLabel(settings)}`;
  status.className = `status ${settings.socketState === "connected" ? "connected" : settings.socketState === "error" ? "error" : ""}`;
  renderLogin(settings);
  const bound = tabs.find(tab => tab.id === settings.boundTabId);
  $("binding").textContent = bound ? `已绑定：${bound.title || bound.url}` : `未绑定；当前检测到 ${tabs.length} 个 ChatGPT 标签页。API 请求会复用唯一标签页，或由扩展自动创建新的 ChatGPT 标签页。`;
  renderModels(settings);
  if (settings.socketError) $("message").textContent = settings.socketError;
  else if (settings.networkProbeError && settings.networkProbeStatus === "error") $("message").textContent = `外网检测：${settings.networkProbeError}`;
  else if (settings.lastModelSelectionError) $("message").textContent = `上次模型/推理强度选择失败：${settings.lastModelSelectionError}`;
}
for (const id of ["serverUrl", "pairingCode", "extensionName"]) {
  $(id).addEventListener("input", () => persistForm().catch(error => { $("message").textContent = `保存配置失败：${String(error?.message || error)}`; }));
}
$("openLogin").addEventListener("click", async () => {
  $("message").textContent = "正在打开 ChatGPT 登录窗口…";
  const response = await send({ type: "popup.login.open" });
  if (!response?.ok) $("message").textContent = response?.error || "打开登录窗口失败";
  else $("message").textContent = response.data?.existing ? "已切换到现有 ChatGPT 登录窗口，请手动完成登录。" : "已打开 ChatGPT 登录窗口，请手动完成登录。";
  await refresh();
});
$("refreshLogin").addEventListener("click", async () => {
  $("message").textContent = "正在被动检测 ChatGPT 登录状态…";
  const response = await send({ type: "popup.login.refresh" });
  if (!response?.ok) $("message").textContent = response?.error || "登录状态检测失败";
  else if (response.data?.state === "ready") $("message").textContent = "ChatGPT 登录状态正常，Composer 已确认可用。";
  else if (response.data?.state === "login_required") $("message").textContent = "检测到 ChatGPT 需要登录，请打开登录窗口手动完成认证。";
  else $("message").textContent = "暂未确认 ChatGPT 登录状态，可打开登录窗口继续检查。";
  await refresh();
});
$("pair").addEventListener("click", async () => {
  $("message").textContent = "";
  await persistForm();
  const response = await send({ type: "popup.pair", serverUrl: $("serverUrl").value.trim(), pairingCode: $("pairingCode").value, extensionName: $("extensionName").value.trim() });
  if (!response?.ok) $("message").textContent = response?.error || "配对失败";
  else $("message").textContent = response.data?.reused ? "已复用现有客户端身份并重新连接。" : "配对成功，配置已保存在本机扩展存储中。";
  await refresh();
});
$("connect").addEventListener("click", async () => { await persistForm(); const response = await send({ type: "popup.connect" }); if (!response?.ok) $("message").textContent = response?.error || "连接失败"; await refresh(); });
$("bind").addEventListener("click", async () => { const response = await send({ type: "popup.bind" }); if (!response?.ok) $("message").textContent = response?.error || "绑定失败"; else $("message").textContent = "绑定成功。后续会被动识别模型与推理强度，不需要打开模型菜单。"; await refresh(); });
$("discoverModels").addEventListener("click", async () => {
  $("message").textContent = "正在被动读取当前页面模型/推理状态，不会打开模型菜单…";
  const response = await send({ type: "popup.discoverModels" });
  if (!response?.ok) $("message").textContent = response?.error || "模型状态读取失败";
  else $("message").textContent = `状态刷新完成。当前模型：${response.data?.current_model || "尚未确认"}；推理强度：${REASONING_LABELS[response.data?.current_reasoning] || response.data?.current_reasoning || "尚未确认"}。`;
  await refresh();
});
$("unbind").addEventListener("click", async () => { await send({ type: "popup.unbind" }); await refresh(); });
$("versionInfo").textContent = `Chrome Bridge · v${EXTENSION_VERSION}`;
refresh().catch(error => { $("message").textContent = String(error); });
setInterval(() => refresh().catch(() => {}), 2000);
