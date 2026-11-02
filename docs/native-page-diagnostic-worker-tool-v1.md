# Worker 工具 · 原生页面诊断 v1.1.0

## 目标

不再重复提交一条复杂生成请求。由中心服务器后台按 **原请求 ID** 创建独立诊断任务，在原来负责该请求的 Linux Worker 上采集 **已生成** ChatGPT 回复的 DOM/CSS/原生截图；不接管模型请求状态，不调用生成 API。

## 初次部署

1. 服务端先更新到包含本工具的提交，确认生产镜像成功打包 `native_page_diagnostic_v1.py` 与 `native_page_tool_control.py` 到 Linux Worker Bundle。
2. 到 Linux Worker 面板执行一次原有「更新 Worker」（或首次安装）。Worker Controller 将获得五个明确白名单命令，浏览器 CDP 调试端口继续只监听 localhost，不重设用户登录状态。
3. 后续仅修订独立 Python 工具：把工具新版本交付给中心服务器的 `/bootstrap/native-page-diagnostic.py` 与对应 SHA256 清单。在后台 **Worker 工具 → 原生页面诊断 → 更新此 Worker 诊断工具** 进行 Worker 端原子安装，不需要重装 Worker、不重启 Chrome，也不改变已有 API 请求。
4. 页面「检查工具版本」返回 Worker 上真正运行的独立脚本版本及 SHA-256，不能以服务端镜像版本替代。

## 操作

1. 输入现有完整请求 ID（`req_...`），前端读取原请求记录自动选择真实 `linux_worker_id`。服务端验证所选 Worker 与请求归属一致。
2. **已有页面仍打开**：不填写 URL，工具仅在本机 Chrome 标签页中查找请求期内的可信标识；找不到即明确失败，绝不模糊匹配相似回复。
3. **原请求窗口已自动关闭**：打开 Worker 的 ChatGPT 历史页面获得 `https://chatgpt.com/c/...` 链接，填入后执行。工具仅能打开该路径的 ChatGPT 历史会话，不发送消息、不输入提示词；抓取最后一个可信助手回复范围。
4. 点击「开始诊断」，查看诊断记录表里的运行阶段及进度。完成后「导出诊断 ZIP」与「导出日志」。失败也可直接「导出日志」。
5. 打开 ZIP 中的 `manifest.json`：比对 `version`、`request_id`、`worker_id`、`request_binding`、节点/表格/图片数量、截断状态。只有 `request_binding=live-request-marker` 表示精确关联；`manual-conversation-url-unverified-turn` 是管理员选择的同一会话 URL，不能证明它对应特定某轮回答。

## 取证文件

- `assistant_dom.json`：仅助手回复范围的节点层次、文本、计算 CSS、元素位置、图片尺寸/加载状态，无图片 URL、Cookie/账号凭据、用户提问或原始源 HTML。
- `screenshots/assistant_XX.png`：针对助手回复区域截取的若干段真实截图，最多 24 段，不控制浏览器滚动。
- `diagnostic.log`：独立工具的版本化进度、状态、错误阶段。
- `manifest.json`：工具版本、原请求 ID、Worker ID、证据可信度与截断标记。

服务端任务记录使用 `data/native_page_diagnostics`，按 job id 单独保存并限制归属。Worker 与服务端通过现有认证 WebSocket 分块传输；服务端验证大小/每片序号/全包 SHA256/ZIP 清单，请求本身不因此从 completed 改为 error。默认单份 ZIP 最多 18 MiB。

## 已知边界

