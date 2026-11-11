# Phase 0 代码级架构审计报告 v1.0

> **审计类型：** 静态生产代码审计 + GitHub Actions 证据核验  
> **仓库：** b8vipvip/chat2api  
> **审计基线：** main / 4ba1113e9b84b902453d785860b923906b80e9f2  
> **审计日期：** 2026-11-11  
> **关联专项：** Issue #784  
> **现有系统总验收：** Issue #585  
> **变更范围：** 本分支只增加审计文档，不修改生产代码、Worker 协议或数据文件。

## 0. 结论摘要

当前 Chat2API 已经不是简单的单路由 FastAPI 服务。生产应用由 app.main 创建基础应用，再由 app.entry 按固定顺序安装大量历史版本 patch/installer。现有系统已经包含 API Key、管理员 Session、用户账户、Worker Registry、请求 Broker、模型能力路由、媒体归档、原生内容 sidecar、Responses API、用户控制台和支付/账单等能力。

因此，#784 的正确实施路径是**复用现有权威模块、抽取共享服务、增加新的隔离边界**，而不是重新写一套 Worker 调度或把用户 Playground 改名为 Chat。

### 0.1 最高优先级发现

| ID | 级别 | 发现 | 直接影响 |
|---|---|---|---|
| P0-01 | P0 | MediaArchive 的 settings.enc 仍由 CHAT2API_API_KEY 派生密钥加密；新配置已将该变量定义为废弃迁移输入，且默认值为空 | 新安装可能使用可预测的空密钥派生值；更换旧环境变量后，已有 S3 凭据配置可能无法解密 |
| P0-02 | P0 | PaymentChannelsStore 不使用 data_secret.key，而是读取 .chat2api_data_secret；缺失时 fallback 到公开固定字符串 chat2api | 新安装的支付商户密钥可能由公开常量推导；旧密文与密钥来源不一致时可能静默显示未配置 |
| P1-01 | P1 | Admin Session 已安装，但默认管理员密码仍为 change-me-admin，登录入口没有发现速率限制 | 部署配置遗漏时存在弱口令和暴力尝试风险 |
| P1-02 | P1 | Broker 和 Worker WebSocket 都是进程内状态；Docker 默认单 Uvicorn worker | 不能直接横向扩展或以多 worker 运行 |
| P1-03 | P1 | 标准 API 兼容面有限；部分字段被 Pydantic 接受但没有被传入 Worker 执行选项 | SDK 调用可能表面成功、参数语义却未生效 |
| P1-04 | P1 | API Key quota 和用户账单在请求完成后计量，没有 admission 阶段的原子额度预留 | 并发请求可能超过额度/余额后才结算 |
| P1-05 | P1 | 账户、Key、价格、账单、支付和文件索引分散在多个 JSON/JSONL 文件，媒体/生成文件另用 SQLite | 跨实体更新缺少统一事务，迁移和多进程写入复杂 |
| P1-06 | P1 | 当前只有单管理员账号配置和 Cookie Session，没有完整管理员目录、RBAC、租户成员关系 | 无法满足多管理员、职责分离和资源级授权 |
| P1-07 | P1 | 当前用户注册是直接开放的自助注册入口，没有服务端注册策略开关 | 无法按管理员配置开放/邀请/关闭注册 |
| P1-08 | P1 | Responses response store 仅保存在进程内 OrderedDict，最多 256 条 | 重启后 response_id 查询不可恢复 |
| P1-09 | P1 | ApiKeyStore、PaymentConfigStore、MediaArchive、PaymentChannelsStore 的密钥来源、派生用途和迁移行为不统一 | 不能把旧 CHAT2API_API_KEY 或 data_secret.key 视为所有加密配置的统一根密钥 |
| P1-11 | P1 | data_secret.key 丢失/为空时 load_or_create_data_secret 会静默生成新 key；部分 Store 解密失败返回空字符串 | 已存在的 API Key / 支付密文可能不可恢复，且错误可能被误认为配置为空 |
| P1-12 | P1 | v13_patch 与 responses_v108_patch 都注册 POST /v1/responses；v13 更早安装且没有发现移除旧路由的逻辑 | Starlette first-match 会先进入旧 v13 handler；v108 的 web_search/image_generation 与 response store 路径可能被遮蔽，GET retrieve 可能无法读取创建结果 |
| P1-10 | P1 | 当前没有独立 Web Chat、Conversation Store、Plugin Registry 或官方网页代理入口 | #784 的主要新增产品仍需建设 |
| P2-01 | P2 | 生产 installer 链包含 92 个唯一 installer、93 次调用，历史 patch 通过包装/替换方法积累 | 新功能继续叠加 installer 会增加顺序耦合和回归成本 |
| P2-02 | P2 | README 与 VERSIONING 文档版本号落后于代码契约 | 用户可能依据文档部署错误版本 |
| P2-03 | P2 | 没有发现 Playwright/Selenium/Cypress 真实浏览器 E2E 测试 | VM 测试不能证明真实 Chrome/官方网页代理行为 |
| P2-04 | P2 | Docker 镜像没有 USER 指令，依赖版本范围没有 lock/hash 锁定 | 生产容器最小权限与可复现构建仍需加固 |

