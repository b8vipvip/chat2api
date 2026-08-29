# chat2api 开发进度与交接上下文

> 最后同步：2026-08-29（北京时间）  
> 仓库：`b8vipvip/chat2api`  
> 默认分支：`main`  
> 功能代码基线：`daa100814413b0625391d4fd6ad1213e47686cdf`（PR #156 合并提交）  
> 用途：供新的 ChatGPT / Codex 开发会话直接读取并继续开发，避免重新推断项目历史、版本关系和已解决问题。

---

## 1. 当前仓库状态

项目仓库：`https://github.com/b8vipvip/chat2api`

当前生产架构入口：

```text
app.entry:app
```

当前版本契约：

| 组件 | 当前版本 | 说明 |
| --- | --- | --- |
| Python package | `0.7.1` | `app/__init__.py` |
| Server Runtime | `0.22.35` | 控制台/服务端能力版本 |
| Worker/Chrome Bridge protocol | `0.8.1` | 兼容协议层版本 |
| Worker Bundle | `0.8.8` | Linux/浏览器 Worker 实际打包版本 |
| Linux Worker Agent | `0.3.6` | 有界远程控制 Agent |
| Worker initialize compatibility baseline | `0.22.24` | 支持 initialize/recovery 的最低服务端兼容基线 |

当前功能代码基线 `daa1008...` 的 GitHub Actions：

- CI #695：`success`
- Production image smoke #372：`success`
- Production image smoke 已不仅构建 Docker 镜像，还会真正启动生产 `app.entry:app` 并检查 `/version`。

当前仓库没有 GitHub Release 对象；正式交付仍以 `main` + Server Runtime / Worker Bundle 版本契约 + CI / Production image smoke 为准。

当前没有已打开的 PR，也没有已打开的 GitHub Issue。

---

## 2. 项目目标与边界

chat2api 的目标是把真实 ChatGPT 网页浏览器会话包装为可管理、可诊断、OpenAI-compatible 的 API 服务，而不是直接调用 OpenAI API。

核心原则：

1. 服务端负责鉴权、路由、请求生命周期、持久化、诊断、控制台和 Worker 编排。
2. Worker 负责运行真实 Chrome / ChatGPT 页面，并通过受控浏览器自动化完成模型选择、推理强度、附件、输入、发送和答案捕获。
3. Linux Worker 是长期运行的浏览器执行节点，不允许中心服务器把任意 shell 当作远程命令执行；Agent 只允许固定白名单命令。
4. API Key 与管理员登录彻底分离；业务流量只使用 managed API Key。
5. 请求必须保留唯一 `request_id`，即使同一个 API Key 同时发送完全相同的 Prompt，也必须是两个独立执行。
6. 失败必须可诊断，不能通过“为了测试通过”而隐藏真实外部调用错误。
7. 对 ChatGPT 页面结构变化采取 fail-closed、可恢复、可观测设计，不允许未验证状态直接发送。

---

## 3. 当前总体架构

```text
外部 OpenAI-compatible Client
        │
        │ Bearer managed API Key
        ▼
┌────────────────────────────────────────────┐
│ FastAPI Server / app.entry:app             │
│                                            │
│  Auth / API Key                            │
│  Model & reasoning normalization           │
│  Sticky routing / Free mini routing        │
│  Request lifecycle / telemetry             │
│  Broker / WebSocket transport              │
│  Files / images / voice / responses        │
│  Admin Console / Playground                │
│  Runtime logs / request diagnostics        │
│  Linux Worker control plane                │
│  Server update + Worker auto-sync          │
└───────────────────┬────────────────────────┘
                    │ WebSocket
                    ▼
┌────────────────────────────────────────────┐
│ Linux Worker Agent                         │
│                                            │
│ systemd services                           │
│ ├─ Xvfb                                    │
│ ├─ Chrome                                  │
│ ├─ Worker Agent                            │
│ ├─ optional Xray proxy                     │
│ ├─ extension autoreload/self-heal          │
│ └─ bounded online upgrade helper           │
└───────────────────┬────────────────────────┘
                    │
                    ▼
┌────────────────────────────────────────────┐
│ Chrome Extension / Worker Bundle           │
│                                            │
│ Background/service worker                  │
│ Content/page drivers                       │
│ Request ownership / route reservation      │
│ Model/reasoning selection                  │
│ Attachments                                │
│ Prompt submission                          │
│ DOM response capture                       │
│ Page-progress / transient retry            │
│ Runtime preflight / stale-tab recovery      │
│ External account tool isolation            │
└───────────────────┬────────────────────────┘
                    │
                    ▼
              chatgpt.com UI
```

