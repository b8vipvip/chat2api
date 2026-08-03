const $ = id => document.getElementById(id);

async function send(message) {
  return chrome.runtime.sendMessage(message);
}

async function refresh() {
  const response = await send({ type: "popup.status" });
  if (!response?.ok) return;
  const { settings, tabs } = response;
  $("serverUrl").value = settings.serverUrl || "";
  $("extensionName").value = settings.extensionName || "Chrome";
  const status = $("status");
  status.textContent = `${settings.socketState || "disconnected"}${settings.clientId ? ` · ${settings.clientId}` : " · 未配对"}`;
  status.className = `status ${settings.socketState === "connected" ? "connected" : settings.socketState === "error" ? "error" : ""}`;
  const bound = tabs.find(tab => tab.id === settings.boundTabId);
  $("binding").textContent = bound ? `已绑定：${bound.title || bound.url}` : `未绑定；当前检测到 ${tabs.length} 个 ChatGPT 标签页`;
  if (settings.socketError) $("message").textContent = settings.socketError;
}

$("pair").addEventListener("click", async () => {
  $("message").textContent = "";
  const response = await send({
    type: "popup.pair",
    serverUrl: $("serverUrl").value.trim(),
    pairingCode: $("pairingCode").value,
    extensionName: $("extensionName").value.trim(),
  });
  if (!response?.ok) $("message").textContent = response?.error || "配对失败";
  else $("pairingCode").value = "";
  await refresh();
});

$("connect").addEventListener("click", async () => {
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
