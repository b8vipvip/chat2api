(() => {
  const VERSION = "0.21.2";
  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = `Server Console · v${VERSION}`;

  function removeLegacyPanel() {
    document.querySelectorAll("[data-concurrency-settings-v211]").forEach(node => node.remove());
  }

  function patchDocs() {
    const block = document.querySelector("[data-concurrency-live-text-v21]");
    if (!block || block.dataset.unifiedConcurrencyV211 === "1") return;
    const heading = [...block.querySelectorAll("h3")]
      .find(node => String(node.textContent || "").includes("单扩展并发与容量"));
    if (!heading) return;

    let node = heading.nextElementSibling;
    while (node && node.tagName !== "H3") {
      const next = node.nextElementSibling;
      node.remove();
      node = next;
    }

    const section = document.createElement("div");
    section.dataset.unifiedConcurrencyDocV211 = "1";
    section.innerHTML = `
      <p>并发策略采用<strong>按扩展 ID 独立计数</strong>。文本、Vision、文件理解、图片生成、普通语音和 GPT Live 每个任务都占 1 个并发名额；新扩展默认上限为 <code>3</code>。</p>
      <p>每个 Extension ID 可以使用不同的并发上限。请在控制台“扩展管理”的“API 调用 / 并发上限”列直接修改；配置会持久化，并随请求以 <code>worker_limit</code> 下发到对应 Chrome Bridge。</p>
      <table>
        <thead><tr><th>请求类型</th><th>并发计数</th><th>规则</th></tr></thead>
        <tbody>
          <tr><td>文本 / Vision / 文件理解</td><td><code>1</code></td><td>受该扩展 ID 自己的上限限制</td></tr>
          <tr><td>图片生成</td><td><code>1</code></td><td>受该扩展 ID 自己的上限限制</td></tr>
          <tr><td>普通语音</td><td><code>1</code></td><td>受该扩展 ID 自己的上限限制</td></tr>
          <tr><td>GPT Live</td><td><code>1</code></td><td>受该扩展 ID 自己的上限限制</td></tr>
        </tbody>
      </table>
      <p>降低某个扩展的上限不会中断已经执行的请求；已有请求完成前，新请求会等待容量。提高上限后，等待中的请求会立即重新竞争空位。</p>
      <p><strong>备用窗口数量不等于并发上限。</strong>备用窗口是浏览器当前已经打开/预热的会话窗口；新的并发 Worker 可以在需要时按需创建。</p>
    `;
    heading.insertAdjacentElement("afterend", section);
    block.dataset.unifiedConcurrencyV211 = "1";
  }

  function boot() {
    removeLegacyPanel();
    patchDocs();
    document.querySelectorAll(".nav button[data-view]").forEach(button => {
      button.addEventListener("click", () => {
        setTimeout(() => {
          removeLegacyPanel();
          if (String(button.dataset.view || "") === "docs") patchDocs();
        }, 0);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
