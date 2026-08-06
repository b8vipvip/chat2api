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

async function refresh() {
  const response = await send({ type: "popup.status" });
  if (!response?.ok) return;
  const { settings, tabs } = response;

  // Only initialize editable fields once. Periodic status refreshes must not
  // overwrite text while the user is typing or pasting configuration values.
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
  $("binding").textContent = bound ? `已绑定：${bound.title || bound.url}` : `未绑定；当前检测到 ${tabs.length} 个 ChatGPT 标签页`;
  if (settings.socketError) $("message").textContent = settings.socketError;
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
  else $("message").textContent = "配对成功，配置已保存在本机扩展存储中。";
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
  await refresh();
});

$("unbind").addEventListener("click", async () => {
  await send({ type: "popup.unbind" });
  await refresh();
});

refresh().catch(error => { $("message").textContent = String(error); });
setInterval(() => refresh().catch(() => {}), 2000);
