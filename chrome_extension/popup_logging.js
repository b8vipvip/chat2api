(() => {
  const download = document.getElementById("downloadRuntimeLog");
  const clear = document.getElementById("clearRuntimeLog");
  const message = document.getElementById("message");
  if (!download || !clear) return;

  function stamp() {
    const d = new Date();
    const p = value => String(value).padStart(2, "0");
    return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  }

  download.addEventListener("click", async () => {
    message.textContent = "正在导出运行日志…";
    const response = await chrome.runtime.sendMessage({ type: "popup.logs.export" });
    if (!response?.ok) {
      message.textContent = response?.error || "运行日志导出失败";
      return;
    }
    const data = response.data || {};
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `chat2api-extension-runtime-${stamp()}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message.textContent = `运行日志已导出，共 ${Array.isArray(data.entries) ? data.entries.length : 0} 条。`;
  });

  clear.addEventListener("click", async () => {
    const response = await chrome.runtime.sendMessage({ type: "popup.logs.clear" });
    message.textContent = response?.ok ? "运行日志已清空。" : (response?.error || "清空运行日志失败");
  });
})();
