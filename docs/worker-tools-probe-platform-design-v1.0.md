# chat2api Worker 工具中心与证据探针平台——技术实现方案

**文档版本：** v1.0（设计稿）  
**编制日期：** 2026-10-21  
**基线版本：** chat2api v0.22.167  
**适用范围：** Linux / Windows Worker、Chrome 扩展、服务端控制台、请求记录与诊断包  
**交付状态：** 方案设计，**尚未实施、未合并、未发布**

## 1. 背景、证据与问题边界

### 1.1 本次故障事实

用户在测试场通过 `gpt-6` 文本聊天模式提交“帮我生成一张可爱小马主图100X100的”。请求 `req_ebb622a99d694cabbfb40ccddcd2aa3e` 在 chat2api v0.22.167 中等待约 **300.01 秒**后以 `Timed out waiting for ChatGPT` 失败。相关诊断事实如下。

| 诊断项目 | 观察值 | 工程含义 |
|---|---|---|
| `request_type` | `text` | 表示输入通道为聊天请求，不能推断输出只允许文本 |
| `submit_stage` | `media-awaiting-image-anchor` | 已进入图片证据等待阶段 |
| `non_deliverable_tool_handoff_event` | `chat.snapshot` | 存在工具交接信号，但不是可交付结果 |
| `media_assistant_nodes_found` | `0` | 既有 assistant 消息选择器未命中 |
| `media_document_candidates` | `0` | 现有候选图片扫描也未命中 |
| `media_baseline_images` | `0` | 请求开始前的候选图片基线为空 |
| `network_stream_http_status` | `200` | 上游请求建立成功，**不等于**生成已完成 |
| `network_stream_terminal_proof` | `false` | 尚无可信的网络完成证据 |
| `network_stream_error` | `signal is aborted without reason` | 网络观察流出现中断 |
| `completion_chars` | `0` | 没有交付文本或图片结果 |
| `status` / `total_ms` | `error` / `300010.2` | 最终由超时结束，而非图片成功交付 |

以上数字取自提供的请求诊断。用户此前的远程画面出现过已生成图片，但**本次诊断本身不能证明此次请求的图片已经生成完成**；需要用新的请求级探针、截图时间线和真实图片载荷进一步核实。

另有一条独立问题：测试场历史对话出现 PIL 代码及 `/mnt/data/...png` 文件路径。该路径属于沙箱/本地路径，**不是浏览器可访问 URL**，不得由服务端拼接域名伪装成真实图片。测试场新增的专用图片模式与原 `gpt-6` 聊天模式应分别验证。

### 1.2 已有代码与可复用能力

已核对主分支存在：

- `chrome_extension/content_request_v6.js`：现有请求生命周期、媒体交接观察及唯一终态控制者。
- `chrome_extension/content_rich_response_v69.js`：图片候选检测、图片归属与富文本提取。
- `app/telemetry.py`：请求记录追加/更新和查询。
- `app/request_history_v94_patch.py`：管理员请求历史与“查看对话”界面。
- `app/main.py`：FastAPI 管理接口、Worker/扩展连接与文件存储入口。
- 现有 Worker 诊断 ZIP 已含运行日志、Chrome 日志、扩展同步日志、服务端状态、运行时日志等，但缺少**按单次请求和探针类别组织的页面证据包**。

**实施要求：** 尽量复用上述模块，不要另建第二套请求历史、第二套 Worker 调度器或第二个终态控制器。以下新增路径、字段、接口均为**建议设计**，不是现有功能声明。

## 2. 建设目标与非目标

### 2.1 功能目标

