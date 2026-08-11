(() => {
  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = "Server Console · v0.10";

  function pad(value) { return String(value).padStart(2, "0"); }

  function localFromNaiveUtc(text) {
    const raw = String(text || "").trim();
    if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(raw)) return null;
    const date = new Date(raw.replace(" ", "T") + "Z");
    if (Number.isNaN(date.getTime())) return null;
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  }

  function convertTableTimes() {
    for (const id of ["recentBody", "keysBody", "rqBody", "testHistory"]) {
      const body = document.getElementById(id);
      if (!body) continue;
      for (const row of body.querySelectorAll("tr")) {
        const cell = row.querySelector("td:first-child");
        if (!cell || cell.dataset.chat2apiLocalTime === "1") continue;
        const converted = localFromNaiveUtc(cell.textContent);
        if (!converted) continue;
        cell.textContent = converted;
        cell.dataset.chat2apiLocalTime = "1";
        cell.title = "浏览器本地时间（服务端原始记录使用 UTC ISO 时间）";
      }
    }
    document.querySelectorAll("#recentBody,#rqBody,#testHistory").forEach(body => {
      const table = body.closest("table");
      const first = table?.querySelector("thead th:first-child");
      if (first && first.textContent.trim() === "时间") first.textContent = "时间（本地）";
    });
  }

  let timer = null;
  const observer = new MutationObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(convertTableTimes, 50);
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  convertTableTimes();

  if (typeof oneTest === "function") {
    const priorOneTest = oneTest;
    oneTest = async function oneTestV10(kind, model, files, testToken) {
      const result = await priorOneTest(kind, model, files, testToken);
      if (result?.kind === "vision" && Array.isArray(result.subtests)) {
        const labels = result.subtests.map(item => `${item.kind === "video" ? "视频" : "图片"}：${item.status === "passed" ? "通过" : item.status === "warning" ? "警告" : "失败"}`);
        const successful = result.subtests.filter(item => item.status === "passed").length;
        const suffix = result.default_assets_generated ? " · 使用自动生成样本" : "";
        result.message = `${labels.join(" · ")} · ${successful}/${result.subtests.length} 成功${suffix}`;
      }
      return result;
    };
  }
})();
