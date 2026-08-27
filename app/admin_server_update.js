(() => {
  if (globalThis.__CHAT2API_SERVER_UPDATE_UI__) return;
  globalThis.__CHAT2API_SERVER_UPDATE_UI__ = {version: 2};

  const nav = document.querySelector(".nav");
  const content = document.querySelector(".content");
  if (!nav || !content) return;

  const button = document.createElement("button");
  button.dataset.view = "updates";
  button.textContent = "版本更新";
  nav.appendChild(button);

  const view = document.createElement("section");
  view.className = "view";
  view.id = "view-updates";
  view.innerHTML = `
    <div class="grid" style="grid-template-columns:repeat(4,minmax(150px,1fr))">
      <div class="card"><div class="muted">Server Runtime</div><div class="n" id="updRuntime">-</div></div>
      <div class="card"><div class="muted">Chrome Bundle</div><div class="n" id="updBundle">-</div></div>
      <div class="card"><div class="muted">当前提交</div><div class="n" id="updLocalCommit" style="font-size:18px">-</div></div>
      <div class="card"><div class="muted">GitHub main</div><div class="n" id="updRemoteCommit" style="font-size:18px">-</div></div>
    </div>

    <div class="panel" id="updSummaryPanel">
      <div style="display:flex;justify-content:space-between;gap:18px;align-items:flex-start;flex-wrap:wrap">
        <div style="min-width:280px;flex:1">
          <div class="muted">服务端更新状态</div>
          <h2 id="updHeadline" style="margin:6px 0">正在检查…</h2>
          <div id="updSummary" class="muted">正在读取本地部署信息与 GitHub main。</div>
        </div>
        <div class="toolbar" style="margin:0">
          <button class="action" id="updCheck">重新检查 GitHub</button>
          <button class="action good" id="updStart">从 GitHub 更新服务端</button>
        </div>
      </div>
      <div style="margin-top:12px;padding:10px 12px;border:1px solid var(--line);border-radius:9px;background:#07101e">
        <label style="display:flex;align-items:center;gap:9px;cursor:pointer">
          <input id="updUseBuildCache" type="checkbox" checked>
          <span><b>使用 Docker 构建缓存（推荐）</b> <span class="muted">复用现有 BuildKit/镜像层和 pip 缓存，通常可显著缩短更新；取消勾选才会执行 --no-cache 全量重建。</span></span>
        </label>
      </div>
      <div id="updRemoteMessage" class="codebox" style="margin-top:12px;display:none"></div>
    </div>

    <div class="panel" id="updInstallerPanel" style="margin-top:14px;display:none">
      <h3>首次启用：安装主机更新助手</h3>
      <p class="muted">chat2api 运行在 Docker 容器内。为避免把 Docker Socket 或宿主机 root 权限直接暴露给 Web 容器，自动更新采用 <b>systemd.path + 固定更新脚本</b>。只需要在宿主机执行一次下面的命令；之后即可一直在本页更新。</p>
      <div class="codebox"><code id="updInstallCommand">sudo bash /opt/chat2api/scripts/install_chat2api_server_updater.sh</code></div>
      <div class="toolbar" style="margin-top:10px"><button class="action" id="updCopyInstall">复制安装命令</button></div>
    </div>

    <div class="panel" id="updProgressPanel" style="margin-top:14px;display:none">
      <div style="display:flex;justify-content:space-between;gap:12px;align-items:center">
        <div><h3 style="margin:0">更新进度</h3><div class="muted" id="updProgressMessage">等待任务状态…</div></div>
        <span class="pill" id="updStatusPill">idle</span>
      </div>
      <div style="display:flex;align-items:center;gap:12px;margin:15px 0 8px">
        <div style="height:11px;flex:1;background:#07101e;border:1px solid var(--line);border-radius:999px;overflow:hidden"><div id="updProgressBar" style="height:100%;width:0%;background:var(--accent);transition:width .3s ease"></div></div>
        <strong id="updProgressPercent" style="min-width:48px;text-align:right">0%</strong>
      </div>
      <div class="grid" style="grid-template-columns:repeat(4,minmax(150px,1fr));margin-top:12px">
        <div class="card"><div class="muted">阶段</div><div id="updStage">-</div></div>
        <div class="card"><div class="muted">任务 ID</div><div><code id="updRequestId">-</code></div></div>
        <div class="card"><div class="muted">目标提交</div><div><code id="updTargetCommit">-</code></div></div>
        <div class="card"><div class="muted">回滚</div><div id="updRollback">-</div></div>
      </div>
      <h4>最近执行日志</h4>
      <pre id="updLogs" style="max-height:380px;overflow:auto;white-space:pre-wrap;word-break:break-word;background:#07101e;border:1px solid var(--line);border-radius:10px;padding:12px;font-size:12px;line-height:1.5">暂无日志</pre>
      <div id="updReconnect" class="warnText" style="display:none;margin-top:10px">服务正在重建/重启，控制台暂时无法连接；页面会自动重试。</div>
    </div>

    <div class="panel" style="margin-top:14px">
      <h3>安全边界</h3>
      <ul class="muted">
        <li>网页只能创建固定格式的更新请求，不能提交任意 Shell 命令。</li>
        <li>宿主机更新助手固定更新 <code>/opt/chat2api</code> 的 <code>origin/main</code>，并在更新前检查 tracked 工作区是否干净。</li>
        <li>先构建新 Docker 镜像，构建成功后才切换容器；健康检查失败时自动回滚到更新前提交并重新构建。</li>
        <li><code>.env</code> 与 <code>data/</code> 在更新过程中保留，更新状态与日志写入持久化 <code>data/</code>。</li>
        <li>GitHub main 优先通过 Git Smart HTTP 检查，不消耗 GitHub REST API 未认证配额；Git 拉取仍强制 HTTP/1.1 并带重试。</li>
      </ul>
    </div>`;
  content.appendChild(view);

  try { if (typeof titles !== "undefined") titles.updates = "版本更新"; } catch (_) {}

  const $ = id => document.getElementById(id);
  const stageText = {
    queued: "排队等待",
    starting: "初始化",
    preflight: "环境检查",
    backup: "备份配置",
    fetch: "拉取 GitHub main",
    checkout: "切换代码",
    build: "构建 Docker 镜像",
    deploy: "启动新容器",
    health: "健康检查",
    rollback: "自动回滚",
    completed: "完成",
  };

  let active = false;
  let polling = false;
  let lastState = "idle";

  async function callApi(path, opt = {}) {
    if (typeof api === "function") return api(path, opt);
    const response = await fetch(path, {
      method: opt.method || "GET",
      headers: opt.body === undefined ? {} : {"Content-Type": "application/json"},
      body: opt.body === undefined ? undefined : JSON.stringify(opt.body),
      credentials: "same-origin",
      cache: "no-store",
    });
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch (_) { data = {detail: text}; }
    if (!response.ok) throw new Error(data.detail || `${response.status} ${text}`);
    return data;
  }

  function setText(id, value) {
    const node = $(id);
    if (node) node.textContent = value;
  }

  function renderStatus(data) {
    if (!data) return;
    const state = String(data.status || "idle");
    const running = state === "queued" || state === "running";
    const hasTask = state !== "idle" || data.request_id || (data.logs || []).length;
    $("updProgressPanel").style.display = hasTask ? "" : "none";
    setText("updStatusPill", state);
    $("updStatusPill").className = "pill " + (state === "succeeded" ? "ok" : state === "failed" ? "bad" : running ? "warn" : "");
    const value = Math.max(0, Math.min(Number(data.percent || 0), 100));
    $("updProgressBar").style.width = value + "%";
    $("updProgressBar").style.background = state === "failed" ? "var(--bad)" : state === "succeeded" ? "var(--ok)" : "var(--accent)";
    setText("updProgressPercent", value + "%");
    setText("updProgressMessage", data.message || "等待任务状态…");
    setText("updStage", stageText[data.stage] || data.stage || "-");
    setText("updRequestId", data.request_id || "-");
    setText("updTargetCommit", (data.target_commit || "-").slice(0, 12));
    setText("updRollback", data.rollback_succeeded ? `已回滚 ${(data.rollback_commit || "").slice(0, 12)}` : data.rollback_commit ? "回滚失败/进行中" : "未触发");
    setText("updLogs", Array.isArray(data.logs) && data.logs.length ? data.logs.join("\n") : "暂无日志");
    const log = $("updLogs");
    if (log) log.scrollTop = log.scrollHeight;
    $("updStart").disabled = running || !data.updater_installed;
    $("updStart").textContent = running ? "服务端正在更新…" : "从 GitHub 更新服务端";
    $("updUseBuildCache").disabled = running;
    lastState = state;
  }

  function renderOverview(data) {
    setText("updRuntime", `v${data.server_runtime_version || "-"}`);
    setText("updBundle", `v${data.chrome_bundle_version || "-"}`);
    setText("updLocalCommit", data.deployed_short_commit || "未记录");
    setText("updRemoteCommit", data.remote?.short_sha || "获取失败");

    const installed = Boolean(data.updater?.installed);
    $("updInstallerPanel").style.display = installed ? "none" : "";
    setText("updInstallCommand", data.updater?.install_command || "sudo bash /opt/chat2api/scripts/install_chat2api_server_updater.sh");

    const remoteOk = Boolean(data.remote?.ok);
    if (!remoteOk) {
      setText("updHeadline", "GitHub main 检查失败");
      setText("updSummary", data.remote?.error || "暂时无法读取 GitHub main。可以稍后重新检查。");
    } else if (!data.deployed_commit) {
      setText("updHeadline", installed ? "已启用更新助手；尚未记录部署提交" : "需要先安装主机更新助手");
      setText("updSummary", installed
        ? "安装助手后首次刷新通常会写入当前提交；如果仍显示未记录，可重新执行一次安装命令。"
        : "由于服务运行在 Docker 容器里，需要一次性安装宿主机 systemd 更新助手。之后即可网页一键更新。"
      );
    } else if (data.update_available) {
      setText("updHeadline", "发现新的 GitHub main 提交");
      setText("updSummary", `当前 ${(data.deployed_commit || "").slice(0, 12)} → 最新 ${(data.remote.sha || "").slice(0, 12)}。点击更新后会自动拉取、构建、切换并健康检查。`);
    } else {
      setText("updHeadline", "当前服务端已经是 GitHub main 最新提交");
      setText("updSummary", `当前提交 ${(data.deployed_commit || "").slice(0, 12)} 与 GitHub main 一致。`);
    }

    const remoteMessage = $("updRemoteMessage");
    if (data.remote?.message) {
      remoteMessage.style.display = "";
      remoteMessage.textContent = data.remote.message;
    } else {
      remoteMessage.style.display = "none";
    }

    renderStatus(data.status || {});
  }

  async function loadOverview(refresh = false) {
    if (!active) return;
    try {
      const data = await callApi(`/api/admin/server-update${refresh ? "?refresh=1" : ""}`);
      renderOverview(data);
    } catch (error) {
      setText("updHeadline", "版本信息加载失败");
      setText("updSummary", String(error?.message || error));
    }
  }

  async function pollStatus() {
    if (polling) return;
    polling = true;
    while (active) {
      try {
        const data = await callApi("/api/admin/server-update/status");
        $("updReconnect").style.display = "none";
        const previousState = lastState;
        renderStatus(data);
        const terminalTransition = ["succeeded", "failed"].includes(String(data.status || "")) && previousState !== data.status;
        if (terminalTransition) await loadOverview(true);
        await new Promise(resolve => setTimeout(resolve, data.status === "queued" || data.status === "running" ? 1000 : 5000));
      } catch (_) {
        if (lastState === "queued" || lastState === "running") $("updReconnect").style.display = "";
        await new Promise(resolve => setTimeout(resolve, 1500));
      }
    }
    polling = false;
  }

  $("updCheck").onclick = () => loadOverview(true);
  $("updCopyInstall").onclick = async () => {
    const command = $("updInstallCommand").textContent || "";
    try { await navigator.clipboard.writeText(command); setText("updCopyInstall", "已复制"); }
    catch (_) { prompt("复制以下命令", command); }
    setTimeout(() => setText("updCopyInstall", "复制安装命令"), 1200);
  };
  $("updStart").onclick = async () => {
    const useBuildCache = Boolean($("updUseBuildCache")?.checked);
    const cacheText = useBuildCache ? "使用现有 Docker/BuildKit 缓存构建" : "执行 --no-cache 全量构建";
    if (!confirm(`确定从 GitHub main 自动更新 chat2api 服务端吗？\n\n本次将${cacheText}。更新过程中控制台会短暂断线；失败会尝试自动回滚。`)) return;
    try {
      await callApi("/api/admin/server-update/start", {method: "POST", body: {confirm: true, use_build_cache: useBuildCache}});
      active = true;
      await loadOverview(false);
      pollStatus();
    } catch (error) {
      alert(`启动更新失败：${error.message}`);
    }
  };

  button.addEventListener("click", event => {
    event.preventDefault();
    if (typeof show === "function") show("updates");
    else location.hash = "#updates";
  });

  if (typeof show === "function" && !show.__chat2apiServerUpdateWrapped) {
    const baseShow = show;
    const wrapped = async function(viewName) {
      const result = await baseShow(viewName);
      active = viewName === "updates";
      if (active) {
        await loadOverview(false);
        pollStatus();
      }
      return result;
    };
    wrapped.__chat2apiServerUpdateWrapped = true;
    show = wrapped;
  }
})();