### 服务端请求数据流

```text
/v1/chat/completions 等入口
  → managed API Key 鉴权
  → model / reasoning 归一化
  → API Key 粘性路由或模型专用路由
  → 为 request_id 分配 Worker / conversation route
  → Broker 创建 request state
  → WebSocket chat.request
  → Worker runtime preflight
  → 浏览器输入/附件/模型/推理控制
  → ChatGPT 页面生成
  → chat.delta / chat.snapshot / terminal event
  → 服务端 OpenAI-compatible SSE / JSON 响应
  → telemetry / request history / runtime logs
  → 释放 reservation / route quarantine / cleanup
```

---

## 4. 应用加载方式：历史补丁栈

生产入口不是简单使用 `app.main:app`，而是必须使用：

```text
app.entry:app
```

`app/entry.py` 从基础 `app.main` 创建的应用开始，按既定顺序安装历史版本补丁和当前最终控制层。安装顺序有语义，不应随意整理、合并或移动。

尤其注意以下顺序关系：

- request stall/recovery 后安装 Worker transport reconnect recovery；
- Playground lifecycle 后安装默认 multimodal sample，再安装 randomized prompt，最后安装 manual Playground chat；
- runtime contract 在历史补丁栈后安装，作为当前 Server Runtime 的 canonical owner；
- Linux Worker diagnostics → initialize → upgrade → routing toggle → console polling guard 有明确依赖顺序；
- server update 最后由 server-worker auto-sync 协调器衔接；
- Starlette 1.x 下 server-worker sync 通过 lifespan compatibility adapter 安装，不能重新改回已经移除的 `app.add_event_handler()`。

如果新开发修改 `app/entry.py`，必须首先确认不会破坏上述装饰顺序。

---

## 5. 当前主要模块

### 5.1 管理员控制台

管理员控制台包含：

- 概览
- API Key
- 请求记录与诊断
- 开发文档
- 模型广场
- Linux Worker 管理
- Runtime logs
- 测试场 / Playground
- Server GitHub 在线更新

管理员身份和业务 API Key 已分离。管理员使用账号密码 + HttpOnly session cookie；业务 API 使用 managed API Key。

管理员 session 在官方 Docker 数据目录中持久化时只保存高熵 token 的 SHA-256 fingerprint 和过期时间，不保存 cookie 原文，因此容器替换后可继续保持登录。

### 5.2 Worker / 设备码模型

用户可见术语已经统一为：

- `Worker`
- `设备码`
- `设备标识`

历史 wire/storage 名称 `client_id`、`pairing_id`、`/api/extensions` 等仍保留作为兼容层，不能因为 UI 已改名就直接删除。

持久 `device_id` 由浏览器端保存；同一个设备码不能被不同 `device_id` 抢占。

### 5.3 API Key 粘性路由

普通付费模型：同一 API Key 优先继续使用既有 Worker；旧 Worker 离线/禁用才迁移。

`gpt-5.5-mini` 是模型专用路由：优先 Free ChatGPT Worker；该 mini 路由不能污染普通付费模型的 API Key 粘性绑定。

同 API Key 并发请求使用独立 request_id 和独立 conversation worker reservation；完全相同的请求体也不能错误复用同一个正在执行的浏览器 Route。

### 5.4 模型与推理