- 历史的 `conversation_id=None` 请求不能凭原请求 ID 反推出已经关闭的 ChatGPT 标签页 URL。旧窗口已关闭且管理员没有会话链接时，应导出 `matching_page_unavailable` 错误日志，而非重新生成或抓取其他人的回答。
- 单个 ChatGPT 会话可能存在多轮回复；只靠 `/c/` URL 无法证明指定请求对应的那一轮。请在日志里确认关联等级，并与后台「生成回复（文字与图片）」比对后再认定 1:1。
- 取证受到最多 30,000 DOM 节点、500,000 文本字符、24 张页面分段截图、18 MiB ZIP 上限约束。若超出限制，日志应显示失败或截断，不应假报完整。
- Chrome/Worker 运行时需要正确监听本机 CDP；Windows Worker 不在该 Linux v1 工具范围内。
- 发布代码和 CI 通过只能证明静态/模拟链路；真实历史 ChatGPT 页面加载、图片命中和 1:1 仍需要 Worker 实机日志核验。


## 2026-11-02 v1.1.0 诊断失败复盘与 UI 更新

本轮 `req_3a882bb5690442c48a9df887a0fd7ad9` 的 v1.0.0 诊断报 `matching_page_unavailable`；Worker 诊断确认 CDP 在 localhost:9222 监听。请求原生页面窗口在完成后自动关闭，且旧版完成日志未记录会话 URL。

v1.1.0 新增严格的 Chrome History 找回逻辑：中心服务端从原请求时间和已生成助手正文生成最多六组 SHA-256 字符串指纹，只将时间上下界和哈希传给相同 Worker。工具只读取该 Chrome profile 在这段时间访问的 `chatgpt.com/c/` URL（不扫描其它网站、不访问 Cookie），最多处理 6 个候选。重新打开候选后须至少两组正文指纹匹配才能交付，其他情况明确提示用户提供原生会话链接。自动找回的产物清单标注 `chrome-history-content-sha256-verified`。这不是无条件保证旧记录都可恢复：历史被清除、原生回复文本差异过大或无持久化回复正文时仍需手动定位。

v1.0.0 在 CDP 页面主世界读取扩展隔离世界的 `globalThis` 状态，不能可靠地检查已完成请求。新请求由 request-v6 在相关的助手 DOM 节点上添加 inert `data-chat2api-native-request-id` 标识，诊断脚本读取该标识精确关联。额外的写 DOM 行为被 try/catch 限制，绝不接管请求完成裁决。

后台 Worker 工具调整为：原生页面诊断置顶，Worker 下拉仅显示 Worker 管理登记的真实完整 Linux `wrk_...` ID；工具目录与 Worker 有效配置合成一个表并默认折叠，原有探针证据也默认折叠。请求记录中右键 ID 后点击“复制完整请求 ID”。

**部署顺序：** 这次修改同时涉及服务端、Worker Controller 和 Chrome 扩展，因此第一次升级必须同步更新服务端与 Linux Worker。后续仅更新 `native_page_diagnostic_v1.py` 工具内容时，仍可使用工具中心的独立更新按钮。


## v1.2.0：一个输入框同时支持请求 ID 与原生历史链接

- Worker 工具 →「原生页面诊断」只保留一个目标输入：可粘贴完整 `req_...`，或独立的 `https://chatgpt.com/c/<conversation-id>`。请求 ID 自动使用其归属 Worker 与已保存的安全会话 URL；直接输入 URL 时，管理员需选择已登录该 ChatGPT 账号的真实 Linux Worker。服务端同样进行 URL 域名/协议/路径白名单和 Worker 身份校验。
- **URL-only** 没有业务请求 ID。服务端在 Worker 内部生成 `req_diag_<jobid>` 以满足原有固定协议和 ZIP 文件所有权校验，但后台记录不将它伪装成原业务请求。ZIP `manifest.json` 标记 `request_binding=url-only-unbound`，不能声称抓取内容属于某条 chat2api API 请求。
- 以后新产生的请求：request-v6 在生成过程持续捕获当前 ChatGPT 页面经过校验的 `https://chatgpt.com/c/...` 地址，并在 completed/error/cancelled 的请求终态诊断里保存 `native_chatgpt_conversation_url`。不保存 Cookie、原始查询参数、媒体 URL 或浏览器历史；Broker 服务端再次验证主机名/协议/路径，避免无效 URL 进入请求日志。
- 「请求记录」请求 ID 单元格右键菜单现在有 **复制完整请求 ID**、**复制原生 ChatGPT 地址** 两项。后者从已有请求详情里的 URL 复制，未留存的历史请求明确提示缺失，不会猜造地址。
- 如果 ChatGPT 没分配标准 /c/ 会话地址（如临时会话、账号中断、提交前报错），字段可能仍为空；v1.2.0 只能保证**存在并曾观察到安全的 URL 时予以保存**，不承诺所有请求都有可重新打开的链接。