以上为代码基线审计结论，不等同于已经验证生产部署环境的运行状态。当前未读取任何生产环境密钥、实际 .env 内容或用户上游会话。

---

## 1. 审计边界与证据等级

### 1.1 本轮读取范围

- 生产入口：app.entry、app.main、app.config、Dockerfile、docker-compose.yml。
- 请求核心：app.broker、app.registry、app.api_keys、app.telemetry、app.models。
- 用户/管理员：app.admin_auth、app.v17_patch、app.user_commerce、app.user_console_v104_patch、app.payment_channels_v106_patch。
- API：Chat Completions、Files、Images、Responses API 及其模型上下文 middleware。
- 富内容：native_content_v1、native_snapshot_finality_v282、sse_asgi_transport_truth_v284、media_archive_v173、generated file archive。
- 模型：model_capability_routing_patch、model_observability_v144_patch、worker_model_library_v145_patch。
- Worker：Chrome manifest、background_entry、request-v6、native content content script。
- 测试：runtime contract、管理员 Session 重启、用户控制台、Key quota、支付、Responses metering、媒体读回完整性。
- GitHub Actions：main 基线提交的 check runs、test job 和 reconcile job 日志。

### 1.2 证据等级

- **已确认：** 由当前 main SHA 的源码或测试直接支持。
- **风险推断：** 由代码结构和运行拓扑推导，需通过部署配置或并发测试进一步确认。
- **未验证：** 需要实际生产环境、真实 Worker、真实上游账号或浏览器测试；本轮不作成功声明。

本报告是静态审计。没有在本轮对真实部署服务器执行压力测试，也没有访问生产 .env、数据库或真实 ChatGPT 登录态。

---

## 2. 生产入口与安装链

### 2.1 生产入口

Dockerfile 的启动命令是 Uvicorn app.entry:app。app.entry 从 app.main 导入已经通过 create_app() 创建的 FastAPI 实例，然后依次执行 installer。生产行为必须以 app.entry:app 为准，不能只用 app.main 的基础 app 做功能判断。

关键文件：

- app/main.py：基础 FastAPI app、状态对象、主要 API 路由。
- app/entry.py：生产组合安装入口。
- app/runtime_contract.py：Server Runtime / Chrome Bridge 版本契约。
- Dockerfile、docker-compose.yml：容器运行方式。

### 2.2 Installer 统计

对 app/entry.py 的 import 和调用进行静态统计：

- 92 行 installer import。
- 93 次 installer 调用。
- 92 个唯一 installer。
- install_request_history_v94_patch 被调用两次；第二次位于入口文件末尾，代码注释和测试将其作为最终 owner sentinel。
- install_runtime_contract 位于 app/entry.py 第 155 行，之后仍有多个 installer。
- install_stream_keepalive_patch 位于第 308 行。
- 最后一行再次调用 install_request_history_v94_patch。

这不是“92 个独立微服务”，而是同一个 FastAPI app 上的显式组合安装与方法包装链。部分 installer 会替换 registry.resolve_client、registry.send、broker.publish、broker.create/release 或中间件行为。

**架构判断：** 现有 patch 链在历史演进中承担了兼容和唯一所有权迁移，但不适合继续作为新产品架构的主要扩展方式。#784 新代码应优先采用明确的 service、policy、adapter 和 middleware；旧 installer 只保留为兼容边界，逐步建立可测试的退出路径。

### 2.3 Installer 测试缺口

当前 runtime contract 测试确认了 runtime contract 在 v21_5 之后安装、版本资源路由唯一，以及部分 Worker 资产顺序。但没有看到一份自动生成并冻结的完整生产路由表、middleware 顺序表和所有方法包装链快照。

建议增加：

1. app.entry:app 的最终路由清单快照。
2. middleware 真实执行顺序测试。
3. 每个核心方法的 owner/wrapper 清单。
4. installer 重复调用与导入顺序的幂等测试。
5. 每次新增 installer 必须说明其 owner、被包装对象、上游/下游调用和失败回滚行为。

---

## 3. 当前请求数据面与单一决策权

### 3.1 Chat Completions 请求链

当前主要链路：

外部请求 → FastAPI Chat Completions 路由 → API Key 认证与 scope → 模型能力上下文 → Worker 选择 → RequestBroker → ClientRegistry WebSocket → Chrome Worker → request-v6 终态 → Broker first-terminal guard → Telemetry → SSE/JSON Adapter。

现有职责应继续保持：

| 事实 | 当前权威或核心模块 | 新功能约束 |
|---|---|---|
| API Key 身份 | ApiKeyStore / require_api_key | 新 BFF 应复用同一认证服务，不复制 Key 解析 |
| Worker 身份与在线连接 | ClientRegistry | 不得把 Worker ID 当作最终用户 ID |
| 请求运行状态 | RequestBroker | 新协议适配器不另造 Broker |
| Worker 请求终态 | Chrome request-v6 | 不允许第二个 Worker terminal owner |
| 服务端首终态 | RequestBroker.publish | 后续事件不能推翻首终态 |
| 模型能力与路由 | model capability routing + Worker model library | 不允许价格表或客户端字段伪造实际模型 |
| 原生内容最终完整性 | native_snapshot_finality_v282 | 新 Renderer 不能自行宣布 verified-final |
| HTTP/SSE 接收事实 | sse_asgi_transport_truth_v284 | Broker completed 不等于客户端完整接收 |
| 媒体字节身份 | MediaArchive / generated-file archive | 不得通过数量、URL 顺序猜测资产身份 |