公开文本模型目前包括：

- `gpt-5.6-sol`
- `gpt-5.5`
- `gpt-5.5-mini`

对 `gpt-5.6-sol` / `gpt-5.5`：

```text
low    -> 极速
medium -> 中
high   -> 高
```

省略 reasoning 时默认归一为 `medium`，不得继承页面当前值。

Free 原生 `gpt-5.5-mini` 不操作不存在的模型 family / reasoning 控件。

### 5.5 Linux Worker

Linux Worker 一般由中心提供 bootstrap 安装：

```text
/bootstrap/linux-worker.sh
```

关键目标是保持：

- Worker identity
- proxy 配置
- Chrome profile
- ChatGPT 登录状态

在线更新使用固定 helper 和白名单 `upgrade_worker`，Agent 不允许任意 shell。

当前 Agent `0.3.6` 调用 root-owned upgrader：

```text
/usr/local/sbin/chat2api-worker-upgrade --schedule
```

历史 sudoers 损坏问题已经修复，bootstrap 会在原子安装前验证 sudoers，并可覆盖 chat2api 自己生成的损坏 fragment。

### 5.6 Server 在线更新与 Worker 自动同步

Docker Web 容器不直接控制 Docker socket。Server update 写持久化更新请求，由宿主机固定 systemd/path helper 执行事务式更新。

部署顺序：

```text
GitHub 更新请求
  → 宿主机拉代码/构建/替换容器
  → 新容器健康检查
  → deployment.json 记录前后 SHA
  → server-worker sync 判断是否存在 Worker payload 变化
  → 按需更新 Linux Worker
```

规则：

- 健康检查成功前绝不更新 Worker；
- server-only 改动不会重复安装已经最新的 Worker；
- Worker 落后时即使本次 server diff 不改 Worker，也会补齐目标版本；
- Worker payload 有变化时，即使开发者忘记 bump bundle version，也会要求 refresh；
- 离线 Worker 标记 pending，上线后继续同步；
- 过老且缺失安全 update helper 的 Worker 标为 `manual-repair-required`，不降级为远程任意命令。

---

## 6. 最近已经完成的关键修复（PR #142 ～ #156）

### PR #142 — 管理员 Session 跨更新持久化

已完成：

- Docker 容器替换后管理员 session 可恢复；
- 只保存 token hash，不持久化原始 cookie；
- 原子写文件和权限收紧；
- logout / expiry 跨重启生效。

### PR #143 — Server update polling 登录恢复

已完成：

- update status polling 同时覆盖晚期 `fetch` 和早期捕获的 `api()` helper；
- 401/403 时不再永远卡在旧进度，而是回登录入口；
- 后续正常更新可跨容器继续 76% → 88% → 100%。

### PR #144/#145/#146 — Linux Worker 控制台卡死

已确认根因并完成三层修复：

1. 删除重复独立网络 polling / render ownership；
2. Linux Worker 关键列表使用 bounded XHR，加入 snapshot fallback；
3. 修复 `admin_linux_worker_enable_v46.js` 全局 MutationObserver 自触发死循环。

当前 UI 应只由一个稳定刷新 owner 控制列表；DOM 写入要求 idempotent。

### PR #147 — Worker WebSocket 短暂断线请求恢复 + 设备标识

已完成：

- in-flight Worker WebSocket disconnect 增加 8 秒 reconnect grace；
- MV3 terminal event durable outbox，socket 恢复后可重放 terminal event；
- 短暂网络抖动不再立即把已提交到 ChatGPT 的请求判为失败；
- 请求记录显示对应设备标识；
- UI 术语统一到 Worker / 设备码。

### PR #148 — Linux 响应捕获停滞 + 外部插件隔离

已完成：

- per-document Worker Bundle marker，防止长期 warm tab 伪装成新版本；
- 每个 browser dispatch 前 runtime preflight；
- stale/incomplete tab 强制 reload 后再验证；
- passive DOM response stream recovery；
- plugin/connector/connected-app UI fail-closed 隔离；
- API 原始用户 Prompt 不被修改，browser-only 加入 no-account-tools 执行规则。

