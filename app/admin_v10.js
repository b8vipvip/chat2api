(() => {
  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = "Server Console · v0.10";

  function canonicalBeijingTime(text) {
    const raw = String(text || "").trim();
    // Since v0.14, naive table timestamps are already rendered from canonical
    // Asia/Shanghai values. Never append "Z" here: doing so treats Beijing
    // wall-clock time as UTC and adds another eight hours in +08 browsers.
    if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(raw)) return raw;
    if (!/^\d{4}-\d{2}-\d{2}T/.test(raw)) return null;
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return null;
    try {
      return new Intl.DateTimeFormat("sv-SE", {
        timeZone: "Asia/Shanghai",
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
        hour12: false,
      }).format(date);
    } catch (_) {
      return raw.replace("T", " ").replace(/(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$/, "");
    }
  }

  function convertTableTimes() {
    for (const id of ["recentBody", "keysBody", "rqBody", "testHistory"]) {
      const body = document.getElementById(id);
      if (!body) continue;
      for (const row of body.querySelectorAll("tr")) {
        const cell = row.querySelector("td:first-child");
        if (!cell || cell.dataset.chat2apiBeijingTime === "1") continue;
        const converted = canonicalBeijingTime(cell.textContent);
        if (!converted) continue;
        cell.textContent = converted;
        cell.dataset.chat2apiBeijingTime = "1";
        cell.title = "北京时间（Asia/Shanghai，UTC+08:00）";
      }
    }
    document.querySelectorAll("#recentBody,#rqBody,#testHistory").forEach(body => {
      const table = body.closest("table");
      const first = table?.querySelector("thead th:first-child");
      if (first && (first.textContent.trim() === "时间" || first.textContent.trim() === "时间（本地）")) first.textContent = "时间（北京时间）";
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