### 3.2 Chat Completions 的流式语义

当前 Chat Completions SSE 路径会发送心跳/状态和经过允许的 reasoning preview；canonical assistant content 由 Worker 的 chat.completed 终态确定，先执行归档与 Telemetry 持久化，再把完整权威文本分成有限 SSE chunk 输出，最后输出 usage/meta 和 [DONE]。

这是一种**终态确认后再交付最终答案内容**的安全策略，不等同于常见的逐 token assistant content streaming。该策略保护了终态、媒体归档和内容一致性，但会影响首个真实答案 token 的延迟和部分 SDK 的体验。

Responses API 则读取 chat.response.snapshot 并尝试按前缀增量输出。两条协议适配器的流式行为不同，必须分别形成契约，不能用一个“支持 stream=true”概括。

### 3.3 Broker 与 Worker 的进程边界

- RequestBroker.requests、client_requests 和请求 Future/Queue 都是内存对象。
- ClientRegistry.sockets、socket_locks、busy_clients 都是进程内对象。
- ClientRegistry.clients 的设备元数据写入 clients.json，但 WebSocket 连接本身不持久化。
- Dockerfile 默认只启动一个 Uvicorn 进程；compose 当前也是单服务配置。

因此当前部署是**单进程协调模型**。如果直接增加 Uvicorn workers 或多个 API 容器，HTTP 请求可能落到没有对应 Worker WebSocket/Broker 状态的进程，出现无法路由、请求状态丢失或重复调度。

Phase 0 建议明确两个选项：

A. MVP 保持单进程/单实例，并增加启动保护、部署文档和健康检查，禁止无意多 worker。  
B. 正式多实例前引入共享数据库、Redis/消息总线或等价协调层、Worker 连接归属路由、跨实例取消/恢复和分布式租约。

不建议在没有架构设计的情况下仅通过 Docker replicas 或 Uvicorn --workers 扩容。

---

## 4. 数据存储与密钥审计

### 4.1 当前数据存储分布

| 数据 | 当前存储 | 关键限制 |
|---|---|---|
| Worker 注册元数据 | clients.json | 文件式持久化；WebSocket 仅内存 |
| API Key | api_keys.json | hash + 可恢复密文；认证会更新 last_used 并保存整个文件 |
| 请求历史 | request_history.jsonl + 内存 deque | Telemetry 内存窗口为 500；请求记录不等于 Conversation |
| 上传文件 | files/index.json + 文件字节 | owner 目前以 API Key ID 为主，不是 user/tenant/conversation |
| 媒体归档 | media_archive/index.sqlite3 + 对象文件/S3 | SQLite 元数据和对象字节分离；settings.enc 密钥问题见 P0-01 |
| 生成文件 | GeneratedFileArchive / 媒体归档数据库及 staging | 有独立的文件生命周期和分块校验 |
| 原生内容 | native_content/{request_id}.json | 按请求持久化的结构化副轨 |
| 用户账户与 Session | user_accounts.json | 用户和 Session 共用 JSON Store |
| 模型价格 | user_pricing.json | 与 Worker 实际可用模型目录是不同来源 |
| 余额/账单/订单 | user_billing.json | 进程锁 + 文件持久化，不是统一交易数据库 |
| 支付密钥配置 | user_payment_config.json | 使用 data_secret 派生密钥加密 |
| 支付渠道配置 | user_payment_channels_v106.json | installer 读取 .chat2api_data_secret；缺失时 fallback 到 settings.api_key 或固定值 chat2api；不是 data_secret.key |
| 管理员 Session | admin_sessions.json（Docker/配置数据目录） | 保存 Token hash 和过期时间 |
| Playground 测试运行 | test_runs.jsonl | 不是用户聊天会话数据库 |
| Worker 探针策略/证据 | worker_probes/ | Worker 工具策略和运行证据，不是通用插件注册表 |
| Responses response store | 内存 OrderedDict | 最多 256 条，重启后不可恢复 |

### 4.2 P0-01：MediaArchive 密钥迁移不完整

代码证据：

- app/main.py 在生产 installer 链执行前初始化 MediaArchive，并传入 config.api_key。
- app/media_archive_v173.py 使用 SHA256("chat2api:media:settings:v1:" + master_secret) 派生 Fernet 密钥；密文位于 media_archive/settings.enc。
- app/config.py 将 CHAT2API_API_KEY 标记为 deprecated migration input，且默认值为空。
- app/v17_crypto_patch.py 只迁移 ApiKeyStore.token_ciphertext，没有迁移 MediaArchive._cipher/settings.enc。
- app.v17_patch.py 后续会调整 Settings.api_key，但这不会改变 MediaArchive 已捕获的 cipher。
- MediaArchive._config() 解密失败时会抛出 RuntimeError，不会自动回退。

影响：

1. 新安装若 CHAT2API_API_KEY 为空，派生 key 仍是可预测的固定值。
2. 旧环境变量被移除或改变后，已有 settings.enc 可能无法解密。
3. 媒体 S3 凭据并未跟随 API Key ciphertext 的 v17 migration。
4. 空 CHAT2API_API_KEY 的启动告警发生在 v17 installer 可能修改 Settings 后，不能可靠反映 MediaArchive 实际捕获的原始密钥。