### PR #149 — sudoers 损坏与在线升级恢复

真实故障根因已确认：initialize patch 的字符串替换误命中 sudoers heredoc 中的 `systemctl restart`，把 `/etc/sudoers.d/chat2api-worker` 拆成非法多行，导致 `sudo -n` 失败和 `upgrade_schedule_failed`。

已完成：

- 只 patch standalone restart command；
- sudoers temp validate + atomic install；
- bootstrap repair 可覆盖损坏 fragment；
- 在线更新返回 scheduler `detail`，不再只显示泛化错误；
- schedule/helper 问题提供幂等 repair bootstrap。

### PR #150 — Playground vision/file 无附件被跳过

已完成：

- 未手动选择附件时，vision 自动生成确定性 PNG；
- file 自动生成确定性 UTF-8 sample；
- 两者仍走真实 `/v1/files` + `/v1/chat/completions` 路径；
- 自动 sample 不泄漏到同一 `all` run 后面的 image-generation case；
- 每次仍分配真实 request_id。

### PR #151 — 浏览器生成停滞与同内容并发

已完成：

- response-stream recovery 升级为 page-progress v49；
- assistant DOM 可作为 authoritative snapshot；
- 15s 无回答进度发 diagnostics；
- idle 无答案约 25s、非 idle 中间态约 45s、仍显示生成控件约 120s 后给明确 UI-stuck，而不是盲等统一 150s；
- heartbeat 改为 diagnostic-only，不再错误续租“真实生成进度”；
- 同 Key 同 Prompt 的同时请求在 await tab allocation 前同步 reservation，确保分开执行。

### PR #152 — 请求终态 Route 竞态 + ChatGPT 临时错误自动重试

已完成：

- 已路由 tab 的旧 content controller 未释放时，返回明确 busy-controller 错误；
- terminal request 在 reservation 释放前同步进入 route quarantine；
- 成功 route 等 controller 真正 idle 后才复用；
- failed/cancelled 根据保存的 ownership 回收；
- 只对明确 transient UI（超时、网络错误、Something went wrong + Retry 按钮）自动 retry；
- 同 request_id 最多 2 次；
- context limit / account / quota / plugin / connector 明确不自动重试；
- Linux extension autoreload helper 缺失时，可从中心 SHA-256 verified Worker Bundle 自恢复。

该 PR 把版本推进到当前：Server `0.22.35` / Worker Bundle `0.8.8`。

### PR #153 — 自动测试 Prompt 随机化

自动测试每一次真正发出的 Prompt 都随机变化，但测试语义保持一致。

已完成：

- text：算术 / 改写 / 奇偶 / 微型解释；
- vision：主体 / 主色 / 构图；
- file：摘要 / 目的+事实 / 主题+关键词；
- image：多个场景 / 参考图多种编辑意图；
- 每个请求生成唯一 `PG-...` marker；
- 报告记录 `prompt_id`、`prompt_variant`、`prompt_preview`；
- 随机化只作用于自动测试场，不改业务 API 用户 Prompt。

### PR #154 — Server 更新后自动同步 Linux Worker

已完成：

- server update 与 Worker update 串成完整自动流程；
- GitHub Compare 判断 Worker payload 是否变化；
- stale Worker 自动补齐；
- offline Worker pending；
- bounded retry；
- `/api/admin/server-worker-sync` 控制和状态接口；
- 持久状态 `linux-worker-auto-sync-status.json`。

### PR #155 — Starlette 1.x 生产启动兼容

真实生产镜像使用 FastAPI 0.141.x / Starlette 1.x 时，Starlette 已移除 `app.add_event_handler()`。

已完成：

- server-worker sync 改用 lifespan compatibility adapter；
- Production image smoke 真正启动 `app.entry:app`；
- 防止“Docker 能 build、应用 import/start 却崩”的回归。

