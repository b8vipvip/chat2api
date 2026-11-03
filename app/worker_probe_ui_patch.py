"""Install Worker tools navigation after the canonical request-history owner.

No second request-table renderer or request lifecycle interceptors are installed.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI

from . import admin as admin_module

PANEL = r'''
<section class="view" id="view-worker-tools">
<style>
#view-worker-tools .native-tool-bar {display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:8px 0}
#view-worker-tools .native-tool-bar input,#view-worker-tools .native-tool-bar select {min-width:0}
#nativePageWorker {width:360px;max-width:100%}
#nativePageRequestId {width:320px;max-width:100%;font-family:monospace}
#nativePageConversationUrl {flex:1 1 360px}
#view-worker-tools .native-tool-details {margin-top:10px}
#view-worker-tools .native-tool-details > summary {cursor:pointer;font-weight:600;padding:8px 0}
#view-worker-tools .native-tool-table {max-height:420px;overflow:auto}
#view-worker-tools .native-tool-table td {vertical-align:middle}
#view-worker-tools .native-id {font-family:monospace;font-size:12px}
#nativePageRecords progress {width:110px;max-width:100%}
#nativePageStatus {margin:6px 0}
</style>
<div class="panel" id="nativePageToolPanel">
  <h2 style="margin:0 0 4px">原生页面诊断 <span class="muted" style="font-size:12px;font-weight:normal">独立 Worker 工具</span></h2>
  <div class="native-tool-bar">
    <label for="nativePageWorker">Worker</label>
    <select id="nativePageWorker"><option value="">选择 Worker</option></select>
    <label for="nativePageRequestId">请求 ID / 历史链接</label>
    <input id="nativePageRequestId" style="flex:1 1 390px;min-width:240px" placeholder="粘贴完整 req_... 或 https://chatgpt.com/c/...">
    <button class="action good" id="nativePageStart">开始诊断</button>
  </div>
  <div class="native-tool-bar">
    <div class="native-tool-release-actions" style="display:flex;align-items:center;gap:8px;flex-wrap:nowrap">
      <button class="action" id="nativePageUpdate">更新诊断工具</button>
      <button class="action" id="nativePageVersion">检查版本</button>
    </div>
    <button class="action" id="nativePageReload">刷新记录</button>
    <span class="muted">工具独立发布：首次升级更新机制后，后续直接点击「更新诊断工具」，不再部署服务端。</span>
  </div>
  <p id="nativePageStatus" class="muted" role="status">输入完整请求 ID 或原生 ChatGPT /c/ 历史链接后即可执行；不发起新生成请求。</p>
  <div id="nativePageDownloads" aria-live="polite" aria-label="诊断文件下载进度与保存链接"></div>
  <div class="scroll native-tool-table"><table><thead><tr>
    <th>诊断任务</th><th>请求 ID / 历史链接</th><th>Worker ID</th><th>工具版本</th><th>状态/结果</th><th>进度</th><th>操作</th>
  </tr></thead><tbody id="nativePageRecords"></tbody></table></div>
</div>
<div class="panel" style="margin-top:10px">
  <details class="native-tool-details" id="workerProbePolicyDetails">
    <summary>基础探针工具与 Worker 有效配置（点击展开）</summary>
    <div class="native-tool-bar">
      <label for="workerToolsScope">策略范围</label>
      <select id="workerToolsScope"><option value="global">全局默认</option></select>
      <button class="action" id="workerToolsReload">刷新配置</button>
      <button class="action" id="workerToolsEnableAbcd">勾选 ABCD 探针</button>
      <button class="action good" id="workerToolsSave">保存策略</button>
      <span class="muted" id="workerToolsRevision"></span>
    </div>
    <p id="workerToolsStatus" class="muted">工具默认关闭；策略仅影响新请求。单 Worker 可继承全局配置或显式开启/关闭。</p>
    <div class="scroll native-tool-table"><table><thead><tr>
      <th>工具</th><th>工具 ID</th><th>版本</th><th>阶段</th><th>可用状态</th><th>配置</th>
    </tr></thead><tbody id="workerToolsPolicyRows"></tbody></table></div>
  </details>
  <details class="native-tool-details" id="workerProbeHistoryDetails">
    <summary>原有请求探针证据和运行记录（点击展开）</summary>
    <div class="native-tool-bar">
      <input id="workerToolsRequestId" placeholder="完整请求 ID，如 req_...">
      <button class="action" id="workerToolsInspect">查看探针证据</button>
      <button class="action" id="workerToolsDownload">下载证据 ZIP</button>
      <button class="action" id="workerToolsRefreshRuns">刷新记录</button>
    </div>
    <pre id="workerToolsEvidence" class="codebox" style="max-height:180px;overflow:auto">选择请求查看原有探针证据。</pre>
    <div class="scroll native-tool-table"><table><thead><tr>
      <th>请求 ID</th><th>采集状态</th><th>事件数</th><th>最后阶段</th><th>操作</th>
    </tr></thead><tbody id="workerToolsRuns"></tbody></table></div>
  </details>
</div>
</section>
'''


def install_worker_probe_ui_v1(app: FastAPI) -> FastAPI:
    if getattr(app.state, "worker_probe_ui_v1_installed", False):
        return app
    html = admin_module.ADMIN_HTML
    if 'id="view-worker-tools"' in html:
        app.state.worker_probe_ui_v1_installed = True
        return app
    if "</div></aside>" not in html or "</div></main>" not in html:
        raise RuntimeError("Worker probe UI: main console mount points not found")
    html = html.replace("</div></aside>",
        '<button data-view="worker-tools">Worker 工具</button></div></aside>', 1)
    html = html.replace("</div></main>", PANEL + "</div></main>", 1)
    # Keep the canonical request conversation as the only conversation owner;
    # append a bounded probe count asynchronously without a second table loader.
    probe_anchor = "    const refs=Array.isArray(row?.response_media)?row.response_media.slice(0,4):[];"
    if probe_anchor in html:
        html = html.replace(probe_anchor, probe_anchor + """
    fetch('/api/admin/requests/' + encodeURIComponent(requestId) + '/probes', {
      headers: {Authorization: 'Bearer ' + key()}, cache: 'no-store',
    }).then(response => response.ok ? response.json() : null)
      .then(report => {
        if(report && requestHistoryMediaSequence === sequence){
          const count = Number(report.summary?.event_count || 0);
          meta.textContent += ' · 日志内含探针证据 ' + count + ' 条';

        }
      }).catch(() => {});
""", 1)

    html, n = re.subn(r"const titles=\{([^;]+)\};", 
                      lambda match: "const titles={" + match.group(1) + ",'worker-tools':'Worker 工具'};",
                      html, count=1)
    if n != 1:
        raise RuntimeError("Worker probe UI: console navigation title owner missing")
    ui_path = Path(__file__).with_name("admin_worker_tools_v1.js")
    client = ui_path.read_text(encoding="utf-8")
    if "</script>" in client.lower():
        raise RuntimeError("Unsafe inline script terminator")
    html = html.replace("</body>", "<script>\n" + client + "\n</script>\n</body>", 1)
    admin_module.ADMIN_HTML = html
    app.state.worker_probe_ui_v1_installed = True
    return app
