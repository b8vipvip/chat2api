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

  function localIso(value) {
    if (value == null || value === "") return null;
    const d = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(d.getTime())) return null;
    const p = (number, width = 2) => String(Math.abs(number)).padStart(width, "0");
    const offsetMinutes = -d.getTimezoneOffset();
    const sign = offsetMinutes >= 0 ? "+" : "-";
    const offsetHours = Math.floor(Math.abs(offsetMinutes) / 60);
    const offsetRemainder = Math.abs(offsetMinutes) % 60;
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}${sign}${p(offsetHours)}:${p(offsetRemainder)}`;
  }

  function addLocalTimes(data) {
    if (!data || typeof data !== "object") return data;
    data.generated_at_local = localIso(data.generated_at);
    data.time_display = {
      canonical_timezone: "UTC",
      local_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "browser-local",
      utc_offset_minutes: -new Date().getTimezoneOffset(),
      note: "Fields ending in Z are canonical UTC. *_local fields are browser-local time.",
    };

    for (const run of Array.isArray(data.runs) ? data.runs : []) {
      run.started_at_local = localIso(run.started_at);
      run.ended_at_local = localIso(run.ended_at);
      if (run.idle_deadline) run.idle_deadline_local = localIso(run.idle_deadline);
      for (const part of Array.isArray(run.parts) ? run.parts : []) {
        part.saved_at_local = localIso(part.saved_at);
      }
    }
    for (const chunk of Array.isArray(data.chunks) ? data.chunks : []) {
      chunk.saved_at_local = localIso(chunk.saved_at);
    }
    for (const entry of Array.isArray(data.entries) ? data.entries : []) {
      entry.at_local = localIso(entry.at);
    }
    return data;
  }

  download.addEventListener("click", async () => {
    message.textContent = "正在导出运行日志…";
    const response = await chrome.runtime.sendMessage({ type: "popup.logs.export" });
    if (!response?.ok) {
      message.textContent = response?.error || "运行日志导出失败";
      return;
    }
    const data = addLocalTimes(response.data || {});
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `chat2api-extension-runtime-${stamp()}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message.textContent = `运行日志已导出，共 ${Array.isArray(data.entries) ? data.entries.length : 0} 条；已附加浏览器本地时间。`;
  });

  clear.addEventListener("click", async () => {
    const response = await chrome.runtime.sendMessage({ type: "popup.logs.clear" });
    message.textContent = response?.ok ? "运行日志已清空。" : (response?.error || "清空运行日志失败");
  });
})();
