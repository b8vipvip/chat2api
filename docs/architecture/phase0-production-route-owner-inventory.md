# Phase 0：生产路由与控制面 Owner 清单（静态审计）

> **状态：** Static inventory / runtime snapshot pending  
> **仓库：** b8vipvip/chat2api  
> **基线：** main 4ba1113e9b84b902453d785860b923906b80e9f2  
> **关联：** #784 / PR #785  
> **本文件不修改生产代码。**

## 1. 范围与判读方式

生产入口是 app.entry:app。app.main 创建基础 FastAPI 应用和主要业务路由，app.entry 再按固定顺序执行 92 个唯一 installer（93 次调用，install_request_history_v94_patch 重复调用一次作为最终 owner sentinel）。

本清单按“入口面 → 路由 owner → 身份/授权 owner → 关键状态 owner”整理。它是对主要路由模块的静态清单，不等于运行时路由快照。Phase 0 退出前仍需在 CI 中导入 app.entry:app，生成最终 path/method/name/dependencies 清单，并检查重复路由和 middleware stack。

## 2. 身份与授权边界

| 主体 | 认证凭据 | 主要入口 | 权威模块 | 不能混淆的身份 |
|---|---|---|---|---|
| OpenAI API caller | Bearer / x-api-key；managed API Key 或 master compatibility principal | /v1/* | require_chat_principal、ApiKeyStore | API Key principal 不是最终 user session，也不是 Worker ID |
| 管理员 | chat2api_admin_session Cookie | /api/admin/*、/api/clients | AdminSessionStore + v17 middleware | 管理员 Session 不等于业务 API Key |
| 用户控制台用户 | chat2api_user_session Cookie | /api/user/* | UserAccountStore | user_id 不能从 API Key 名称或 Worker ID 推断 |
| Chrome Bridge | pairing code 注册、每个 client token / WebSocket | /api/extensions/register、/ws/extensions/{client_id} | PairingStore + ClientRegistry | ext_* 是执行节点，不是最终用户 |
| Linux Worker | enrollment code、Worker 控制通道 token | /api/workers/enroll、/api/workers/connect | LinuxWorkerStore + Worker socket registry | wrk_* 与其控制 Chrome ext_* ID 是不同身份 |
| 媒体访问者 | URL 中的 opaque media token（资源级 bearer capability） | /api/media/{media_id}/{access_token}[/{variant}] | MediaArchive | media token 只授权单个媒体资源，不授予 API 或管理权限 |
| 生成文件访问者 | opaque file access token / owner checks | /api/generated-files/*、/v1/generated-files/* | GeneratedFileArchive / DeferredGeneratedFiles | 下载 token 不是 API Key |

## 3. 主要路由面

### 3.1 系统、版本与控制台 shell

| Method / Path | 来源 | 授权 | 说明 |
|---|---|---|---|
| GET / | app.main | public | 服务信息/健康概览入口 |
| GET /healthz | app.main | public | 基础健康检查 |
| GET /version | runtime_contract | public | Server/Bridge 版本契约 |
| GET /admin | app.main + HTML middleware stack | public shell；数据 API 受管理员 Session 保护 | 管理控制台页面 |
| GET /developers | app.main + HTML middleware stack | public shell；数据 API 受管理员 Session 保护 | API 文档/开发控制台 |
| GET /console | user_console_v104_patch | public shell；数据 API 受用户 Session 保护 | 用户控制台页面 |

### 3.2 OpenAI 兼容 API

| Method / Path | 来源 | 授权 / Owner | 审计说明 |
|---|---|---|---|
| GET /v1/models | app.main | API principal | 在线模型目录 |
| GET /v1/models/{model_id} | v13_patch | API principal | 单模型查询 |
| POST /v1/chat/completions | app.main | API principal → model routing → Broker | 核心文本请求入口 |
| POST /v1/images/generations | app.main | API principal → image engine | 图片生成入口 |
| POST /v1/audio/transcriptions | v7_patch | API principal | 语音转录兼容入口 |
| POST /v1/files | app.main | API principal | 当前为 JSON/base64 上传契约，不是完整 multipart OpenAI Files |
| GET /v1/files/{file_id} | app.main | API principal + owner | 文件元数据/读取 |
| DELETE /v1/files/{file_id} | app.main | API principal + owner | 文件删除 |
| POST /v1/completions | v13_patch | API principal | 旧文本 Completion adapter |
| POST /v1/responses | v13_patch **及** responses_v108_patch | API principal；route owner 有冲突 | 见 P1-12 |
| GET /v1/responses/{response_id} | responses_v108_patch | API principal + response owner | v108 response store 当前为进程内最多 256 条 |
| GET /v1/native-content/{request_id} | app.main | API principal/owner | 原生内容副轨读取 |
| GET /v1/native-content/{request_id}/events | app.main | API principal/owner | 原生内容事件读取 |

### 3.3 Worker / Bridge 通信

| Method / Path | 来源 | 授权 | 状态 owner |
|---|---|---|---|
| POST /api/extensions/register | app.main + v17_patch middleware | pairing code + device_id | PairingStore / ClientRegistry |
| WS /ws/extensions/{client_id} | app.main | per-client registration token | ClientRegistry socket + RequestBroker |
| POST /api/workers/enroll | linux_worker_patch | one-time enrollment code | LinuxWorkerStore |
| WS /api/workers/connect | linux_worker_patch | enrolled Worker identity/token | Linux Worker socket registry |
| GET /bootstrap/linux-worker.sh | linux_worker_patch | public installer script | Linux Worker release/install contract |

Chrome Bridge 和 Linux Worker 的注册、连接、控制命令是两个通道。不能将 ext_* 连接与 wrk_* controller 当作同一个 socket，也不能把 Worker 的代理配置当作最终用户的官方网页 Proxy Session。

### 3.4 管理控制面

v17_patch middleware 对 /api/admin/*（排除 /api/admin/auth/*）和 /api/clients 执行 AdminSessionStore gate。通过 Session 后，它向旧有 Depends(require_admin_key) 注入内部兼容 token。外部 master API key 不再作为管理端的正常登录凭据。

主要路由组：

- 管理员认证：POST /api/admin/auth/login、GET /api/admin/auth/session、POST /api/admin/auth/logout。
- 概览与 Key：/api/admin/overview、/api/admin/keys、/api/admin/requests。
- 请求和诊断：/api/admin/diagnostics/*、/api/admin/requests/{request_id}/log、/api/admin/worker-tools/*。
- 媒体与生成文件：/api/admin/media-storage/*、/api/admin/media-storage/generated-files/*。
- Worker 管理：/api/admin/linux-workers/*、/api/admin/extensions/*、/api/admin/extensions/{client_id}/capacity/*。
- 用户商业管理：/api/admin/user-pricing、/api/admin/user-payments、/api/admin/payment-channels-v106/*。
- 原生页面诊断：/api/admin/worker-tools/native-page/*。
- 服务更新：/api/admin/server-update、/api/admin/server-update/status、/api/admin/server-update/start。
- 管理页面资产：/assets/chat2api-admin-*.js、/assets/chat2api-v*.js。

注意：部分新 installer 自身也再次检查 AdminSessionStore（例如 linux_worker_patch、native_page_diagnostic_api）；这属于 defense-in-depth，但不应形成互相矛盾的认证真值。admin_v17.js 替换最终页面的 master-key transport，不代表每个独立 HTML/JS 文件都可单独验收。

### 3.5 用户控制面与支付

用户控制台主要路由：

- 账户：POST /api/user/register、POST /api/user/login、POST /api/user/logout、GET /api/user/me、PATCH /api/user/profile。
- API Key：GET/POST /api/user/keys、GET /api/user/keys/{key_id}/secret、PATCH /api/user/keys/{key_id}/quota、DELETE /api/user/keys/{key_id}。
- 用户数据：GET /api/user/requests、GET /api/user/models、GET /api/user/billing、GET /api/user/dashboard。
- Playground：POST /api/user/playground。
- 充值：POST /api/user/billing/recharge、GET/POST /api/user/payments/zpay/notify、GET /api/user/payments/zpay/return。
- 支付渠道：GET /api/user/payment-channels-v106、POST /api/user/billing/recharge-v106、POST /api/user/payments/v106/usdt/{order_id}/txid、支付 provider callback。
- 管理端支付：GET/PUT /api/admin/payment-channels-v106、POST /api/admin/payment-channels-v106/test、USDT order review endpoints。

user console 路由以 UserAccountStore 的 user session 为主要授权 owner；支付 provider callback 是外部 webhook/return，不应被误当作用户 Cookie 会话。管理员商业配置由 AdminSessionStore gate + module-local admin check 保护。

## 4. P1-12：Responses 重复路由与 first-match 遮蔽

当前生产安装顺序：

1. entry.py 早期执行 install_v13_patch(app)。
2. v13_patch 注册 POST /v1/responses。
3. entry.py 后续执行 install_responses_model_routing_v108_patch(app)。
4. 再后续执行 install_responses_v108_patch(app)，再次注册 POST /v1/responses 和 GET /v1/responses/{response_id}。

v13 route 的 handler 对 body.tools 非空明确返回 unsupported。v108 route 才调用 web_search / image_generation tool handler，并写入 responses_v108_store。

ResponsesModelContextMiddleware 会设置 model context、包裹 request/response、记录 telemetry，但最终仍调用下层 FastAPI router，不会重排或替换重复 route。

Starlette Router 对 route list 按顺序进行 first-match；重复 path/method 时较早注册的 v13 route 会先匹配。因此生产 POST /v1/responses 可能进入旧 handler，v108 的 tools 和 response store 路径被遮蔽；GET retrieve 可能返回找不到 response。

### 必须新增的生产入口回归

- 导入 app.entry:app，断言 POST /v1/responses 恰好一个 route owner。
- 通过生产 ASGI app 发送 web_search tool 请求，断言进入 v108 tool path。
- 通过生产 ASGI app 发送 image_generation tool 请求，断言复用现有 Images engine，且只 dispatch 一次。
- 创建 response 后，使用同一 principal GET /v1/responses/{response_id}，验证 owner scope 和 retrieve。
- streaming / non-streaming 分别验证 terminal、telemetry、billing、request_id 只记录一次。
- 不允许只在一个空 FastAPI app 上单独安装 v108 patch 的单元测试替代生产组合入口测试。

## 5. Middleware 与 installer 组合注意事项

### 5.1 已确认的关键 middleware

- app.main：CORSMiddleware 与 SSETransportTruth。
- app.v8_patch：DiagnosticMiddleware。
- app.v17_patch：管理员 Session / pairing / master-key removal middleware。
- user_console_v104_patch：用户控制台与管理端兼容 middleware。
- payment_channels_v106_patch：支付渠道访问与 callback middleware。
- user_console_privacy_v105_patch：用户数据隐私边界 middleware。
- responses_model_routing_v108_patch：Responses emulated tools middleware 与 Responses model/telemetry middleware。
- app.entry 最后安装 stream_keepalive_patch；其注释明确要求它位于会注册 BaseHTTPMiddleware 的 patch 之后。

本轮没有在运行时导入 app.entry:app 并序列化 Starlette user_middleware 的最终对象链。因此 middleware 最终 outer-to-inner 顺序仍标记为 runtime snapshot pending，不以 installer 源码顺序直接冒充执行顺序。

### 5.2 Installer 与状态 owner

- 92 个唯一 installer，93 次调用；request_history_v94_patch 重复调用一次作为 final-owner sentinel。
- installer 可替换 Registry/Broker/Telemetry 方法；不能只以“函数存在”判断 owner。
- v17_admin middleware 是 /api/admin/* 与 /api/clients 的统一 Session gate。
- RequestBroker 是请求状态 owner；Worker terminal 和原生 snapshot finality 不得被协议适配器复制。
- Responses 当前有独立 adapter 和 model middleware，但没有共享 ChatRequestService。
- 当前 route shadow 表明静态模块测试不足以保证生产组合入口的最终 owner。

## 6. Phase 0 退出前的自动化要求

1. 在 CI 中 import app.entry:app，输出每条 route 的 method、path、name、endpoint module、dependencies。
2. 对所有重复 method/path 执行失败门禁，除非显式 allowlist 并有理由说明 route order。
3. 输出最终 middleware class 的 outer-to-inner 列表。
4. 对 /api/admin/*、/api/user/*、/v1/*、Worker WebSocket 和 opaque resource URL 建立 auth smoke matrix。
5. 测试 app.entry:app 的 lifespan，而不是只测试单个 installer。
6. 对关键 method/path 标注唯一 handler owner；新增路由必须更新清单。

**结论：** 当前清单已覆盖核心公开 API、Worker、管理员、用户、支付、媒体和诊断路由的主要所有者；它仍是静态清单。只有生产 app.entry:app 路由和 middleware 运行时快照进入 CI，并通过唯一 owner 检查后，才能将 Phase 0 的路由图标记为完成。