## v1.2.1：独立页面诊断失败证据与真实历史页面结构兼容

2026-11-02 用户的 URL-only 诊断任务 `75bbd367af6e40a0aff8fa7948ffcea1`：工具 v1.2.0，Worker `wrk_58e403d5e5f85ac18e83b861`，从 13:25:06 到 13:25:35（UTC+8）约 29 秒，`history_reply_unavailable`。日志只提供统一错误，未给出登录状态、真实页面的助手区域是否存在、JS 是否抛异常，**不能从现有日志认定它一定是登录问题**。

修复：
- 独立 Python 页面采集器升级 **v1.2.1**：在现有安全范围内支持历史页 `article/[data-testid=conversation-turn]` 内嵌的 `ChatGPT said` 标题，以及显式 `assistant-message/assistant-turn` 标记，不依赖仅有的同级标题后兄弟节点。仍拒绝包含用户消息的候选节点。
- 页面加载期间只采集有限的结构证据：document.readyState、路径**类别**（不含实际 URL）、main/assistant/user/turn/标题标记数量、密码框/挑战 iframe/加载 skeleton 存在性，以及固定种类的 CDP JS 结果。**不读取页面标题、个人资料、聊天正文、Cookie、LocalStorage、凭据或页面原始 URL**。不发送生成请求、不改变 `chat.completed` 权限。
- 将原有模糊错误细分为 `history_access_gate_detected`、`history_challenge_detected`、`history_page_still_loading`、`history_assistant_boundary_not_found`、`history_capture_runtime_failed`、`history_assistant_capture_rejected`；最终失败时 `page_probe` 会保留到 Worker status、服务端记录及可导出的日志 JSON，**失败也可见证据**。
- 服务端更新清单的版本号自动从 Python 工具源码读取，保持独立诊断版本及 SHA256 一致。未来变更采集脚本可从后台单独“更新诊断工具”到新版本，不必反复修改服务端清单常量。

验收：代码 CI 和模拟历史回复 DOM 测试不代表 Worker 登录状态实际可用。部署后对同一条历史链接诊断，失败时直接导出 JSON 并观察新增 `page_probe`；应能区分访问门槛、DOM 边界和 JS 运行异常，从而再实施有确切实机证据的下一步修复。


## v1.2.2：直接打开独立 Worker Chrome 标签页，不尝试登录

2026-11-02 的 URL-only 实机日志 `fdb56dab367f4591a694bfae681b59b7`，工具 1.2.1，原地址 `https://chatgpt.com/c/6ae7dac6-8328-83e8-878d-fc3599d7b10b`，在 29 秒后失败：`history_page_still_loading`，但 `page_probe=null`。现有日志**不能证明**是浏览器需要认证，不能直接要求用户登录。

此次 v1.2.2 修改页面定位方式：
- 输入精确 /c/ 历史链接时，**总是**在所选 Linux Worker 的现有 Chrome 浏览器配置中通过 localhost CDP `PUT /json/new?<encoded URL>` 创建一个专用新标签页，即使同样的历史链接已经在别的 Worker 标签页中打开也不复用、不干扰。
- 工具不会执行登录或提供凭据，也不会向 ChatGPT 发送新生成请求。如果当前 Chrome 已有可访问会话，新页面直接显示历史内容；如果 ChatGPT 自行转向身份验证页面，日志只记录 `navigation_route=access-gate` 而不读取页面私密信息或尝试绕过认证。
- 用本次诊断创建的 Chrome tab id 定位页面，保持对 `about:blank`、网页跳转和临时导航错误的感知，最多等待 45 次页面观察；只有可信助手回复真实存在才开始 DOM/截图采集。分类导航错误 `history_browser_navigation_failed`、`history_unexpected_redirect`、`history_diagnostic_tab_missing`。
- 失败时除了状态 JSON 中的只读结构化 `page_probe`，错误文本也附带有限安全摘要（页面路由类别/readyState/固定采集状态/助手节点数），防止较旧 Controller 没转发探针时再丢失全部原因。服务端也禁止使用新轮询中的 null 覆盖上一次合法探针。
- 旧版未关闭的会话页仍可由请求 ID + 精确请求标记匹配；有历史 URL 时优先专用新页。独立工具还是只读观察，不干扰 request-v6 唯一终态、窗口池或原请求媒体交付。