### PR #156 — Playground 多轮聊天窗口

最新已完成功能。

测试场现在同时有：

```text
测试场
├─ 手动聊天对话
└─ 自动标准化测试
```

手动聊天能力：

- 真实调用 `/v1/chat/completions`；
- SSE 流式显示；
- `prompt_mode=full`；
- 最近最多 40 条成功 user/assistant 文本作为当前 tab 上下文；
- 模型选择；
- reasoning strength；
- managed Key / 手工 Key；
- 单轮最多 4 个附件；
- 每轮独立 `X-Chat2API-Request-ID`；
- 显示 request id / model / total latency；
- `Enter` 发送、`Shift+Enter` 换行；
- `sessionStorage` 保存当前 tab 的文本历史，不保存 API Key；
- “新建对话”清除本地上下文；
- 附件名只作为显示信息，实际用户 Prompt 仍保持输入原文；
- 失败轮次保留在 UI 中，但失败 user/assistant pair 不进入后续模型上下文。

该功能是 Server console-only，不要求 Worker Bundle bump；`/version.features.playground_chat_window=true`。

---

## 7. 关键文件索引

### 7.1 生产入口 / 版本

- `app/entry.py`  
  生产 ASGI 入口和补丁安装顺序。修改前必须理解 patch ordering。

- `app/main.py`  
  基础 FastAPI application、核心 API 和基础状态对象。

- `app/runtime_contract.py`  
  Server Runtime / Worker Bundle / feature revision / `/version` 的 canonical owner。

- `app/__init__.py`  
  Python package version。

- `docs/VERSIONING.md`  
  不同版本面的约定。

### 7.2 API / 数据模型 / 鉴权

- `app/models.py`  
  ChatCompletion、Files、Playground 等 Pydantic request model。

- `app/api_keys.py`  
  managed API Key 鉴权、可恢复密文相关逻辑。

- `app/timezone_utils.py`  
  北京时间 canonical helper。

- `app/test_runs.py`  
  Playground 测试 run 的持久状态。

### 7.3 管理员控制台

- `app/admin.py`  
  基础 console HTML/CSS/JS。

- `app/admin_v*.js`  
  历史控制台叠加层。

- `app/admin_linux_workers.js`  
  Linux Worker 主列表及稳定刷新所有权。

- `app/admin_linux_worker_*.js`  
  Worker pairing、proxy、upgrade、enable、stable table 等专项 UI。

- `app/request_device_identity_patch.py`  
  请求记录设备标识和 Worker/设备码术语层。

### 7.4 Playground

- `app/playground_lifecycle_patch.py`  
  自动测试 run lifecycle、真正业务 API 调用、取消和报告。

- `app/playground_multimodal_defaults_patch.py`  
  vision/file 无附件时的默认真实 sample。

- `app/playground_random_prompt_patch.py`  
  自动测试随机 Prompt 和 `PG-*` marker。

- `app/playground_chat_patch.py`  
  手动多轮聊天资产注入。

- `app/admin_playground_chat.js`  
  手动聊天窗口主体；SSE、上下文、Key、附件、request id、sessionStorage 都在这里。

### 7.5 请求稳定性 / 浏览器恢复

重点关注：

- `app/stream_keepalive_patch.py`
- `app/request_stall_patch.py`
- `app/request_recovery_patch.py`
- `app/worker_transport_recovery_patch.py`

Chrome Worker 侧重点文件/模块族：

- `chrome_extension/background.js`
- `chrome_extension/content_request*.js`
- `chrome_extension/content_completion*.js`
- `chrome_extension/content_page_driver_v22.js`
- `chrome_extension/content_request_stall_guard_v34.js`
- `chrome_extension/*response*`
- `chrome_extension/*page_progress*`
- `chrome_extension/*request_lifecycle*`
- `chrome_extension/*transient_retry*`
- `chrome_extension/*runtime_preflight*`
- `chrome_extension/*tool_isolation*`
- `chrome_extension/conversation_workers_v24.js`
- `chrome_extension/conversation_dispatch.js`

