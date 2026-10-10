# Worker 自动登录与会话恢复（v154，Draft）

本功能按 **Worker ID** 管理 ChatGPT 账号；适用于已经绑定 Chrome Bridge 的 Windows Worker 与 Linux Worker。Linux 首次创建独立 Chrome Profile、安装并完成 Bridge 绑定仍需人工操作。

## 控制台

在 **Worker管理** 中选择 **Windows Worker** 或 **Linux Worker**，找到对应 Worker ID 的 **自动登录设置**，输入登录邮箱、密码和可选的 Base32 TOTP 验证器密钥，启用自动检测并保存。可点击 **触发登录恢复** 主动尝试；自动流程遇到挑战时，可点击 **人工接管** 停止自动填写并聚焦已有登录窗口。

服务器将密码及 TOTP 种子以 Fernet 加密存入数据目录，分别使用文件权限 `0600` 的数据文件和密钥文件；更推荐通过 `CHAT2API_WORKER_LOGIN_KEY` 在独立密钥管理服务中注入密钥。**同时泄露数据文件和本地密钥文件仍会暴露账号凭据。** 请妥善保护备份、数据卷及管理员会话。

配置 API（仅管理员 Session 可用）：

- `GET /api/admin/worker-login/{worker_id}`：查询非敏感配置标识和恢复状态；
- `PUT /api/admin/worker-login/{worker_id}`：保存或更新账号配置；
- `DELETE /api/admin/worker-login/{worker_id}`：删除账号配置；
- `POST /api/admin/worker-login/{worker_id}/trigger`：手动触发自动恢复；
- `POST /api/admin/worker-login/{worker_id}/manual`：停止当前自动填写并打开人工登录窗口；
- `GET /api/admin/worker-login/{worker_id}/totp`：管理员手动获取当前验证码（响应不缓存）。

## 自动恢复流程

1. 扩展 `login-v27` 回报登录状态。连续两个**不同检测时间戳**的新鲜、高/中置信度 `login_required` 状态触发恢复；重复上报不算新检测。
2. 服务器确认 Worker ID 对应已绑定、在线且未禁用的 Chrome Bridge。对于加密的 WebSocket（`wss`）或服务器本机回环连接（`127.0.0.1` / `::1`），通过已认证的 Bridge 连接下发该 Worker 的邮箱和密码。**禁止通过远端明文 `ws` 传输凭据**；此时仅打开人工登录窗口。
3. 扩展在窗口池专属后台登录界面中尝试填写账号、密码。扩展只在 `chatgpt.com`、`www.chatgpt.com`、`chat.openai.com`、`auth.openai.com`、`login.openai.com` 的 HTTPS 页面中运行登录填写。
4. 遇到 TOTP 验证时，扩展通过认证 WebSocket 为此次恢复任务请求当前六位码；服务器本地生成一次性验证码，**不会将 TOTP 种子下发给扩展**。如果未配置密钥，则立即转为人工登录。
5. 服务器只有收到此次任务发起时间**之后**的新鲜 `ready + composer_ready` 证据，才把恢复标记为 `logged_in`。窗口已打开、字段已填写、收到扩展“成功”通知都不能单独认定登录成功。
6. 发生 CAPTCHA、额外身份挑战、异常登录方式或页面无法识别时，停止自动操作并保留人工接管入口。

单次恢复上限 **180 秒**，相邻自动尝试冷却 **300 秒**。最近 **30 分钟**内累计 **3 次失败**后暂停自动恢复；管理员可以在控制台主动触发重试或重新保存配置重置计数。已有的服务端登录就绪路由门禁仍会在未确认 `ready` 时拒绝新请求，不会把登录中 Worker 误判为可调度 Worker。

## 连接安全与部署

必须使用正确配置的 TLS / `wss` 连接，或仅用于可信本机回环环境的明文连接。通过 TLS 反向代理部署时，确保 ASGI 能识别 HTTPS/WSS；如果 ASGI 实际看到的是远端明文 `ws`，恢复自动降级为人工登录，**不会下发密码**。不要通过不可信的中间代理发送账号密码。

扩展只在恢复期间的临时内存持有账号密码与六位 OTP；不写入 `chrome.storage.local` 或 `chrome.storage.session`，不包含在 Worker 元数据或常规日志中。网络上仍需依靠 TLS 和管理员授权来保护凭据。

## 未完成验证

本次 PR 有 Python 单元/行为测试、Chrome JS 语法、现有登录探测 VM 测试与 Docker 冒烟测试，但**未对真实 Windows/Linux Chrome 及 ChatGPT 最新登录流程进行端到端操作验证**。实际邮箱/密码页面、TOTP UI、TOTP 时钟同步、扩展后台保活、反向代理 WSS 以及登录成功后恢复 API 请求均需实机确认。若认证页面变更或出现新挑战，只能转人工完成；不能保证所有登录方法都能无人值守。此 PR 在完成实机验证前保持 Draft。