验证：新增模拟 Chrome CDP/new-tab 的回归测试，确保同 URL 已存在时仍单独发起且仅发起一次 PUT，新标签页能够完成证据 ZIP，不需要重新生成请求。**部署后仍需复测真实 ChatGPT 历史页面，不能把 CI 通过误写成实际 1:1 成功。**


## v1.2.2 起的真正独立工具发布通道（2026-11-02）

现有旧接口 `/bootstrap/native-page-diagnostic.py` 原本直接读取部署镜像里的 Python 文件，因此“更新诊断工具”只会重复安装**服务端已经拥有**的版本，若工具源代码发生变化必须先重建/部署 chat2api 服务端。这不符合独立更新需求。

**本次为一次性的服务端更新机制改造。** 安装新版本的服务端和 Worker Controller 后，后续如果仅修改独立采集脚本，不再需要更改或部署 chat2api main 分支。专用 GitHub 发布分支：

`b8vipvip/chat2api` / `native-page-diagnostic-releases`

操作方式：
1. 在上述固定分支单独修改 `scripts/native_page_diagnostic_v1.py`，每次修改务必增加脚本内独立 `VERSION`（例如 1.2.3、1.2.4）；不推送服务端 main，不重新编译 Docker 镜像，不操作普通 Linux Worker 升级。
2. 后台的 **「更新诊断工具」** 触发 Worker 既有 `native_page_update` 固定指令；Worker 只向现有 HTTPS 中心服务端拉取清单及 SHA256 绑定的脚本。服务端固定从 GitHub Releases 专用分支（GitHub Contents API）读取工具源码并校验 Git blob SHA1、完整文件 SHA256、大小、Python 可编译性及独立工具版本；缓存不超过 90 秒。远程获取失败返回明确错误，不隐式降级到镜像旧脚本。
3. Worker 再次校验来源元数据、下载文件大小、SHA256、源代码标称 VERSION 和 Python 语法，并禁止降级或在**同版本**下用不同代码覆盖旧版。原子替换 `/var/lib/chat2api-worker/controller/native_page_diagnostic_v1.py`，没有重启 Worker、Chrome 或 server。再次点击而内容未变则返回 `updated=false`（已是最新）。
4. **「检查版本」** 与「更新诊断工具」同排：显示此 Worker 实际装好的版本及独立发布分支的最新版本，远程不可达则标识检查失败，不虚报成功。
5. 主分支与服务端 Git commit 保持不变；未来只改诊断采集脚本时，由**发布分支**的独立提交记录每次版本，不会触发主分支 Server→Worker 全量更新。

安全限制：上游是写死的 GitHub 公共仓库、路径与专用分支；不能通过 UI/Worker 命令指定新源或下载 URL。脚本原本就有执行权限，所以必须只允许管理员手动触发并校验独立版本。依赖服务端访问 `api.github.com` 的 HTTPS 能力及该仓库发布分支正常可用。GitHub 请求失败时已有诊断任务不受影响。

**一次性注意**：此修改包含服务端代理逻辑、工具更新器的安全检查和后台按钮布局。因此**只在这次更新机制改造时**需要更新 chat2api 服务端，并由既有自动同步更新 Linux Worker；之后仅工具脚本发布，不再需要服务端升级。