不要只修改 service worker 而忽略已经打开的长期 ChatGPT document；项目已经专门为 warm-tab/stale-module 问题加入 bundle marker、runtime preflight 和 hot-load/reload 机制。

### 7.6 Linux Worker

- `scripts/bootstrap_linux_worker.sh`  
  安装/升级主入口，必须保持幂等并保护 identity/proxy/profile。

- `scripts/linux_worker_agent.py`  
  Agent 基础实现。

- `scripts/linux_worker_agent_v43.py`  
  bounded initialize/recovery。

- `scripts/linux_worker_agent_v44.py`  
  当前 Agent `0.3.6`，加入固定 `upgrade_worker`。

- `scripts/linux_worker_upgrade.sh`  
  root-owned online upgrade helper。

- `scripts/linux_extension_autoreload.sh`  
  extension autoreload/self-heal helper；此前真实 Worker 曾缺失该文件。

- `app/linux_worker_upgrade_patch.py`  
  中心在线升级 API / 状态 / fallback。

- `app/linux_worker_initialize_patch.py`  
  initialize 与 bootstrap transformation。

- `app/linux_worker_diagnostics_patch.py`  
  Worker 诊断。

- `app/linux_worker_console_polling_patch.py`  
  Worker console polling guard。

### 7.7 Server 更新 / Worker 自动同步

- `app/server_update_patch.py`  
  Server GitHub update 控制面。

- `app/server_worker_sync_patch.py`  
  post-deployment Worker reconciliation 主逻辑。

- `app/server_worker_sync_lifespan_patch.py`  
  Starlette 1.x lifespan compatibility。

- `docs/SERVER_WEB_UPDATE.md`  
  Web 更新设计文档。

### 7.8 CI

- `.github/workflows/ci.yml`
- `.github/workflows/production-image-smoke.yml`

CI 包括 Python compile、浏览器/控制台 JS syntax、Worker installer syntax、多个 Node VM contract 和 pytest 全量。

Production smoke 负责构建最终 Docker image、验证 bootstrap/Worker bundle，并真实启动 production ASGI entrypoint。

---

## 8. 已确认并关闭的真实问题

以下问题已有真实日志/现场证据，不能在以后再次当作“未知根因”从头猜：

1. **Linux Worker 页面点击即浏览器卡死**  
   曾由重复 polling/render 和全局自触发 MutationObserver 引起；已修复。

2. **Linux 请求已提交/已开始生成，却因 Worker WebSocket 短断立即失败**  
   已通过 reconnect grace + terminal outbox 修复。

3. **长期 warm tab 可以提交 Prompt，但无 delta/snapshot**  
   已确认 stale content module/runtime freshness 是重要根因；已有 bundle marker + preflight + reload + passive DOM recovery。

4. **在线更新 `upgrade_schedule_failed`**  
   曾由 chat2api sudoers fragment 被 bootstrap patch 损坏导致 `sudo -n` 失败；已修复并增加 repair path/detail。

5. **Playground vision/file 未选附件直接 skipped**  
   已改成生成默认真实 sample 并走完整 multimodal API。

6. **生成开始后长时间没有答案，心跳错误地续租请求**  
   已用 page-progress v49 和 diagnostic-only heartbeat 修复。

7. **相同 API Key + 完全相同 Prompt 同时请求可能抢同 Route**  
   已增加 request-id keyed synchronous reservation。

8. **刚失败的 Route 在几十毫秒后又被下一请求选中**  
   已用 terminal-before-release route quarantine 修复。

9. **ChatGPT 页面明确出现临时“发送超时/网络错误 + Retry”时只能等待失败**  
   已加入有界自动 Retry，且排除额度/账号/context/tool 状态。

10. **Server 容器替换导致管理员 session 消失，更新 UI 卡在旧百分比**  
    已用 session persistence + 401/403 login recovery 修复。

