const $ = id => document.getElementById(id);
const DEFAULT_SERVER_URL = "https://chat2api.mv3.cn";
let formInitialized = false;

async function send(message) {
  return chrome.runtime.sendMessage(message);
}

async function persistForm() {
  await chrome.storage.local.set({
    serverUrl: $("serverUrl").value.trim(),
    pairingCode: $("pairingCode").value,
    extensionName: $("extensionName").value.trim(),
  });
}

function renderModels(settings) {
  const models = Array.isArray(settings.models) ? settings.models : [];
  const current = settings.currentModel || "chatgpt-web";
  if (!models.length) {
    $("models").textContent = "模型目录尚未读取。API 调用仍会把 model 下发到扩展并现场识别、选择；“刷新可用模型”只用于提前查看目录。";
    return;
  }
  const labels = models
    .filter(item => item?.id && item.id !== "chatgpt-web")
    .map(item => `${item.id === current || item.selected ? "✓ " : ""}${item.id}`);
  $("models").textContent = labels.length
    ? `已识别模型：${labels.join("、")}。API 请求会按 model 参数现场选择。`
    : "当前仅报告默认模型 chatgpt-web；指定 model 时扩展仍会现场尝试选择。";
}

async function refresh() {
  const response = await send({ type: "popup.status" });
  if (!response?.ok) return;
  const { settings, tabs } = response;

  if (!formInitialized) {
    $("serverUrl").value = settings.serverUrl || DEFAULT_SERVER_URL;
    $("pairingCode").value = settings.pairingCode || "";
    $("extensionName").value = settings.extensionName || "Chrome";
    formInitialized = true;
  }

  const status = $("status");
  status.textContent = `${settings.socketState || "disconnected"}${settings.clientId ? ` · ${settings.clientId}` : " · 未配对"}`;
  status.className = `status ${settings.socketState === "connected" ? "connected" : settings.socketState === "error" ? "error" : ""}`;
  const bound = tabs.find(tab => tab.id === settings.boundTabId);
  $("binding").textContent = bound
    ? `已绑定：${bound.title || bound.url}`
    : `未绑定；当前检测到 ${tabs.length} 个 ChatGPT 标签页。桌面唤醒或 API 请求会自动精确绑定/创建目标页。`;
  renderModels(settings);

  if (settings.socketError) $("message").textContent = settings.socketError;
  else if (settings.lastLaunchBindingError) $("message").textContent = `自动绑定重试中：${settings.lastLaunchBindingError}`;
  else if (settings.lastModelSelectionError) $("message").textContent = `上次模型选择失败：${settings.lastModelSelectionError}`;
}

for (const id of ["serverUrl", "pairingCode", "extensionName"]) {
  $(id).addEventListener("input", () => {
    persistForm().catch(error => {
      $("message").textContent = `保存配置失败：${String(error?.message || error)}`;
    });
  });
}

$("pair").addEventListener("click", async () => {
  $("message").textContent = "";
  await persistForm();
  const response = await send({
    type: "popup.pair",
    serverUrl: $("serverUrl").value.trim(),
    pairingCode: $("pairingCode").value,
    extensionName: $("extensionName").value.trim(),
  });
  if (!response?.ok) $("message").textContent = response?.error || "配对失败";
  else $("message").textContent = response.data?.reused
    ? "已复用现有客户端身份并重新连接。"
    : "配对成功，配置已保存在本机扩展存储中。";
  await refresh();
});

$("connect").addEventListener("click", async () => {
  await persistForm();
  const response = await send({ type: "popup.connect" });
  if (!response?.ok) $("message").textContent = response?.error || "连接失败";
  await refresh();
});

$("bind").addEventListener("click", async () => {
  const response = await send({ type: "popup.bind" });
  if (!response?.ok) $("message").textContent = response?.error || "绑定失败";
  else $("message").textContent = "绑定成功。模型会在刷新目录或 API 请求时自动识别。";
  await refresh();
});

$("discoverModels").addEventListener("click", async () => {
  $("message").textContent = "正在打开模型菜单并读取当前账号可用选项…";
  const response = await send({ type: "popup.discoverModels" });
  if (!response?.ok) $("message").textContent = response?.error || "模型读取失败";
  else $("message").textContent = `模型目录更新完成，共 ${response.data?.models?.length || 0} 项。API 调用不依赖手动刷新。`;
  await refresh();
});

$("unbind").addEventListener("click", async () => {
  await send({ type: "popup.unbind" });
  await refresh();
});

refresh().catch(error => { $("message").textContent = String(error); });
setInterval(() => refresh().catch(() => {}), 2000);
