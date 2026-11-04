# Windows 原生页面诊断与统一 ext_ Worker ID · 候选实现

## 目的

后台 **Worker 工具 → 原生页面诊断** 同时选择 Linux 和 Windows Worker，用户可见身份统一采用 `ext_…`。Linux 的 `wrk_…` 仍是已有 Controller 鉴权/命令路由内部键，绝不因展示层修改而重命名、迁移、清空或取消绑定。

本功能独立于模型请求、流式输出、Broker 终态和额度扣费。历史 URL 自选诊断绝不冒充真实 API 请求归属。

## 两条采集路径

| 能力 | Linux | Windows |
|---|---|---|
| 公共 Worker ID | `ext_…` | `ext_…` |
| 执行器 | 原 Linux Controller + localhost CDP + 独立 Python 工具 | 已配对 Windows Chrome 扩展的后台只读采集器 |
| 检查版本 | 独立 `native-page-diagnostic-releases` 发布分支 | 扩展内置 `windows-dom-1.1.1` |
| 原生历史链接 | 支持 | 支持 |
| 请求 ID 且保存了安全历史链接 | 支持 | 支持 |
| 请求 ID 但没有历史链接 | Linux 原历史恢复机制 | 快速返回 422，并提示粘贴 /c/ 链接 |
| 消息结构、文本、表格、图片几何信息 | 支持 | 支持（限量 DOM 结构） |
| Chrome CDP 长页截图、原始图片字节/下载、像素比对 | Linux 原有能力 | **暂不支持；不能宣称 1:1 验证** |
| 诊断 ZIP | 原 Linux ZIP | `assistant_dom.json` + `manifest.json` + `README.txt` |
| 流式终态/业务请求状态 | 不改变 | 不改变 |

Windows 证据每份最多约 1.5 MiB 原始 JSON，单次采集限定节点、字符、深度；响应发送时按 32 KiB 分块，以 Worker 身份/Job ID 验证来源、分块顺序、完整长度和 SHA-256；后端只接受当前认证 WebSocket、匹配的正在运行 Job，收到的数据不转送聊天 Broker。ZIP 中 `completeness_verified=false`、`visual_verified=false`，即使 DOM 本身完整也不得当作原生视觉 1:1 通过。

Windows 采集器仅允许明确的 HTTPS ChatGPT /c/ URL，禁止 URL 查询串/凭证、其它主机和自定义路径；只在所选扩展对应 Chrome Profile 中创建**额外的后台标签页**，绝不复用/覆盖当前 API 请求标签页。通过 `__CHAT2API_NATIVE_PAGE_DIAG_TAB_IDS_V1__` 登记临时租约，标签页监督器不回收它；v132 持久窗口池、v90 观察器及 v88 兼容管理器不把它算入可用窗口或改变路由身份；结束后主动关闭并撤销租约。采集脚本不点击、提交或修改页面。保留 user 消息排除和 DIL ID 对齐校验。

## 追加安全修复 · 2026-11-04

- Windows 诊断选择浏览器会话时，只允许当前扩展显式绑定的 `boundTabId`、当前 Worker conversation router 记录的 `tab_id`，或持久窗口池已通过运行时验证的 `runtimeReadyTabs` 标签页；**不再把查询到的第一个 ChatGPT 标签页当作兜底**。旧版 v1.1.0 如无可证明的受管标签页会返回 `worker_managed_chatgpt_tab_not_found`；**v1.1.1 起已改为安全创建独立诊断窗口**，详见下方修复记录。
- 打开临时页面后，要求实际已完成加载的 HTTPS ChatGPT 页面与管理员指定的 `/c/` 路径一致；跳转到登录页、主页或其它对话返回 `diagnostic_conversation_redirected`，不得上传无关内容。
- 采集时记录 `probe.generation_active`（可见的生成停止控制或助手 busy 标记）。稳定哈希只有在边界有效且页面不处于生成中时才能提前结束轮询；持续生成或边界无法证明的采样必须保留 `complete=false`。
- 服务端分块接收器额外检查 `finished.size` 与原始分块声明一致，不能只依赖摘要匹配。
- 独立执行过扩展真实源码的模拟浏览器检查（5 个场景），以及 `capturePage()` 真实源码模拟 DOM 检查（4 个场景）；**V8 模拟不等于仓库 Node 脚本、Python pytest 或真实 Windows 浏览器验收**。Python/FastAPI 完整回归与 Windows TX03 实机仍待完成。

## 部署与验收顺序

1. 此分支**涉及中心服务端、Windows Chrome 扩展、标签页监管和管理后台**，不同于可独立热更新的 Linux 诊断 Python 脚本。首次部署必须按正式服务端/Worker 升级流程更新相应组件；只点击 Linux「更新诊断工具」无法获得 Windows 功能。
2. 在升级 Windows Worker 扩展并重新连线之后，中心服务端看到其 `windows_native_page_diagnostic_version=windows-dom-1.1.1`，才在原生诊断下拉框中允许选择；旧扩展仅展示“需更新 Windows 扩展”，不能发起无效任务。
3. Windows 选择 `ext_…` → 粘贴正确账号下的原生历史 URL → 开始诊断 → 获取 ZIP。确认 `manifest.worker_platform=windows`、`request_binding=url-only-unbound`、`visual_verified=false`、实际 HTML 消息 ID 和表格/图片计数。
4. 同时在 Windows Worker 发起真实 API 请求，检查 Windows 诊断标签页不会被错误回收、误占预约备用窗口、改变路由任务或异常消耗并发指标。再运行 Linux 历史诊断回归，验证 `ext_` 展示映射到原 `wrk_` Controller，Linux 更新版本功能仍可用。
5. 验证 Windows 旧版本检查、离线/禁用、macOS 不被伪装成 Windows、诊断并发限制、错误 ZIP、非法 URL/请求跨 Worker 归属、浏览器关闭中断和按版本区分提示。失败时不动 `main`、保留本功能独立分支。

