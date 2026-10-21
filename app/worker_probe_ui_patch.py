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
    <p class="muted">集中管理只读诊断探针。P0 可执行图片交付与聊天 DOM 采集；P1 工具仅展示规划状态，不会误报执行成功。</p>
    <div class="toolbar">
      <label for="workerToolsScope">策略范围</label>
      <select id="workerToolsScope"><option value="global">全局默认</option></select>
      <button class="action" id="workerToolsReload">刷新配置</button>
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
    <p class="muted">单 Worker 可继承全局策略或显式开启/关闭。配置变更只对新请求生效；已运行的请求不受影响。</p>
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