建议：MediaArchive 迁移到唯一 DataKeyring；对旧 settings.enc 进行显式 dry-run、备份、解密、用途隔离重加密、原子替换和回读验证。不能解密时必须 fail closed，不得清空设置或回退到 local backend。旧 key 兼容窗口和回滚备份须保留，直到完整发布周期和人工验收完成。

### 4.3 P0-02：PaymentChannelsStore 使用独立且可预测的密钥来源

代码证据：

- app/user_console_v104_patch.py 调用 load_or_create_data_secret(data_dir)，为 API Key migration 与 PaymentConfigStore 提供 data_secret.key。
- app/payment_channels_v106_patch.py 的生产 installer 没有调用 load_or_create_data_secret，而是读取 data_dir/.chat2api_data_secret。
- 该文件读取失败时，installer 使用 settings.api_key or "chat2api"；当前 Settings.api_key 默认值为空，因此新安装走固定字符串 chat2api。
- PaymentChannelsStore.__init__ 在直接收到空字符串时还会 fallback 到 chat2api-payment-v106。
- PaymentChannelsStore._decrypt 在 InvalidToken 时返回空字符串，而不是报告 key mismatch。
- tests/test_payment_channels_v106.py 直接用 local-secret 构造 Store，没有覆盖真实 installer 的 key source 或迁移。

影响：

1. PaymentChannelsStore 与 ApiKeyStore / PaymentConfigStore 使用不同根密钥文件和派生逻辑。
2. 新安装的支付商户密钥可能使用公开固定值 chat2api 加密，不构成安全的 at-rest secret protection。
3. 如果旧 .chat2api_data_secret 丢失或内容变化，支付配置可能在后台静默显示未配置。
4. 本次没有访问生产数据卷，无法确认现有 .chat2api_data_secret 是否存在、由哪个旧版本创建或与生产 ciphertext 匹配；不得在生产迁移中猜测。

建议：PaymentChannelsStore 纳入 DataKeyring，先用明确的 LegacyKeyResolver 支持 .chat2api_data_secret、旧 CHAT2API_API_KEY 和已知生产 fallback 候选；只有解密和 schema 校验都成功才认定候选。任何非空 ciphertext 解密失败必须显式报错，不能返回空值。新写入禁止使用 fallback 常量。

### 4.4 P1-11：根密钥丢失与解密失败可能被静默处理

- load_or_create_data_secret 在 key 文件不存在或内容为空时直接生成新 key，没有先检查 api_keys.json、user_payment_config.json 等是否已有密文。
- PaymentConfigStore.secret_key() 与 PaymentChannelsStore._decrypt() 在解密失败时返回空字符串。
- ApiKeyStore.migrate_cipher_from 对不能由 legacy key 解密的记录选择跳过，没有统一迁移结果报告；API Key hash 认证仍可能正常，但 secret reveal 不再可用。
- load_or_create_data_secret 使用固定 data_secret.tmp 且没有跨进程初始化锁；这与当前单实例部署相符，但不能作为未来多进程启动的安全实现。

建议：首次空数据目录可以生成 key；只要检测到已有加密资产但 key 缺失/损坏，就 fail closed 并要求恢复备份。迁移必须输出仅含计数和错误类别的报告，不能记录明文或密钥。

### 4.5 API Key 生命周期

当前 ApiKeyStore 同时保存 token_hash 和 token_ciphertext；管理员/用户接口存在读取 secret 的 endpoint。撤销时会清除 token_ciphertext。v17_crypto_patch 已把旧 managed Key 密文迁移到 data_secret.key，但新 Key 仍是可恢复密文模式。

这不是新版目标的 hash-only / one-time reveal。迁移必须兼容现有用户：

1. 先保留旧 Key 鉴权哈希和 owner/scopes/quota。
2. 新 Key 创建只展示一次，不再保存可恢复密文。
3. 提供轮换流程，允许用户短暂并行使用新旧 Key。
4. 用户完成轮换后再删除旧 ciphertext 和 secret reveal endpoint。
5. 不得在迁移时批量无预警吊销用户已有 Key。

此外，ApiKeyStore.account_completed_usage 在请求 completed 后才增加 tokens_used。authenticate 只检查当前已记账余额，没有请求 admission 阶段的原子预留。并发请求可同时通过额度检查，最终用量超过额度后才自动禁用 Key。当前 quota 测试覆盖的是顺序计量，不证明并发额度不会超发。

### 4.6 账户、账单和支付

已有 UserAccountStore、PricingStore、BillingStore、PaymentConfigStore、PaymentChannelsStore。它们是 #784 应复用的现有资产。

需要补足：