1. 在服务端导航新增一级“**Worker 工具**”，集中管理所有可观察性探针，支持 Linux/Windows Worker、按 Worker ID 配置、全局默认、配置继承、启停、版本、状态、触发策略和诊断下载。
2. 启用后探针在**当前请求的同一浏览器 tab / window / Worker**按预设阶段自动采集证据，关联 `request_id`，不要求用户临时手工截图。
3. 首批提供图片交付、DOM/聊天回合、网络事件摘要、模型/工具状态、页面选择器兼容性五种只读探针。
4. 请求记录展示**探针摘要、证据时间线、失败分类、采集状态**；支持下载完整去敏证据 ZIP。
5. 页面改版后允许更新探针选择器规则包，历史诊断可回溯其 `probe_version` 和 `selector_rule_version`。
6. 为文本聊天触发图片、多步工具、文件附件等未来排障留出统一可扩展的探针接口。

### 2.2 非功能目标

- **单一终态所有权：** 只有现有 `request-v6` 可提交 `completed / error / timeout / cancelled`，探针只能提交事实与候选建议。
- **最小性能影响：** 正常模式轻量采样；强采样限速、限量、限时；探针失败不能拖慢或中断业务请求。
- **严格隔离：** 配置来源于服务端，按 Worker 身份鉴权；仅预置版本化探针，不允许控制台向浏览器任意注入 JavaScript。
- **可审计与隐私保护：** 默认不上传原始提示词、Cookies、Authorization、完整 URL 查询参数、签名图片 URL、图片原始字节。
- **版本兼容：** 老 Worker 忽略不认识的探针配置并报告 `unsupported`；不静默标记已执行。

### 2.3 非目标

不在本次平台开发中自动保证图片生成成功；不绕过 ChatGPT 页面原有权限、限额与工具能力；不以“页面存在图片”代替“图片已实际交付”；不修改普通 API 模型/路由策略；不修改 300 秒超时来掩盖捕获故障。

## 3. 总体架构与单一所有权

建议采用“**配置平面—采集平面—证据平面—现有决策平面**”四层架构。

```text
管理员控制台：Worker 工具
    │ 读取探针目录、全局策略、Worker 覆盖配置
    ▼
服务端：ProbeRegistry + ProbePolicyStore（配置版本化）
    │ 经现有 Worker 控制通道同步 effective_policy
    ▼
Linux/Windows Worker + Chrome 扩展
    │ ProbeSupervisor（只读调度、采集配额、事件时间线）
    ├─ media / conversation / network / model / selector probes
    │                 │ 结构化事实 ProbeEvent
    ▼                 ▼
EvidenceIngest（鉴权、校验、脱敏、幂等、限流）
    ├─ ProbeRun / ProbeEvent 元数据索引
    └─ EvidenceArtifact 文件存储（单独于 request_history.jsonl）
             │
请求记录：探针摘要 / 时间线 / ZIP 下载
             │
request-v6：唯一终态裁决者，读取被验证的交付证据
```

**核心规则：** `ProbeSupervisor` 不调用 `chat.completed`、`chat.failed`，也不持有业务超时计时器的写权限。媒体捕获器负责把可信字节/引用转换为可交付结果；`request-v6` 使用这一结果及请求生命周期证据提交最终状态。新增探针不能再增加一个“兜底成功控制器”。

### 3.1 组件职责

| 组件（建议） | 职责 | 禁止行为 |
|---|---|---|
| `ProbeRegistry` | 内置工具列表、能力、版本与配置 schema | 加载管理员上传的任意可执行脚本 |
| `ProbePolicyStore` | 全局/Worker 策略、审计记录、继承与 revision | 决定业务模型或窗口路由 |
| `ProbeSupervisor` | 在允许阶段调度只读探针、收集事实 | 提交请求最终状态 |
| 各类 Probe | DOM、图像、网络事件、控件选择器观察 | 发送用户消息、点击生成按钮、改变模型 |
| `EvidenceIngest` | 归档、脱敏、关联与查询、生成诊断包 | 存储令牌、未经授权的完整页面文本 |
| `request-v6` | 唯一的请求成功/失败终态控制 | 仅凭图片候选数量判 `completed` |

## 4. 服务端“Worker 工具”页面设计

### 4.1 导航与页面布局

