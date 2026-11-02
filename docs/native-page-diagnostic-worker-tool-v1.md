# Worker 工具 · 原生页面诊断 v1.0.0

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