- UserAccountStore 与 ApiKeyStore 是两个独立文件；创建 Key 与 attach_key 是两个写入步骤，失败可能留下孤立 Key。
- BillingStore 以 request_id 幂等，但按完成请求计费，当前 usage 来源为本地估算；没有 admission reserve/settle/release 的事务闭环。
- BillingStore 在 billing_enabled 时直接扣余额，没有看到余额不足时的预留拒绝逻辑；余额可能变为负数，是否允许后付费必须成为显式产品策略。
- 用户费用恢复同步只遍历 Telemetry 的 recent(500)。若 Telemetry 写入成功后、BillingStore 记录前进程崩溃，且该请求在恢复时已不在最近 500 条中，可能无法自动补记。
- 价格快照已被保存，这是正确方向；历史账单不能被新价格覆盖。
- 支付通知需要继续保持签名验证、订单状态幂等和金额校验。

---

## 5. 身份与管理控制面

### 5.1 已存在的管理员认证

本轮纠正旧版 #784 的一个错误判断：AdminSessionStore 确实已经进入生产安装链。

- app.entry 安装 app.v17_patch。
- app.v17_patch 创建 AdminSessionStore，并设置 app.state.admin_sessions。
- 提供 /api/admin/auth/login、/api/admin/auth/session、/api/admin/auth/logout。
- 通过 chat2api_admin_session Cookie 进行管理员登录。
- 管理员 Session Token 以 SHA-256 hash 持久化，测试覆盖容器重启后的恢复和登出撤销。
- middleware 对 /api/admin/* 和 /api/clients 执行 Session gate，并为旧依赖注入内部兼容 token。
- admin_v17.js 替换旧管理 UI 的 master-key transport，显示管理员登录界面。

因此不应再次把“安装管理员 Session”列为从零实现。

### 5.2 仍然存在的管理缺口

- 当前是单管理员账号/密码配置，不是 AdminUser 目录。
- 未发现完整的 Role、Permission、AdminMembership 或 RBAC 授权矩阵。
- 未发现完整的管理员用户列表、创建、停用、邀请和角色管理 endpoint。
- Settings.admin_password 默认值为 change-me-admin；若部署没有显式覆盖，会落入弱默认密码。
- 管理员登录路由未发现速率限制/失败退避实现。
- cookie 使用 HttpOnly、SameSite=Lax；仍需为所有 cookie-auth 修改请求明确 CSRF/Origin 策略，并验证可信反向代理头。
- 当前所有管理员 Session 本质上拥有同一管理员权限，不具备职责分离。

### 5.3 用户身份

已有用户邮箱注册/登录、密码哈希、Session Token hash 和 API Key ownership 关系。当前注册接口会直接创建用户并登录，未发现开放注册/邀请/关闭注册策略或邮箱验证门禁。

用户、API Key 和 Worker 三种身份必须继续分离。新模型需要 tenant/workspace、membership、resource ownership 和 policy revision；不能仅把 user_id 填进 API Key metadata 就当作多租户隔离。

---

## 6. API 兼容性与 Responses 适配器

### 6.1 Chat Completions

ChatCompletionRequest 明确声明 model、messages、stream、client_id、prompt_mode、timeout、attachments、reasoning_effort、reasoning、max_completion_tokens 和 max_tokens，并设置 Pydantic extra="allow"。

但 /v1/chat/completions 构造 Worker chat.request 时，options 主要只传 auto_switch_text、timeout_seconds、model 和 probe_policy。当前没有看到把 temperature、top_p、stop、n、presence_penalty、frequency_penalty、seed、response_format、tools、tool_choice 等字段转换成 Worker 可执行语义。

即使 max_tokens、max_completion_tokens、reasoning_effort 被 schema 接受，也不能据此证明它们已经进入 Worker options。

**结论：** 当前应声明“已实现并验证的子集”，不能宣称完整 Chat Completions 兼容。对未支持字段应显式拒绝或记录有文档的降级，避免静默忽略。

### 6.2 Files

POST /v1/files 接收 FileUploadRequest JSON，其中 data_base64 是必填字段；这是一套自定义上传协议，不是完整的 multipart UploadFile 路径。不能据此宣称官方 OpenAI SDK Files 上传兼容。

当前文件 owner 主要绑定 owner_key_id。新 Web Chat 需要 conversation_id、message_id、user_id、tenant_id 和 retention state 的资源关联。

### 6.3 Images

ImageGenerationRequest 当前固定 model 为 gpt-image，n 的最大值为 1，并支持 size 与 url/b64_json 输出。这是明确的有限子集，应写入能力矩阵。

### 6.4 Responses API

- /v1/responses 与 /v1/responses/{response_id} 均在生产安装链注册。
- Responses 有自己的 _authenticate、_dispatch、_collect、_stream 和 response object adapter。
- Responses _dispatch 自己选择 Worker、创建 Broker state、发送 chat.request；没有复用一个统一 Chat Request Service。
- Responses usage 使用本地 usage_for 估算。
- response store 是 OrderedDict，最多 256 条，进程重启后丢失。
- 当前 v108 原生工具配置识别 web_search，并通过 responses_image_tool_v182 处理 image_generation；其他外部工具类型会返回 responses_external_tools_pending。
- ResponsesModelContextMiddleware 负责模型上下文与 Telemetry 观察，不能因此把它视为通用 Plugin Registry。

**P1-12：生产路由注册存在重复 owner。** app/v13_patch.py 在 entry.py 的早期安装阶段注册 POST /v1/responses；app/responses_v108_patch.py 在后续阶段再次注册同一路径。当前没有发现 v108 installer 移除/替换 v13 route 的逻辑。Starlette Router 对重复路由按注册顺序进行 first-match，较早的 v13 route 会先匹配。v13 handler 明确拒绝 body.tools，因此 v108 的 web_search / image_generation handler 不会通过这条生产入口触发；v108 的 GET retrieve route 也无法读取 v13 handler 未写入的 v108 store。responses_model_routing_v108_patch 是 ASGI middleware，它设置上下文、观察响应并记录 Telemetry，但随后仍调用下层 Router，并不会改变 route matching owner。

现有 tests/test_responses_native_bridge_v108.py 在单独新建的 FastAPI app 上只安装 v108 patch；没有验证完整 app.entry:app 中 POST /v1/responses 的唯一 owner，也没有端到端验证 native tools 和 response retrieve。

**必须补充的生产回归：**
1. 对 app.entry:app 枚举 POST /v1/responses，断言只有一个最终 route owner。
2. 通过生产 ASGI app 发起 web_search tool 请求，确认走 v108 tool handler，而不是 v13 的 unsupported tools 分支。
3. 通过生产 ASGI app 创建 response 后，使用同一 principal GET /v1/responses/{response_id}，确认 owner scope 和 retrieve 成功。
4. 测试 streaming / non-streaming 两种路径，确认 telemetry、billing 和 request_id 只记录一次。

**建议：** 抽取 ChatRequestService，负责 admission、principal、model resolution、Worker dispatch、request_id、Broker state 和 terminal handle。Chat Completions、Responses 和 Web BFF 只做协议转换，不再各自实现一套请求调度。并在 v108 统一注册/替换旧 route，保证每个 public method/path 只有一个明确 owner。

---

## 7. Web Chat 与模式 1 的现状

### 7.1 Web Chat

当前 user console 有 Dashboard、API Keys、Requests、Profile、Billing、Models、Playground、Docs 等模块。Playground 是单次 API 测试界面，不能替代多轮聊天产品。

本轮未发现：

- Conversation / Message 持久化模型。
- Web Chat 独立前端工程。
- /api/client/* BFF。
- 会话列表、历史、搜索、重命名、删除和归档 API。
- 稳定 message_id / sequence / request_id 关联。
- SSE resume cursor 或 request replay guard 的 Web Chat 产品接口。

desktop_client 是单独的 Python 客户端目录，不是浏览器 Web Chat。

### 7.2 模式 1

本轮未发现用户面向的官方网页 reverse proxy ingress、ProxySession Store、上游 Host/Path allowlist、Cookie jar 隔离、CSP/HTML 资源重写或完整 HTTP/SSE/WebSocket 双向代理。

现有 linux_worker_proxy_catalog 等模块是 Worker 出站代理配置，不是官方 ChatGPT 网页反向代理。

模式 1 应保持独立子系统和默认关闭。不得把现有 Worker 的登录态或 Cookie 直接共享给多个最终用户。

---

## 8. 模型、插件和 Worker 工具

### 8.1 模型权威

model_capability_routing_patch 会根据当前请求模型上下文筛选兼容 Worker；model_observability_v144_patch 将最终模型证据写入诊断；worker_model_library_v145_patch 将经过验证的 Worker 模型库作为可选模型的主要权威。

但用户控制台模型展示主要由 PricingStore 的 enabled models 生成。价格目录和 Worker 当前实际可用模型不是同一个事实源，可能出现“价格配置启用但没有在线 Worker 能执行”的展示差异。

新版应把以下字段分开：

- catalog model
- priced model
- user-entitled model
- worker-advertised model
- validated/routable model
- requested model
- actual served model

### 8.2 插件

当前 worker_probe_store 和 worker-tools catalog/policies/runs 管理的是 Worker 可用工具与探针策略。未发现通用 Plugin Registry、PluginVersion、PluginSecretRef、PluginGrant 或 ToolExecution 权限系统。

不得把 Worker Tools 改名后直接宣称完成插件平台。插件执行必须有服务端授权、密钥隔离、网络 allowlist、执行超时、输入输出 schema、审计和副作用确认。

---

## 9. 部署、安全与版本治理

### 9.1 部署

- Dockerfile 使用 Uvicorn app.entry:app，未指定多个 workers。
- docker-compose 当前为单服务和数据目录挂载。
- Dockerfile 未声明非 root USER。
- requirements.txt 使用版本范围，不是 lock + hashes。
- 当前没有可证明多实例安全的共享 Broker/连接层。
- CORS 默认 allowed_origins 为 *，allow_credentials 为 false；若 Web App 使用独立子域和 Cookie Session，必须配置明确 Origin allowlist，不能依赖 wildcard。

### 9.2 管理员与用户安全

- 管理员 Session Token hash-only 持久化是现有优点，应保留。
- 默认管理员密码需要启动拒绝或明确强制轮换，而不只是 .env.example 提示。
- 管理员和用户登录都需要速率限制、失败退避和审计。
- Cookie-auth 修改请求需要明确 CSRF/Origin 策略。
- 用户注册需要开放/邀请/关闭三种策略。
- API Key 新版目标应为 one-time reveal + hash-only；当前可恢复 ciphertext 与 secret endpoint 必须有兼容退役计划。
- data_secret.key 与被加密数据位于同一个数据卷；生产多备份/云环境应评估 KMS 或外置 Secret Manager，不能把 chmod 0600 当作密钥与数据分离。

### 9.3 版本漂移

当前代码契约：

- Python package / pyproject / app.__version__：0.7.1。
- Server Runtime：0.22.291。
- Chrome manifest 与 Bridge Bundle：0.22.291。
- runtime_contract 测试显式断言 0.22.291。

但 docs/VERSIONING.md 仍出现 0.22.173，README 仍出现 0.22.163。当前测试只验证 README 描述版本契约，不校验文档中的具体当前版本号。

建议由 runtime_contract.py 作为唯一版本源，并在 CI 中校验 README、VERSIONING、manifest、pyproject 和 package version。不要让文档手工维护另一份“当前版本”数字。

### 9.4 CI 证据

在本轮核验的 main SHA 上：

- CI 的 Tests job 通过，执行完整 pytest -q --timeout=120 及大量 Node VM/协议回归。
- Release、observe、execute 等 check run 有成功记录。
- GPTAuto reconcile 失败，日志显示它派发了 CI workflow_dispatch 后，在 90 秒注册窗口内没有发现与同一 SHA 对应的 workflow run，随后发布 post-merge recovery handoff。
- production-image-smoke/build check 在该提交的检查列表中为 skipped。

因此结论是“测试 job 通过，但 post-merge reconcile 未完成，且本次没有生产镜像 smoke 证据”，不是“全部发布验收完成”。

---

## 10. Phase 0 ADR 建议

以下为建议决策，尚待项目负责人确认；本报告不把建议伪装成已批准设计。

### ADR-001：单实例 MVP 与多实例扩展边界

建议第一阶段明确支持单进程单实例，并增加启动保护，拒绝不受支持的多 worker 配置。若要多实例，先完成共享数据库、Redis/消息总线、Worker WebSocket 归属路由和分布式请求租约。

### ADR-002：统一数据密钥与加密配置迁移

详见 [ADR-002：统一数据密钥与加密配置迁移](adr-002-versioned-data-encryption-keyring.md)。该 ADR 仍为 Proposed，重点处理 MediaArchive 与 PaymentChannelsStore 的 P0 密钥来源分裂，以及 key loss fail-closed、legacy resolver、备份/回滚和完整 readback 测试。

### ADR-003：共享 ChatRequestService

建议将 Worker admission、model resolution、Broker create/send、request correlation 和终态 handle 抽取为共享服务。Chat Completions、Responses、Web Chat BFF 作为 protocol adapters。

### ADR-004：Mode Resolver 与独立 Proxy Plane

建议标准 /v1/* 永远默认 mode2。mode1 只通过专用 Host/入口进入；建立 ProxySession Manager、上游 allowlist、DNS/IP 约束、Cookie jar 隔离和长连接资源治理。模式 1 默认关闭，先做单用户技术 Spike。

### ADR-005：统一用户/管理员/租户身份

保留当前 UserAccountStore 和 AdminSessionStore 的密码/Session 能力；增加 Tenant、Membership、Role、Permission、AdminUser、API Key owner 和资源 owner 的统一模型。Admin Session 不重写，但要增加 RBAC 和高风险操作审计。

### ADR-006：控制面数据库迁移

单实例 MVP 可先使用 SQLite WAL + schema migration + 事务；多实例正式启用前转 PostgreSQL。迁移先 dry-run、计数核对、余额/订单校验、Key ownership 校验和备份回滚。不要长期双写两个真值源。

### ADR-007：OpenAI 兼容性契约

建立逐字段兼容矩阵。没有执行语义的参数要显式拒绝或标记 unsupported；Files 先决定 multipart 标准入口；Responses response store 要决定持久化范围；Usage 明确区分 estimated 与 authoritative。

### ADR-008：Web Chat 产品层

新增 Conversation/Message/Attachment Store、用户 Session BFF、SSE cursor/resume、消息 sequence、request/message 关联和安全 Renderer。前端不得保存服务端密钥，也不得通过自 HTTP 调用 /v1 重新创建一套请求生命周期。

### ADR-009：插件执行边界

初始只支持内置/审核过的声明式插件或隔离远程执行；不允许任意代码在主 FastAPI 进程直接执行。PluginGrant 必须按 user/tenant/role/action 授权。

---

## 11. Phase 0 退出条件与下一步顺序

Phase 0 不以“读完代码”作为结束，而以以下交付为退出条件：

- [ ] 生产 app.entry 最终路由与 middleware 清单。
- [ ] 所有核心状态 owner / wrapper / evidence-only / projection 矩阵。
- [x] 完成 DataKeyring / legacy key migration 的详细 ADR 提案（待评审）：[ADR-002](adr-002-versioned-data-encryption-keyring.md)。
- [ ] 确认生产数据卷中 legacy key 文件与 ciphertext 的实际存在状态（仅收集脱敏文件清单/哈希，不收集密钥）。
- [ ] 批准 DataKeyring 设计后实现迁移服务、dry-run、备份、apply、readback 和 rollback 测试。
- [ ] 用户、管理员、Worker、API Key、Proxy Session、tenant 身份图。
- [ ] JSON/JSONL/SQLite/内存 Store 的 schema 和迁移清单。
- [ ] Chat Completions / Responses / Files / Images 的逐字段兼容矩阵。
- [ ] 模式 1 上游身份来源与合法授权可行性结论。
- [ ] 单实例/多实例架构决策。
- [ ] Web Chat BFF 和 Conversation 数据模型。
- [ ] RBAC 与 Plugin Registry 的授权模型。
- [ ] 安全威胁模型和测试矩阵。
- [ ] 真实 Chrome E2E 技术方案。

### 建议实施顺序

1. **先处理 P0-01 / P0-02：** MediaArchive 与 PaymentChannelsStore 的旧密钥来源分裂，避免弱密钥保护或旧密文不可恢复。
2. **再完成 ADR-001/002/005/006：** 身份、密钥、存储和部署边界。
3. **然后完成 ADR-003/007：** 抽取共享请求服务并固化 API 兼容契约。
4. **随后完成 ADR-004：** 模式 1 单用户代理 Spike，默认关闭。
5. **最后进入 Web Chat 和客户端管理控制面实现。**

在上述退出条件通过前，不建议重写 app.main、request-v6、Broker 或 Chrome Worker 主控制器。

---

## 12. 旧 Issue 清理核对

截至本次核对，仓库共有 4 个 Open Issue：

- #585：现有请求/Worker/SSE/媒体/原生富内容总验收入口，保留。
- #784：新版专项需求入口，保留。
- #648：独立原生页面诊断工具任务，仍有专用分支。
- #654：Windows 原生页面诊断与 Worker ID 任务，仍有专用分支。

已核对以下历史重复/旧追踪 Issue 均为 Closed：#322、#482、#554、#557、#558、#560、#566、#568、#580。

#648 与 #654 不是 #784 v2.0 的重复需求，也没有被本专项正文完整替代；它们属于 #585 的诊断工作流。关闭它们意味着放弃或迁移独立诊断任务，不应误标为“已被 #784 完整覆盖”。如项目负责人决定放弃这两项工作，应先在 #585 留存剩余要求和分支证据，再明确关闭原因。

---

## 13. 本报告引用的主要代码位置

所有链接固定到本报告审计 SHA，避免 main 后续变化导致证据漂移。

- [生产入口 app/entry.py](https://github.com/b8vipvip/chat2api/blob/4ba1113e9b84b902453d785860b923906b80e9f2/app/entry.py)
- [基础应用 app/main.py](https://github.com/b8vipvip/chat2api/blob/4ba1113e9b84b902453d785860b923906b80e9f2/app/main.py)
- [API 请求模型 app/models.py](https://github.com/b8vipvip/chat2api/blob/4ba1113e9b84b902453d785860b923906b80e9f2/app/models.py)
- [API Key Store](https://github.com/b8vipvip/chat2api/blob/4ba1113e9b84b902453d785860b923906b80e9f2/app/api_keys.py)
- [管理员认证安装器](https://github.com/b8vipvip/chat2api/blob/4ba1113e9b84b902453d785860b923906b80e9f2/app/v17_patch.py)
- [管理员 Session Store](https://github.com/b8vipvip/chat2api/blob/4ba1113e9b84b902453d785860b923906b80e9f2/app/admin_auth.py)
- [配置定义](https://github.com/b8vipvip/chat2api/blob/4ba1113e9b84b902453d785860b923906b80e9f2/app/config.py)
- [MediaArchive](https://github.com/b8vipvip/chat2api/blob/4ba1113e9b84b902453d785860b923906b80e9f2/app/media_archive_v173.py)
- [数据密钥与 API Key 迁移](https://github.com/b8vipvip/chat2api/blob/4ba1113e9b84b902453d785860b923906b80e9f2/app/v17_crypto_patch.py)
- [用户账户/计费 Store](https://github.com/b8vipvip/chat2api/blob/4ba1113e9b84b902453d785860b923906b80e9f2/app/user_commerce.py)
- [用户控制台 installer](https://github.com/b8vipvip/chat2api/blob/4ba1113e9b84b902453d785860b923906b80e9f2/app/user_console_v104_patch.py)
- [Responses API](https://github.com/b8vipvip/chat2api/blob/4ba1113e9b84b902453d785860b923906b80e9f2/app/responses_v108_patch.py)
- [Responses model context middleware](https://github.com/b8vipvip/chat2api/blob/4ba1113e9b84b902453d785860b923906b80e9f2/app/responses_model_routing_v108_patch.py)
- [Worker model library](https://github.com/b8vipvip/chat2api/blob/4ba1113e9b84b902453d785860b923906b80e9f2/app/worker_model_library_v145_patch.py)
- [Runtime contract](https://github.com/b8vipvip/chat2api/blob/4ba1113e9b84b902453d785860b923906b80e9f2/app/runtime_contract.py)
- [Runtime contract tests](https://github.com/b8vipvip/chat2api/blob/4ba1113e9b84b902453d785860b923906b80e9f2/tests/test_runtime_contract.py)
- [CI workflow](https://github.com/b8vipvip/chat2api/blob/4ba1113e9b84b902453d785860b923906b80e9f2/.github/workflows/ci.yml)

**状态：** Phase 0 静态代码审计第一轮完成；生产代码未修改。下一步是评审本报告中的 ADR，优先解决 P0-01，并补齐最终运行时拓扑与部署验证。