## 验证状况

- Node/V8 模拟回归：`tests/native_page_windows_capture_dom.mjs`、`tests/native_page_windows_extension.mjs` 已执行通过。
- Python/FastAPI 回归**已编写但尚未执行**：`tests/test_native_page_windows_bridge.py`、`tests/test_native_page_windows_api.py`；包含 chunk 归属/完整性、ID 映射、Windows API/ZIP 和完整性否定证明。
- **尚未更新 TX03 Windows Worker，也没有在 Windows 实机完成 Chrome 扩展采集验收；不得声明正式可用。**
- 本分支不调整 `native-page-diagnostic-releases` 的独立 Linux 工具版本，也不调整主项目正式发布号。

## 回滚

回滚本功能代码只需恢复服务端/Windows 扩展至前一已确认版本；保留 Linux Worker 的 `wrk_` 注册/Controller 数据、Chrome profile、已存在真实请求和独立诊断 ZIP 原样不动。不要批量修改持久化 Worker ID 或清空客户端配对。

## 2026-11-04：Windows 可视化诊断与逐阶段进度（v1.1.0）

- 管理后台「原生页面诊断」支持勾选 **显示诊断标签页（Windows 可视化模式）**。默认不勾选仍采用 `active:false` 后台标签页；Linux Controller 不支持此选项。
- 勾选后仅在当前 Worker 拥有的 ChatGPT Chrome 窗口里新增 `active:true` 临时历史会话标签页，尝试将窗口前置；完成或异常时关闭临时标签页并尽力恢复之前的活动页。不允许借用任意其他用户窗口。
- 在活动路由请求或 Worker requestRoutes 未清空时，**拒绝**可视化模式并报告 `worker_busy_visual_diagnosis`，避免主动隐藏正在运行的 API 请求窗口。后台模式继续可用。该防护是执行时检查，不能替代高并发场景的实机冲突验证。
- Windows 扩展经已认证连接发送独立 `native.page.windows.progress` 消息（不属于 `chat.completed`、`request-v6` 终态）。服务端仅接受匹配的 Worker 和 Job，限定阶段、数值范围和最多 48 条事件；不会接受任意页面文本或 URL 进入进度证据。
- 管理后台在诊断记录中展示当前阶段、百分比及耗时，并提供「查看轨迹」，记录标签页 ID、窗口 ID 与各阶段相对耗时。ZIP 增加 `execution_trace.json`，管理员导出的 `diagnostic.json` 也包含 `trace`。
- 阶段：accepted → worker_lookup → tab_opening → tab_opened → tab_loading → page_ready → assistant_scanning → assistant_captured → uploading → upload_complete → tab_closed → 完成。异常同样记录 `failed` 和清理信息（如有标签页）。
- **范围限制不变**：显示历史页面≠抓取完整页面截图；不导出完整截图或图片源字节，也不自动宣称视觉 1:1 交付成功。更新本功能需要更新 Server 与 Windows Chrome Worker 扩展；Linux 独立工具仍通过独立发布分支更新。

## 2026-11-04 空闲 Windows Worker 自有窗口回退（v1.1.1 / chat2api v0.22.264）

- 根因：旧版仅检索现存的 `boundTabId` / conversation router / persistent ready pool 标签页。Windows Worker 空闲后窗口被回收，诊断在 `worker_lookup` 之前直接返回 `worker_managed_chatgpt_tab_not_found`，不会执行到打开页面阶段。
- 修复：若存在已验证的 Worker 所属 ChatGPT 窗口，就在该窗口中新建空白诊断标签页；如果一个也没有，扩展直接使用同一 Chrome Profile 的 `chrome.windows.create({url:"about:blank", ...})` 新建**完全独立**的诊断窗口。新建页面先拿到 tab ID、登记全局诊断 lease，再用 `chrome.tabs.update` 导航至历史 /c/ URL，以免窗口监督器提前把诊断页面收进业务池。
- 新增阶段：`window_creating`、`window_created`。日志和 `execution_trace.json` 会记录窗口 ID、标签页 ID、耗时以及最终清理；如果业务请求在诊断期间到来，不强行切回之前活动页面。默认 `show_tab=false` 时不主动聚焦；勾选“显示诊断标签页”时创建聚焦窗口，并且有活动请求时拒绝进入可视化模式。
- 故障显示：服务端现在分别公开当前扩展包 `extension_version`、诊断能力 `diagnostic_version`。后台下拉框不再把未上报能力一律写成“需更新”，并允许选择 Worker 查看实际版本，刷新记录会刷新 Worker 状态；真正不满足版本要求的情况仍由后端安全阻断。
- 该机制只对 Windows 扩展内置工具生效；Linux Controller 独立工具及业务 `request-v6` 完成终态均不变。Windows 扩展必须安装新包并重连后才能上报 `windows-dom-1.1.1`。这只是开窗与采集诊断修复，不等于富媒体视觉 1:1 已通过。