左侧新增一级菜单 **Worker 工具**，页面顶部为“工具目录 / Worker 配置 / 运行记录 / 策略模板 / 全局设置”五个页签。默认选择“工具目录”，可按名称、平台、分类、状态筛选。

工具列表字段：工具名称、工具 ID、版本、用途、支持平台、当前启用 Worker 数、触发策略、最近运行结果、采集开销等级、配置/详情操作。每个工具详情页展示输入 schema、采集字段、脱敏规则、最近成功率、失败原因及升级说明。

**Worker 配置页：** 选择设备（例如 TX03）→ 选择 Linux/Windows Worker → 显示各工具的“继承全局 / 显式开启 / 显式关闭”、有效配置版本、最后同步时间、当前执行状态。配置变更可预览 diff、保存、回滚上一个版本。

**运行记录页：** 按 Worker、请求 ID、工具、开始时间、阶段、成功/失败过滤，查看采集时间线及下载诊断包。“手动采集”只面向当前在线、已授权 Worker 的当前 ChatGPT tab，必须展示采集范围和隐私提示。

### 4.2 内置探针目录

| ID | 名称 | 优先级 | 默认启用 | 主要触发 |
|---|---|---|---|---|
| `media_delivery_v1` | 图片交付探针 | P0 | 有条件 | 图片交接、图片候选异常、超时前 |
| `conversation_dom_v1` | 聊天回合与 DOM 探针 | P0 | 轻量 | 请求前基线、提交后、疑似完成 |
| `network_evidence_v1` | 网络/SSE 摘要探针 | P1 | 轻量 | 每次请求、有网络异常时增强 |
| `model_tool_state_v1` | 模型和工具状态探针 | P1 | 开启 | 请求准备、模型验证异常 |
| `selector_compat_v1` | 页面改版/选择器兼容探针 | P1 | 异常时 | 关键选择器命中数为零 |

## 5. 第一批探针详细设计

### 5.1 图片交付探针 `media_delivery_v1`（P0）

**采集阶段：** 请求提交前的基线；提交后首次图片工具交接；每次出现新图片候选；停止生成后；“可见但未交付”超过阈值；超时/失败前最后一次采集。

**结构化字段：**

- `request_id`、`worker_id`、`tab_id`、`window_id`、`conversation_generation`、采集阶段与时间戳；
- 主聊天区候选数量、当前回合候选数量、基线新增数量、位于当前用户回合之后的候选数量；
- 图片 DOM 类型（`img`、`picture`、CSS background、canvas、工具卡片，实际支持范围依能力报告）；
- 图片宽高（DOM 布局与 `naturalWidth/naturalHeight`）、可见性、加载完成状态、节点所属容器、按钮特征（编辑/下载/分享）；
- 只存 URL 类型、域名与归一化哈希，不存完整 URL、查询字符串、签名参数；
- 图片复制方式：data URL / blob / 同源请求 / 可信域名 relay；尝试、成功、失败计数；
- MIME、大小限制、可信域名检查、可传输字节是否存在、是否创建归档 `artifact_id`。

如扫描结果持续为零，增强模式应记录主聊天容器范围、候选 DOM 结构摘要、未命中的选择器列表，并允许在权限审批下加入脱敏页面截图。**不要把 canvas 或 CSS 背景图视为已自动实现捕获**：须先明确其可读取性与跨域限制。

### 5.2 聊天回合与 DOM 探针 `conversation_dom_v1`（P0）

对最新 3 条可识别回合分别采集：消息角色、DOM 容器类型、测试属性和稳定的选择器特征、文本长度、生成状态、展开按钮、消息前后相对位置；取**去敏的结构摘要**，默认不保存完整 HTML、提示词或回复文本。

对无法归属的独立媒体卡片记录：与新用户消息的文档顺序、渲染时间、候选父容器、是否位于 `main/[role=main]`，以便区分“页面没有图片”和“图片存在但超出当前提取器扫描范围”。

### 5.3 网络事件探针 `network_evidence_v1`（P1）