11. **FastAPI/Starlette 新版本下生产入口 import 崩溃，而 Docker build 仍成功**  
    已通过 lifespan compatibility + 真正 production boot smoke 修复。

---

## 9. 当前仍未完成 / 需要现场验证的事项

这些不是已经证明存在的新代码缺陷，而是下一会话应优先确认的剩余工作。

### P0：把最新 `main` 部署到真实中心服务器并核对版本

在真实服务器完成“从 GitHub 更新”后，确认：

```text
/version
server.runtime_version = 0.22.35
chrome_bridge.bundle_version = 0.8.8
features.playground_chat_window = true
features.server_update_worker_auto_sync = true
```

同时确认控制台显示的 commit/版本已经对应最新 main，而不是浏览器缓存或旧容器。

### P0：核对所有 Linux Worker 是否真正达到 0.8.8

此前真实诊断中出现过 Server 已更新但某 Worker 仍停留 `0.8.7`，并伴随 autoreload helper 缺失。

当前已经有 server-worker auto-sync，但仍必须在生产现场验证：

- 在线 Worker 自动升级；
- 已是 0.8.8 的 Worker 不重复重装；
- 离线 Worker 上线后继续 pending sync；
- 老 Worker 如果报 `manual-repair-required`，执行一次幂等 bootstrap `--upgrade` 后恢复。

不能只看 Server Runtime 推断 Worker 已加载最新 Bundle。

### P0：真实测试 Playground 新聊天窗口

至少覆盖：

1. gpt-5.6-sol 单轮文本；
2. 连续 3～5 轮上下文；
3. 切换 reasoning low/medium/high；
4. managed business API Key；
5. 手工粘贴 API Key；
6. 图片附件；
7. PDF/文档附件；
8. 流式长回答；
9. 故意制造一轮失败，再继续下一轮，确认失败 turn 不污染上下文；
10. 点击“新建对话”确认上下文清零；
11. 用 request id 在“请求记录”中对应到准确 Worker / 设备标识和诊断。

### P1：自动测试场的语音用例仍未接入

`voice_generation` 和 `voice_conversation` 在 `PlaygroundRunManager._run_kind()` 中仍返回 `skipped`：

```text
当前测试场尚未接入该语音用例
```

业务层已有 voice / realtime voice 相关能力，但自动 Playground 尚没有对应真实测试 case。这是明确未完成项。

### P1：持续验证 ChatGPT 页面变化的鲁棒性

项目最大的外部不稳定源仍是 `chatgpt.com` DOM/UI 行为变化。

后续如果再次出现：

- submission confirmed 后无输出；
- model/reasoning 无法验证；
- Retry/error UI 变化；
- attachment UI 变化；
- plugin/tool UI 新形态；

必须先导出 request JSON + runtime logs + Worker diagnostics，再根据真实 DOM/事件证据修改，不要扩大无依据的 timeout 或只让测试变绿。

### P2：正式 GitHub Release 流程尚未建立

仓库当前没有 GitHub Releases，也没有 release workflow。现阶段“正式版”定义仍是：

```text
merge to main
+ runtime/bundle version contract
+ CI success
+ production image smoke success
+ production deploy verification
```

如果后续希望对用户提供可追踪的正式版本、release notes 或不可变 artifact，需要单独设计 release/tag 流程。

---

## 10. 下一步推荐计划

### Phase A — 生产基线确认

1. 部署最新 `main`。
2. 核对 `/version`。
3. 核对每个 Linux Worker Agent / Bundle。
4. 查看 `/api/admin/server-worker-sync` 状态。
5. 如果 Worker 未同步，先判断：offline / stale / helper missing / sudoers / runtime preflight，不要先修改浏览器请求逻辑。

### Phase B — Playground Chat E2E

1. 手动聊天文本多轮。
2. 附件多轮。
3. 模型和 reasoning。
4. Key 切换。
5. 失败隔离。
6. request history / runtime log correlation。

