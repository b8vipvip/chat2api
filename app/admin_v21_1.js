(() => {
  const VERSION = "0.21.2";
  const DEFAULT_LIMIT = 3;
  const MIN_LIMIT = 1;
  const MAX_LIMIT = 32;
  const brandSmall = document.querySelector(".brand small");
  if (brandSmall) brandSmall.textContent = `Server Console · v${VERSION}`;

  let booted = false;
  let loadInFlight = null;

  function ensurePanel() {
    const container = document.querySelector("#view-overview .panels");
    if (!container) return null;
    let panel = container.querySelector("[data-concurrency-settings-v211]");
    if (panel) return panel;
    panel = document.createElement("div");
    panel.className = "panel wide";
    panel.dataset.concurrencySettingsV211 = "1";
    panel.innerHTML = `
      <h3>并发设置</h3>
      <div class="muted" style="margin-bottom:10px">所有任务统一按 1 个并发计数；文本、Vision、文件、图片、普通语音和 GPT Live 共用同一个上限。默认最多 3 个，可即时修改。</div>
      <div class="toolbar" style="margin-bottom:4px">
        <label style="min-width:220px">单扩展最大并发
          <input id="maxConcurrencyV211" type="number" min="${MIN_LIMIT}" max="${MAX_LIMIT}" step="1" value="${DEFAULT_LIMIT}" style="width:120px;margin-left:8px">
        </label>
        <button class="action good" id="saveConcurrencyV211">保存配置</button>
        <span id="concurrencyStateV211" class="muted">读取中...</span>
      </div>
      <div class="muted" style="font-size:12px">降低上限不会中断正在执行的请求；已有请求完成前，新请求会等待容量。提高上限后等待中的请求会立即重新竞争空位。</div>
    `;
    container.appendChild(panel);
    panel.querySelector("#saveConcurrencyV211")?.addEventListener("click", saveConfig);
    return panel;
  }

  function stateNode() {
    return document.getElementById("concurrencyStateV211");
  }

  function inputNode() {
    return document.getElementById("maxConcurrencyV211");
  }

  function setState(text, className = "muted") {
    const node = stateNode();
    if (!node) return;
    if (node.textContent !== text) node.textContent = text;
    if (node.className !== className) node.className = className;
  }

  async function loadConfig() {
    ensurePanel();
    if (loadInFlight) return loadInFlight;
    loadInFlight = (async () => {
      try {
        const response = await fetch("/api/admin/concurrency", { cache: "no-store", credentials: "same-origin" });
        if (!response.ok) {
          if (response.status === 401) setState("登录控制台后可读取配置", "muted");
          else setState(`读取失败 HTTP ${response.status}`, "bad");
          return;
        }
        const payload = await response.json();
        const value = Number(payload.max_concurrency || DEFAULT_LIMIT);
        const input = inputNode();
        if (input && input.value !== String(value)) input.value = String(value);
        setState(`当前上限：${value}`, "ok");
      } catch (error) {
        setState(`读取失败：${String(error?.message || error)}`, "bad");
      } finally {
        loadInFlight = null;
      }
    })();
    return loadInFlight;
  }

  async function saveConfig() {
    const input = inputNode();
    const value = Number(input?.value || 0);
    if (!Number.isInteger(value) || value < MIN_LIMIT || value > MAX_LIMIT) {
      setState(`请输入 ${MIN_LIMIT}-${MAX_LIMIT} 的整数`, "warnText");
      return;
    }
    setState("保存中...", "muted");
    try {
      const response = await fetch("/api/admin/concurrency", {
        method: "PUT",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ max_concurrency: value }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        setState(payload.detail ? `保存失败：${payload.detail}` : `保存失败 HTTP ${response.status}`, "bad");
        return;
      }
      const applied = Number(payload.max_concurrency || value);
      if (input && input.value !== String(applied)) input.value = String(applied);
      setState(`已保存并生效：${applied}`, "ok");
    } catch (error) {
      setState(`保存失败：${String(error?.message || error)}`, "bad");
    }
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
      <p>从 v0.21.1 起，并发策略改为<strong>统一请求计数</strong>。文本、Vision、文件理解、图片生成、普通语音和 GPT Live 每个任务都只占 1 个并发名额；单扩展默认最大并发为 <code>3</code>，可在控制台“并发设置”中修改，配置会持久化并立即用于后续请求。</p>
      <table>
        <thead><tr><th>请求类型</th><th>并发计数</th><th>规则</th></tr></thead>
        <tbody>
          <tr><td>文本 / Vision / 文件理解</td><td><code>1</code></td><td>受统一最大并发限制</td></tr>
          <tr><td>图片生成</td><td><code>1</code></td><td>受统一最大并发限制</td></tr>
          <tr><td>普通语音</td><td><code>1</code></td><td>受统一最大并发限制</td></tr>
          <tr><td>GPT Live</td><td><code>1</code></td><td>受统一最大并发限制</td></tr>
        </tbody>
      </table>
      <p>例如上限为 <code>3</code> 时，可以同时运行 3 个图片任务、3 个 GPT Live Session，或者任意混合的 3 个任务。达到上限后请求最多等待约 1.5 秒，仍无空位时返回 <code>429 extension_capacity_exhausted</code>。</p>
      <p>服务端会把当前 <code>worker_limit</code> 随任务下发给 Chrome Bridge，因此扩展的同 API Key Worker 数会跟随控制台设置，而不是固定写死为 3。</p>
    `;
    heading.insertAdjacentElement("afterend", section);
    block.dataset.unifiedConcurrencyV211 = "1";
  }

  function patchVisibleView(view) {
    if (view === "overview") {
      ensurePanel();
      loadConfig();
    } else if (view === "docs") {
      patchDocs();
    }
  }

  function boot() {
    if (booted) return;
    booted = true;
    ensurePanel();
    patchDocs();
    loadConfig();

    document.getElementById("connect")?.addEventListener("click", () => {
      setTimeout(() => { ensurePanel(); loadConfig(); }, 350);
    });
    document.getElementById("refresh")?.addEventListener("click", () => {
      setTimeout(() => { ensurePanel(); loadConfig(); }, 200);
    });
    document.querySelectorAll(".nav button[data-view]").forEach(button => {
      button.addEventListener("click", () => {
        const view = String(button.dataset.view || "");
        setTimeout(() => patchVisibleView(view), 0);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