复用当前 `conversation-fetch` 观察机制，不另装一个争夺 fetch 主导权的网络拦截器。采集：HTTP 状态、SSE 事件类型计数、序号、首包/末包时间、工具交接事件名、取消/中断原因、是否出现终态证明；只保留有限数量的事件类型摘要，默认不保存 SSE 原始正文。

### 5.4 模型与工具状态探针 `model_tool_state_v1`（P1）

采集页面模型显示、推理强度可见性与可信度、Chat/Work 模式、composer 可用性、图片生成入口可见性、限额/登录状态及控件命中来源。报告“未找到 / 不可信 / 已确认”，避免只给一个推断模型名。

### 5.5 选择器兼容探针 `selector_compat_v1`（P1）

针对被请求处理器依赖的选择器建立版本化清单：命中数、结构签名、替代候选、匹配置信度与 DOM 特征哈希。页面改版时比较 `selector_rule_version`，快速定位失效范围。新规则先以 observe-only 部署，经过测试后再修改捕获器。

## 6. 探针执行时机、权限与资源限制

建议支持以下触发枚举：`request_pre_submit`、`request_submitted`、`media_handoff`、`media_candidate_changed`、`generation_idle`、`unresolved_after_threshold`、`before_terminal`、`manual_capture`。

**默认 P0 策略建议：** 所有请求执行极轻量基线；仅遇到图片交接或图像意图时启用图片探针；超过 20 秒仍无交付证据时增强一次；距离业务超时约 15 秒再采样一次。此数值是**建议配置**，必须通过性能实测调整，不得改变业务超时与原控制器的含义。

- 探针使用队列与令牌桶，不阻塞页面点击、请求调度和正常文本增量发送。
- 同请求同一探针默认最多 8 次快照；每次 DOM 采集预算建议 150 ms；网络摘要在已存在监听器上增量聚合。
- 普通请求原始探针元数据默认压缩后上限 1 MiB；失败增强模式建议 5 MiB；截图另设数量和体积上限。
- 服务器达到存储阈值时停止详细截图/HTML 归档，但仍保留简要失败原因和 `capture_dropped_reason`。
- “仅失败时执行”只能采集故障发生后还存在的页面；请求结束前的基线和变化过程必须由轻量探针先行留存，否则无法复盘丢失的 DOM。

## 7. 配置模型与示例

探针定义与策略分离：定义由代码版本维护，配置只包含经过 JSON Schema 验证的参数。策略层级为“全局 → Worker 覆盖 → 单次管理员诊断会话”，解析为最终 `effective_policy`，保存 `policy_revision` 和 `policy_hash`。

```json
{
  "schema_version": 1,
  "policy_id": "worker-tools-default",
  "revision": 12,
  "scope": {"kind": "worker", "worker_id": "wrk_..."},
  "tools": {
    "media_delivery_v1": {
      "enabled": true,
      "mode": "on_media_handoff",
      "triggers": ["request_pre_submit", "media_handoff", "unresolved_after_threshold", "before_terminal"],
      "unresolved_after_ms": 20000,
      "max_snapshots": 8,
      "capture_screenshot": "on_failure_opt_in",
      "capture_raw_html": false,
      "capture_full_media_url": false
    },
    "conversation_dom_v1": {"enabled": true, "mode": "lightweight"},
    "network_evidence_v1": {"enabled": true, "mode": "event_summary"}
  }
}
```

**配置规则：** `enabled=false` 必须实际停止采集，不能由隐式旧开关重新启用；`on_failure_opt_in` 指仅预先授权的故障增强会话可保存截图，不能用运行时失败自动推定用户已授权。离线 Worker 保留待同步配置但不显示“已生效”。

## 8. 证据数据合同与归档结构

### 8.1 主记录摘要（扩展既有 request telemetry）

业务请求主记录新增非终态字段：`probe_summary`、`probe_run_ids`、`probe_policy_revision`、`probe_artifact_count`、`probe_capture_status`。证据字节、截图、HTML 摘要文件不写入请求历史 JSONL，防止重复 upsert 导致日志膨胀。