如果发现 bug，优先围绕 `app/admin_playground_chat.js` 修复 UI/上下文问题；如果请求已经离开浏览器控制台进入 `/v1/chat/completions`，则按正常生产 request path 排查，不要为 Playground 创建第二套业务执行逻辑。

### Phase C — 补齐 Playground Voice

给 `voice_generation` / `voice_conversation` 增加真实测试 case，并保持：

- 唯一 request id；
- 可取消；
- 可持久报告；
- 正常业务 API 路径；
- 不用 mock 成功代替真实 Worker/voice path。

### Phase D — 持续硬化 Worker

只有真实日志再次证明存在新问题时继续：

- response capture；
- page progress；
- route lifecycle；
- transient retry；
- warm-tab runtime freshness；
- online upgrade；
- worker auto-sync。

原则是每个修复都同时覆盖“测试环境”和“外部真实请求”，不能只针对某个测试样本写特判。

---

## 11. 新聊天接手时的最低检查清单

新的开发会话不要直接从旧聊天结论继续写代码，先用 GitHub 重新确认当前状态：

```text
1. fetch main HEAD
2. fetch app/runtime_contract.py
3. fetch chrome_extension/manifest.json
4. fetch scripts/linux_worker_agent_v44.py
5. list open PR / issues
6. inspect latest CI + Production image smoke
7. if user supplies runtime log/diagnostic,以该次现场证据覆盖本文的历史现场状态
```

然后再开始改动。

如果要开发：

```text
main
  → 新建 feature/fix branch
  → 修改
  → 补 regression test
  → PR
  → CI + Production image smoke 全绿
  → merge
  → 再检查 main push workflow
```

除非用户明确要求直接提交 main，否则继续采用 PR + CI 后合并。

---

## 12. 不能破坏的回归约束

- 生产入口始终验证 `app.entry:app`，不能只测 `app.main`。
- `SERVER_RUNTIME_VERSION`、Worker Bundle、Agent version 是不同兼容面，不要机械统一 bump。
- console-only/server-only 功能不应无意义要求 Worker 重装。
- Worker payload 变化即使忘记 bump bundle，也应由 server-worker sync 的 diff detection 触发 refresh。
- Linux Agent 不能演变成 arbitrary remote shell。
- repair/bootstrap 必须幂等并保存 Worker identity/proxy/Chrome profile。
- UI MutationObserver / polling 必须有单一 owner、bounded frequency、idempotent DOM write。
- response heartbeat 只能做诊断，不能把“页面仍有 Stop 按钮”当成真正 token/answer progress。
- terminal request 在 reservation release 前必须完成正确 quarantine/ownership cleanup。
- failed Playground chat turn 不得进入后续模型上下文。
- 自动 Playground 可以随机 Prompt；手动 Playground chat 必须原文发送。
- API Key、管理员 cookie、Worker token 等 secrets 不得写入测试报告、runtime public diagnostics 或浏览器 sessionStorage。
- 新人可读时间统一使用北京时间；`*_ms` / epoch / `performance.now()` 不做时区转换。

---

## 13. 当前结论

截至 2026-08-29，仓库代码基线已经完成一轮针对 Linux Worker 稳定性、响应捕获、请求生命周期、在线升级、Server 自更新、Worker 自动同步以及 Playground 的集中硬化。

当前代码层没有已知阻塞合并项；最新功能 PR #156 已合并，功能基线 CI 和生产镜像 smoke 均通过。

下一阶段不应继续无目标地重写底层架构。优先顺序应该是：

```text
真实服务器部署最新 main
→ 核对所有 Worker 真正同步到 0.8.8
→ 完整 E2E 测试新 Playground 聊天窗口
→ 补齐 Playground voice 测试
→ 只根据新的真实日志继续修复 Worker/ChatGPT 页面适配
```

本文档作为新的聊天/开发会话的首要交接入口；开发规范仍以 `docs/DEVELOPMENT.md` 为准。