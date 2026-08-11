(() => {
  const download = document.getElementById("downloadRuntimeLog");
  const clear = document.getElementById("clearRuntimeLog");
  const message = document.getElementById("message");
  if (!download || !clear) return;

  function beijingParts(value = Date.now()) {
    const d = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(d.getTime())) return null;
    return new Date(d.getTime() + 8 * 60 * 60 * 1000);
  }

  function stamp() {
    const d = beijingParts();
    const p = value => String(value).padStart(2, "0");
    return `${d.getUTCFullYear()}${p(d.getUTCMonth() + 1)}${p(d.getUTCDate())}-${p(d.getUTCHours())}${p(d.getUTCMinutes())}${p(d.getUTCSeconds())}`;
  }

  function beijingIso(value) {
    if (value == null || value === "") return null;
    const d = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(d.getTime())) return null;
    const shifted = new Date(d.getTime() + 8 * 60 * 60 * 1000);
    const p = (number, width = 2) => String(number).padStart(width, "0");
    return `${shifted.getUTCFullYear()}-${p(shifted.getUTCMonth() + 1)}-${p(shifted.getUTCDate())}` +
      `T${p(shifted.getUTCHours())}:${p(shifted.getUTCMinutes())}:${p(shifted.getUTCSeconds())}.${p(shifted.getUTCMilliseconds(), 3)}+08:00`;
  }

  function normalizeBeijingTimes(data) {
    if (!data || typeof data !== "object") return data;
    if (data.generated_at) data.generated_at = beijingIso(data.generated_at) || data.generated_at;
    data.timezone = "Asia/Shanghai";
    data.utc_offset_minutes = 480;
    data.time_display = {
      canonical_timezone: "Asia/Shanghai",
      utc_offset_minutes: 480,
      note: "All human-readable chat2api timestamps use Beijing time (+08:00). Epoch millisecond deadlines and monotonic duration fields are timezone-independent.",
    };

    for (const run of Array.isArray(data.runs) ? data.runs : []) {
      if (run.started_at) run.started_at = beijingIso(run.started_at) || run.started_at;
      if (run.ended_at) run.ended_at = beijingIso(run.ended_at) || run.ended_at;
      if (run.idle_deadline) run.idle_deadline_beijing = beijingIso(run.idle_deadline);
      for (const part of Array.isArray(run.parts) ? run.parts : []) {
        if (part.saved_at) part.saved_at = beijingIso(part.saved_at) || part.saved_at;
      }
    }
    for (const chunk of Array.isArray(data.chunks) ? data.chunks : []) {
      if (chunk.saved_at) chunk.saved_at = beijingIso(chunk.saved_at) || chunk.saved_at;
    }
    for (const entry of Array.isArray(data.entries) ? data.entries : []) {
      if (entry.at) entry.at = beijingIso(entry.at) || entry.at;
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
    const data = normalizeBeijingTimes(response.data || {});
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `chat2api-extension-runtime-${stamp()}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message.textContent = `运行日志已导出，共 ${Array.isArray(data.entries) ? data.entries.length : 0} 条；时间统一为北京时间。`;
  });

  clear.addEventListener("click", async () => {
    const response = await chrome.runtime.sendMessage({ type: "popup.logs.clear" });
    message.textContent = response?.ok ? "运行日志已清空。" : (response?.error || "清空运行日志失败");
  });
})();