### 8.2 `ProbeEvent` 示例

```json
{
  "schema_version": 1,
  "event_id": "probe_evt_...",
  "probe_run_id": "probe_run_...",
  "request_id": "req_...",
  "worker_id": "wrk_...",
  "tab_id": 2021540669,
  "tool_id": "media_delivery_v1",
  "tool_version": "1.0.0",
  "policy_revision": 12,
  "sequence": 7,
  "stage": "unresolved_after_threshold",
  "occurred_at": "2026-10-20T20:56:10+08:00",
  "capture_status": "ok",
  "facts": {
    "assistant_turn_nodes": 0,
    "document_image_candidates": 0,
    "new_media_candidates": 0,
    "tool_handoff_observed": true,
    "network_terminal_proof": false,
    "image_transfer_attempts": 0
  },
  "classification": "handoff_without_visible_media",
  "artifact_ids": []
}
```

`classification` 是**探针观察结果**，不得直接写入 `request.status`。有数据则使用整数/布尔值；没有采到要写 `null` 与 `capture_status=unavailable`，不能把“未采集”误写成 0。

### 8.3 诊断 ZIP 规范

```text
request-diagnostics-<request_id>.zip
  manifest.json                   # schema、版本、Worker、时间范围和 SHA256
  request-summary.json            # 已脱敏请求概要与终态
  probe-summary.json              # 每个工具运行/缺失原因
  events/probe-timeline.jsonl     # 有序证据事件
  probes/media-delivery.json      # 图片候选与传输证据
  probes/conversation-dom.json    # 回合/选择器摘要
  probes/network-evidence.json    # SSE/工具事件摘要
  probes/model-tool-state.json    # 模型/控制状态
  probes/selector-compat.json     # 页面版本特征
  screenshots/                    # 仅在授权且确有采集时出现
  checksums.sha256
```

ZIP 清单记录 `artifact_present`、`artifact_omitted_reason`、`redaction_version`、`capture_status`；因此即使没抓到图片也能看出“探针未启用、调用失败、权限拒绝、页面不匹配、上传中断”中的哪一种。

## 9. 图片结果判定状态机——探针与最终决策分离

**重要纠正：** “DOM 里发现图片”属于**候选证据**，不等于可向 API 客户端交付的结果。`completed` 的必要条件始终是产生可消费的文字、文件或图片结果载荷，并且由唯一终态控制者确认归属与稳定性。

建议的媒体子状态：`none → handoff_seen → candidate_detected → candidate_attributed → transfer_attempted → deliverable_verified`；失败分支包括 `no_candidate`、`ambiguous_owner`、`untrusted_source`、`transfer_failed`、`invalid_media`、`tool_incomplete`。

| 证据组合 | 处理结论 | 是否可写成功终态 |
|---|---|---|
| `chat.snapshot` 工具交接，DOM 无图片 | 继续等待并启动探针，记录 `handoff_without_visible_media` | 否 |
| DOM 新图片可见，但无法归属本次请求 | 继续收集回合/时序证据，标记不确定 | 否 |
| 当前请求图片已归属，但图片传输失败 | 重试安全读取并保留失败原因 | 否 |
| 已获得真实图片字节/持久可访问的受控引用，且完成归属校验 | 构建 image 结果并请求现有终态控制者提交 | **可以** |
| 仅得到 PIL 代码和 `/mnt/data/xxx.png` | 视为文本，不伪造图片 | 仅按实际文本成功规则 |
| 收到明确取消/服务端超时 | 固化最后一次证据并保留原失败终态 | 否 |

建议记录两种分开的指标：`generation_observed`（页面已生成迹象）与 `delivery_verified`（真实结果字节/受控文件引用存在），不得合并为一个 `success`。

## 10. 管理 API / Worker 通信接口（建议新增）

