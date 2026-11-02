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
  <div class="panel">
    <h2>Worker 工具</h2>
    <p class="muted">集中管理只读诊断探针：基础图片/DOM/网络/模型控件，以及 ABCD 四组场景（A 富文本、B 多图片、C 多文件、D 多轮上下文）和回复边界完整性。启用后仅对新请求采样；探针不修改请求终态，不保存对话原文、私有下载链接或图片字节。</p>
    <div class="toolbar">
      <label for="workerToolsScope">策略范围</label>
      <select id="workerToolsScope"><option value="global">全局默认</option></select>
      <button class="action" id="workerToolsReload">刷新配置</button>
      <button class="action" id="workerToolsEnableAbcd">勾选 ABCD 全部证据探针</button>
      <button class="action good" id="workerToolsSave">保存策略</button>
      <span class="muted" id="workerToolsRevision"></span>
    </div>
    <p id="workerToolsStatus" class="muted">等待管理员连接</p>
  </div>
  <div class="panel" style="margin-top:12px">
    <h3>工具目录</h3><div class="scroll"><table><thead>
      <tr><th>工具</th><th>工具 ID</th><th>版本</th><th>阶段</th><th>状态</th></tr>
    </thead><tbody id="workerToolsCatalog"></tbody></table></div>
  </div>
  <div class="panel" style="margin-top:12px">
    <h3>Worker 有效配置</h3>
    <p class="muted">单 Worker 可继承全局策略或显式开启/关闭。勾选全部证据探针只会修改当前页面的选项，必须点击“保存策略”才生效。建议选择 TX03 的目标 Worker 而不是全局。配置仅影响新请求；单个请求最多 32 条探针事件，事件数达到上限时依然保留终态前证据。</p>
    <div class="scroll"><table><thead>
      <tr><th>工具</th><th>阶段</th><th>配置</th></tr>
    </thead><tbody id="workerToolsPolicyRows"></tbody></table></div>
  </div>
  <div class="panel" style="margin-top:12px">
    <h3>请求探针证据与诊断包</h3>
    <div class="toolbar"><input id="workerToolsRequestId" placeholder="输入请求 ID，如 req_...">
      <button class="action" id="workerToolsInspect">查看证据</button>
      <button class="action" id="workerToolsDownload">下载证据 ZIP</button>
      <button class="action" id="workerToolsRefreshRuns">刷新运行记录</button>
    </div>
    <pre id="workerToolsEvidence" class="codebox" style="max-height:310px;overflow:auto">选择请求查看证据时间线，默认不保存提示词正文和完整图片 URL。</pre>
    <div class="scroll"><table><thead><tr><th>请求 ID</th><th>采集状态</th><th>事件数</th><th>最后阶段</th><th>操作</th></tr></thead>
      <tbody id="workerToolsRuns"></tbody></table></div>
  </div>
  <div class="panel" style="margin-top:12px" id="nativePageToolPanel">
    <h3>原生页面诊断 · 独立 Worker 工具</h3>
    <p class="muted">仅用于已经生成的回复，不重新发送请求。优先根据请求 ID 匹配仍打开的原生页面；如果旧窗口已关闭，可补填该历史聊天的 ChatGPT /c/ 地址，工具将在新标签页打开后采集。不会读取 Cookie、用户问题、浏览器存储或原始下载 URL。</p>
    <div class="toolbar">
      <label for="nativePageWorker">执行 Worker</label>
      <select id="nativePageWorker"><option value="">选择 Worker</option></select>
      <label for="nativePageRequestId">原请求 ID</label>
      <input id="nativePageRequestId" placeholder="req_..." style="min-width:230px">
    </div>
    <div class="toolbar">
      <input id="nativePageConversationUrl" placeholder="可选：已关闭页面的 https://chatgpt.com/c/... 地址" style="min-width:380px;flex:1">
      <button class="action good" id="nativePageStart">开始诊断</button>
      <button class="action" id="nativePageVersion">检查工具版本</button>
      <button class="action" id="nativePageUpdate">更新此 Worker 诊断工具</button>
      <button class="action" id="nativePageReload">刷新记录</button>
    </div>
    <p class="muted" id="nativePageStatus">首次安装随 Worker Bundle 部署；后续仅更新该独立工具，无需重新安装全部 Worker。首次使用旧 Worker 前需要先升级一次 Worker 控制器。</p>
    <div class="scroll"><table><thead><tr>
      <th>诊断任务</th><th>原请求 ID</th><th>Worker</th><th>工具版本</th><th>状态/阶段</th><th>进度</th><th>操作</th>
    </tr></thead><tbody id="nativePageRecords"></tbody></table></div>
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