| 接口 | 用途 | 权限 |
|---|---|---|
| `GET /api/admin/worker-tools/catalog` | 获取内置工具目录、schema、最低 Worker 版本 | 管理员 |
| `GET /api/admin/worker-tools/policies` | 查看全局与 Worker 配置 | 管理员 |
| `PUT /api/admin/worker-tools/policies/{scope_id}` | 带 `expected_revision` 的配置更新 | 管理员 |
| `GET /api/admin/worker-tools/runs` | 检索探针运行记录 | 管理员 |
| `POST /api/admin/worker-tools/workers/{worker_id}/capture` | 在当前授权 tab 手动采集（带审计） | 管理员 |
| `GET /api/admin/requests/{request_id}/probes` | 请求证据摘要和时间线 | 管理员 |
| `GET /api/admin/requests/{request_id}/probes/download` | 下载脱敏证据 ZIP | 管理员 |

Worker 与服务端间的 `policy.sync`、`probe.event`、`probe.artifact` 是**建议扩展现有已鉴权通道的内部消息类型**，不是新增公开 HTTP 接口。上报必须验证 Worker 绑定、请求归属、tab/window 所有权、配置版本，使用 `(probe_run_id, sequence)` 幂等去重，并限制重传次数。

**管理 API 规则：** 必须复用现有管理员鉴权；业务 API Key 不能读取探针证据；所有下载写入管理员审计日志；手动采集必须拒绝离线 Worker 或目标 tab 与请求归属不符的情况。

## 11. 安全、隐私、保留与存储

- **默认只读与最少内容：** 只保存节点统计、类名/属性的有限白名单、文本长度、事件类别和哈希；不默认记录完整 HTML、完整对话、浏览器 Cookie、第三方身份令牌。
- **URL 去敏：** 只保留协议、域名、图片资源类型与加盐哈希；删除所有查询参数、路径中可识别的会话凭据。截图不包含浏览器地址栏中的凭据。
- **截图与增强采集：** 默认关闭；管理员显式授权、显示保存位置/范围；对账户头像、提示词、其他会话、账户信息区域自动遮罩；失败不能回退为未遮罩原图。
- **保留策略：** 建议事件摘要 30 天，原始增强证据 72 小时，截图 24 小时；均可调整，需明确最大磁盘空间与 LRU/TTL 清理。删除请求时同步删除对应证据。
- **上传限制：** JSON Schema 白名单、最大字段数、体积、频率限制；二进制文件执行 MIME 验证与内容检查，阻止 ZIP 路径穿越、HTML 脚本和可执行内容被当作预览文件。
- **权限隔离：** 禁止任意脚本插件化；未来增加工具时通过签名版本包和审查过的内置注册表发版，不能通过管理控制台上传 JS 直接执行。
- **稳定性：** Worker 离线和证据上传失败只影响诊断完整性，不应改变原请求终态、队列容量、备用窗口数量或模型决策。

## 12. 存储模型和迁移

建议增加 `worker_probe_policies`、`probe_runs`、`probe_events`、`probe_artifacts` 四种逻辑实体。可以复用现有存储技术，但**事件与产物应按请求索引并从大体积文件中分离**。

唯一索引：`probe_runs(run_id)`、`probe_events(run_id, sequence)`、`probe_artifacts(artifact_id)`；辅助索引：`request_id, occurred_at`、`worker_id, occurred_at`、`tool_id, status`。策略表采用 `scope_id, revision` 乐观锁。

旧版请求没有探针数据时显示“未启用探针/该请求无证据”，不应显示“图片数量 = 0”；迁移不能重写历史请求的成功/失败状态。

## 13. 测试与验收方案

### 13.1 单元与模拟浏览器回归

1. 工具目录注册/关闭/继承/覆盖，配置错误拒绝，低版本 Worker 报 `unsupported`。
2. 请求前已有图片不被归属为新请求；侧边栏头像、用户上传图片和历史图片被排除。
3. 独立图片卡片、`picture/currentSrc`、blob、受信 HTTPS URL、懒加载、DOM 重建与展开/收起多种夹具。
4. `chat.snapshot` 出现但无可交付图片时，不能标记成功；真实图片字节归档后才可成功。
5. 网络流 HTTP 200 但 SSE abort、图片仍生成中、Worker 断线、Tab 切换、多个并发请求时，探针关联不能串请求。
6. 图片下载失败、MIME 异常、大小超限、跨域拒绝、用户权限不足时，探针日志必须有独立错误码。
7. 证据 ZIP schema、校验和、完整/部分采集、缺失原因；不出现密钥和签名 URL。
8. `request-v6` 唯一终态断言：同一 request 不得出现两次 `chat.completed` 或成功后被探针改写为 `error`。

### 13.2 TX03 实机验收（必须执行）

使用**全新测试对话**，分别测试 `gpt-6` 普通聊天“生成一张购物袋主图”与专用 `gpt-image` 图片模式。区分“页面是否真正生成”与“请求是否拿到真实图片”。

| 测试场景 | 期望结果 |
|---|---|
| 图片在 60 秒内显示于 ChatGPT 页面 | 请求证据时间线记录图片/候选出现；如工具未命中，给出具体扫描范围与未命中原因 |
| 页面图片存在但 API 无图 | 探针显示归属、图片资源类型、传输尝试及失败阶段，仍不可虚报 `completed` |
| API 成功获取图片 | 请求 `completed`、`delivery_verified=true`，测试场与“查看对话”可预览/下载相同受控产物 |
| 图片生成失败/限额 | 仍报告原始失败，保留探针最后快照与限额信号 |
| 普通纯文本请求 | 探针 CPU/内存/流式延迟在约定预算内，无新增错误 |
| 页面 DOM 改版夹具 | 选择器兼容探针报告哪些节点消失和候选替代结构 |
| Worker 断线/版本不兼容 | 日志清晰标注 `offline/unsupported/partial`，不阻塞其他 Worker |

重点检查请求记录能否形成完整的“**时间—页面候选—归属—下载—交付—终态**”链条。**若只见 DOM 图片，却没有真实载荷，不应通过验收。**

### 13.3 建议性能与可观测性指标

- 正常文本请求探针增加的 P95 CPU 时间和网络流量须先建立基线，建议目标：单次轻量采集 CPU 时间小于 150 ms，非截图证据小于 1 MiB。
- 工具级采集成功率、采集延迟、Worker 不兼容占比、证据截断率、无法归属图片比率需有后台统计。
- 系统应能在请求失败后不依赖开发者远程复现，仅凭下载的诊断 ZIP 初步区分“选择器失效、消息回合缺失、页面无结果、媒体下载失败、终态缺失”五大类。

以上为设计验收目标，需要实测确认；并非现有项目已经达到的结果。

## 14. 开发拆分、依赖与发布流程

### P0：可用的证据闭环（先做）

- 工具目录、全局/Worker 策略、基础工具页、版本同步和权限；
- `ProbeSupervisor`（不含业务终态控制）及 `media_delivery_v1`、`conversation_dom_v1`；
- 请求级摘要、时间线、证据 ZIP、下载权限与存储保留；
- 既有 `request-v6` 唯一终态不变；完成端到端回归和 TX03 实机故障重放。

**P0 验收：** 同样的 300 秒失败即使再次发生，也能确定是 DOM 未发现、图片未归属、媒体不可下载还是没有终态证明，而不是只有泛化 `Timed out waiting for ChatGPT`。

### P1：诊断精度与主动发现

- 网络 SSE 摘要、模型/工具状态、页面选择器兼容探针；
- 关键选择器命中异常自动增强采样；Worker 间策略模板与变更审计；
- 测试场检测图片意图后**提示用户**切换到专用图片生成模式，不默默改用其他计费/语义的模型；
- 请求记录可视化证据时间轴、横向比较不同 ChatGPT 页面版本。

### P2：规模化工具生态

- 多 Worker 批量分配与模板；工具兼容性矩阵及逐步灰度启用；
- 已签名、经审核的内置工具包升级与旧版回滚；
- 针对文件、语音、视频、连续任务新增探针类别；
- 聚合问题报告与跨版本差异分析。

### 发布纪律与上线步骤

1. 从最新 `main` 建功能分支；先跑 Python、Node、浏览器模拟/扩展测试及版本契约校验。
2. GitHub Actions 执行完整 CI 与生产镜像冒烟；检查异常时不降低检查门槛。
3. 功能 PR 评审通过后合并；建立正式版本 PR，同步 Server、Linux/Windows Worker、扩展清单和版本测试。
4. 发布 PR 验证通过后合并，等待**同一 SHA**的主分支 CI/镜像检查及 GitHub Release 实际生成。
5. 先在一台测试 Worker 开启轻量探针，进行真实页面验证；再灰度到 TX03 及其他 Worker。
6. 回滚支持关闭某个探针、回滚策略 revision、回退版本；禁止因探针回滚而修改历史请求终态。

## 15. 最终验收清单

- [ ] 后台出现独立“Worker 工具”菜单，配置权限、工具状态、版本、继承策略准确。
- [ ] Linux/Windows Worker 均能获取各自的有效策略，离线/旧版提示明确。
- [ ] 每次被触发的探针记录 `request_id + worker_id + tab_id + tool_version + policy_revision`。
- [ ] 请求日志能够展示探针是否执行、关键事实、失败原因与事件时间线。
- [ ] ZIP 包可下载、可校验、已脱敏、有缺失原因和保留策略。
- [ ] 模拟 DOM 改版后，可明确定位失效选择器或新结构，不误抓历史图片。
- [ ] 真实图片字节/受控引用未获得时不允许误报成功；多 Worker 并发不串归属。
- [ ] 探针不会竞争 Worker 调度、页面输入、图片采集执行权和唯一请求终态。
- [ ] P0 功能通过自动化与 TX03 实机验收，正式版标记与运行 Worker 版本一致。

## 附录 A：推荐故障分类字典

`PROBE_DISABLED`、`PROBE_UNSUPPORTED`、`PROBE_TIMEOUT`、`PROBE_UPLOAD_FAILED`、`DOM_SELECTOR_MISS`、`CHAT_TURN_NOT_FOUND`、`MEDIA_CANDIDATE_NOT_FOUND`、`MEDIA_OWNER_AMBIGUOUS`、`MEDIA_SOURCE_UNTRUSTED`、`MEDIA_TRANSFER_CORS_FAILED`、`MEDIA_TRANSFER_HTTP_FAILED`、`MEDIA_TOO_LARGE`、`MEDIA_INVALID_MIME`、`MEDIA_BYTES_NOT_VERIFIED`、`NETWORK_TOOL_HANDOFF_ONLY`、`NETWORK_TERMINAL_MISSING`、`AUTH_REQUIRED`、`PAGE_RATE_LIMITED`。

单条请求允许多个**事实错误码**，但最终业务错误码仍由现有 `request-v6` 决定。日志摘要不应只剩一种泛化 `timeout`。

## 附录 B：开发实现映射（建议新增，非现存文件）

```text
app/worker_probe_registry.py       工具清单与 schema
app/worker_probe_policies.py       配置管理、revision、审计
app/worker_probe_store.py          元数据索引与归档
app/worker_probe_api.py            后台 API / 导出
chrome_extension/content_probe_supervisor.js   采集调度与限流
chrome_extension/content_probe_media.js        图片交付探针
chrome_extension/content_probe_dom.js          聊天回合与选择器探针
chrome_extension/content_probe_network.js      复用网络观察事件摘要
chrome_extension/content_probe_model.js        页面模式/控件观察
tests/test_worker_probe_*.py       API、存储、配置和权限回归
tests/worker_probe_*.mjs          DOM 模拟、安全与单一终态回归
```

**技术结论：** 优先把“看不到证据”变成“明确知道哪项证据缺失”，再根据真实采集报告修订媒体结果捕获器。探针平台本身不应改变业务语义，更不能绕过真实图片交付证明。