# chat2api 开发规范

## 时间标准：统一使用北京时间

chat2api 的产品运行环境、控制台、测试报告与扩展运行日志统一以 **Asia/Shanghai（北京时间，UTC+08:00）** 作为人可读时间标准。

### 必须遵守

- 所有新增的人可读 ISO 时间字段必须带 `+08:00`，例如：`2026-08-11T17:28:27.792+08:00`。
- 服务端持久化数据中的 `created_at`、`updated_at`、`recorded_at`、`started_at`、`finished_at`、`last_seen_at`、`last_used_at`、`revoked_at`、文件创建时间等统一使用北京时间。
- Chrome 扩展的状态时间、运行日志 `at`、run 的 `started_at` / `ended_at` / `last_activity_at`、日志 part 的 `saved_at`、日志导出 `generated_at` 统一使用北京时间。
- 读取旧版本 UTC (`Z` / `+00:00`) 持久化时间时，应在加载或对外输出时转换为 `Asia/Shanghai`，不能要求用户手工加 8 小时。
- 前端展示和导出的日志文件不得把浏览器所在时区当作 chat2api 的产品时区；即使浏览器运行在其它地区，chat2api 的产品时间仍固定为北京时间。
- **服务端控制台不得把已经是北京时间的无时区表格字符串再次当作 UTC 解析。**
- Python 代码优先使用 `app/timezone_utils.py` 的 `beijing_now_iso()` / `to_beijing_iso()`。
- Chrome 扩展需要生成时间字符串时使用固定 `+08:00` 的 Beijing helper，不使用浏览器本地时区推导产品时间。

### 不应“加 8 小时”的值

以下值是时区无关的绝对计时或耗时，不应做时区换算：

- `Date.now()` / Unix epoch milliseconds；
- `chrome.alarms` 的 `when`；
- 120 秒 run idle deadline 的 epoch 数值；
- `performance.now()`；
- `*_ms` 耗时、延迟、超时和 duration。

## 管理员登录与业务 API Key

- **管理员身份和业务 API 身份必须彻底分离。** 控制台只使用 `CHAT2API_ADMIN_USERNAME` + `CHAT2API_ADMIN_PASSWORD` 登录，登录成功后由服务端签发 HttpOnly session cookie。
- 管理员账号密码不得作为 `/v1/*` 的 Bearer Token，也不得作为 Chrome 扩展认证凭据。
- `CHAT2API_API_KEY` 从 v0.17 起是废弃迁移输入：不能登录控制台，也不能调用 `/v1/*`。它只允许在首次升级时帮助解密旧版业务 Key 密文，迁移完成后应从 `.env` 删除。
- 业务调用只允许使用“API Key”页面创建的 managed API Key。测试场同样必须选择或手动粘贴业务 API Key，不能隐式借用管理员会话。
- 业务 API Key 的可恢复密文使用 `CHAT2API_DATA_DIR/data_secret.key` 的独立服务端数据密钥加密。管理员密码变化不得导致已创建业务 Key 无法解密。
- 管理员 session 默认只保存在服务端内存；服务器重启后要求重新登录。

## 扩展配对与扩展管理

- 控制台必须提供独立“扩展管理”页面，集中管理配对码和已绑定扩展；扩展记录区域统一命名为 **扩展列表**，不再使用“绑定设备”作为区域标题。
- 配对码通过控制台创建，原文只在创建时显示一次，持久化只保存哈希和前缀。
- **一个配对码只允许绑定一个持久设备标识。** Chrome Bridge 首次运行生成随机 `device_id` 并保存于 `chrome.storage.local`；浏览器重启或扩展重载不得改变该标识。
- 首次成功配对后，服务端保存 `pairing_id + client_id + device_id`；扩展保存 `client_id + clientToken`。以后扩展上线必须直接使用设备凭据自动连接，不要求再次输入配对码。
- 同一配对码、同一 `device_id` 可重新配对并轮换 client token；同一配对码不能被不同 `device_id` 抢占。
- 控制台配对码列表必须显示绑定设备、绑定扩展和实时连接状态（未绑定 / 在线 / 忙 / 离线 / 已禁用）。
- 扩展列表必须显示扩展最近一次上报的 ChatGPT 账户类型：`Free` / `付费` / `未识别`。
- Chrome Bridge 必须在首次配对、WebSocket hello 和后续状态上报时被动识别当前 ChatGPT 账户类型；不得为了识别账户类型点击账户菜单，不得上传邮箱、昵称等无关个人信息。
- 账户类型发生变化后，应在后续状态上报中自动更新服务端元数据，不要求重新生成 `device_id` 或重新配对。
- “断开连接”必须不仅关闭当前 WebSocket，还把设备标记为 `connection_enabled=false`；否则扩展的自动重连会立即把它重新接回。管理员点击“允许连接”后，扩展下一次自动重连应恢复上线。
- 废弃的 `CHAT2API_PAIRING_CODE` 只作为一次性迁移输入导入配对码列表，后续配对码必须从控制台管理。

## API Key → 扩展粘性路由

- 当调用方显式提供 `client_id` / `X-Chat2API-Client` 时，必须优先使用指定扩展，并把该选择记录为当前 API Key 的最新绑定；模型专用的 Free mini 路由除外，见下文。
- 当调用方没有指定扩展时：
  1. 若该 API Key 上次绑定的扩展仍在线且允许连接，继续使用同一扩展；
  2. 若是该 API Key 首次请求，从当前在线且允许连接的扩展中随机选择一个并持久化绑定；
  3. 若旧绑定扩展离线或被管理员禁用，从剩余在线扩展中随机迁移，并覆盖旧绑定。
- `api_key_routes` 必须持久化，服务端重启后仍保持同一 API Key 的设备亲和性。
- 为保持同一 ChatGPT 浏览器环境的连续性，已绑定扩展“忙”时不要因为忙而偷偷切换到另一台设备；并发冲突仍按现有 busy/409 语义处理。
- Chat、Responses、Images、Voice、Realtime Voice 都必须共享同一套粘性路由规则，不得各自维护独立分配逻辑。
- **`gpt-5.5-mini` 的 Free 优先选择属于模型专用路由，不得覆盖普通付费文本模型的 API Key 粘性绑定。** 一次 mini 请求不能把后续 `gpt-5.5` / `gpt-5.6-sol` 错误粘到 Free 扩展。

## 模型与推理强度路由

公开文本模型 ID 使用：

- `gpt-5.6-sol`
- `gpt-5.5`
- `gpt-5.5-mini`：ChatGPT Free 账户逻辑模型

`gpt-5.6-sol` / `gpt-5.5` 的推理强度使用 OpenAI 风格：

- `low` → ChatGPT `极速`
- `medium` → ChatGPT `中`
- `high` → ChatGPT `高`

**所有可选择推理强度的 OpenAI 兼容文本入口都必须采用确定性的默认推理强度。** 对 `gpt-5.6-sol` / `gpt-5.5`，调用方未传 `reasoning_effort`，或 Responses API 未传 `reasoning.effort` 时，服务端统一归一为 `medium`，扩展必须按 ChatGPT 页面“中”执行，不得继承当前页面已有的推理强度。

`gpt-5.5-mini` 是例外：Free 页面没有可选择的模型 family / 推理强度控件，因此该逻辑模型不暴露独立推理强度语义。调用方即使传入 `reasoning_effort`，Free 原生路径也不得尝试操作不存在的模型或推理 UI。

### `gpt-5.5-mini` 路由

1. 请求 `gpt-5.5-mini` 时，优先从当前在线、空闲且已识别为 `free` 的扩展中随机选择。
2. 选中 Free 扩展后，直接使用当前 Free 页面默认模型，**不打开、不点击、不验证模型 family 或推理强度菜单**；只执行 composer preflight、附件准备和消息发送。
3. 当前没有可用 Free 扩展时，从其它在线、空闲扩展中随机选择一个，并在扩展侧实际执行 `gpt-5.5 + low/极速`。
4. 无论 Free 原生还是付费回退，对外 OpenAI-compatible 响应中的 `model` 都保持调用方请求的 `gpt-5.5-mini`；实际回退细节只能放在 chat2api diagnostics / metadata 中。
5. 已明确识别为 Free 的扩展不得承接 `gpt-5.5` / `gpt-5.6-sol` 的可选模型请求；`unknown` 为兼容旧扩展可暂按非 Free 候选处理，但扩展应尽快刷新账户识别状态。
6. Free 扩展状态上报只声明 `gpt-5.5-mini` 默认文本模型，并移除 `model-selection`、`reasoning-selection`、`passive-model-state` 能力标记。

网页端的 `智能/自动` 只保留为兼容旧页面或手工操作状态的防御性识别值，不是公开 API 推理强度，也不能作为省略参数时的默认行为。

普通付费模型扩展路由原则：

1. 优先被动读取当前 composer DOM 与可信同页缓存；模型和推理强度均匹配时必须走 zero-op，不重新打开菜单。
2. ChatGPT 可能把当前状态合并显示为 `5.5 高` 之类的 composer pill；被动探测必须能同时从该控件识别模型和推理强度。
3. 模型 UI 切换后，旧选择器二次打开菜单可能看不到已选模型。若 composer DOM 已明确证明目标模型，应使用被动 DOM 恢复验证。
4. GPT-5.6 Sol family transition recovery 只有在旧 family、精确目标点击和 composer 可观察变化同时存在时才允许推断成功。
5. 推理强度不一致时优先走快捷键、键盘和原生 range 等 no-click 路径；只有这些路径均不可用时才 click fallback。
6. **推理滑块不得假定只有三个键盘步进。** `极速` 可用 Home、`高` 可用 End、`中` 必须逐步移动并根据页面真实状态确认。
7. 在模型和推理强度最终验证通过之前不得发送 prompt。

## 控制台模型广场

- 控制台必须提供独立的 **模型广场** 导航页面，集中介绍 chat2api 已发布的文本、多模态、图片与语音模型。
- 页面采用模型卡片形式展示模型 ID、定位说明、输入/输出类型、能力标签、推理强度、默认推理参数、支持 API、适用场景和最小调用示例。
- 页面必须使用实时模型目录标记“当前可用 / 未在线”，不能只依赖硬编码状态。
- 支持按模型 ID、能力、使用场景搜索，并支持“全部 / 文本与多模态 / 图片 / 语音”分类筛选。
- 模型卡片必须提供复制模型 ID 和复制最小调用示例的快捷操作。
- 只展示 chat2api 能可靠声明的数据。没有可靠来源的价格、上下文窗口、吞吐、限速、基准分数等字段不得编造。

## Chrome 扩展版本显示

- 扩展 popup 必须直接显示 `chrome.runtime.getManifest().version`。
- 版本显示不得从硬编码文本读取。

## 请求诊断版本

- `/api/admin/requests/<id>/log` 的 `server_version` 必须由当前最外层服务端补丁覆盖。

## 运行日志

- 运行日志自动静默保存在 `chrome.storage.local`；导出按钮只是查看/导出入口，不是日志记录开关。
- 日志按 API Key 独立 sessionize，同 Key 最后一个请求结束后 120 秒无新请求才封账。
- 日志目标 part 大小约 200 KiB，只能在完整 JSONL 记录之间换卷。
- 运行日志中的人可读时间直接以北京时间为 canonical time。

## 回归要求

涉及管理员登录、扩展管理、路由、模型、时间或运行日志的改动，提交前至少验证：

- Python compile；
- 所有扩展和控制台 JavaScript `node --check`；
- pytest 全量通过；
- 管理员 API 未登录返回 401，账号密码登录后可访问，退出后 session 失效；
- 旧 `CHAT2API_API_KEY` 不能调用 `/v1/*`，也不能代替管理员 session；
- 业务 managed API Key 仍可正常调用 `/v1/models` 和请求链路；
- 新配对码首个设备可绑定，同配对码不同 `device_id` 必须拒绝；
- 已配对设备重连不需要配对码；管理员断开后设备不能接入，恢复允许后可自动重连；
- 配对/hello/状态上报会更新 `account_type`，扩展列表能显示 Free / 付费 / 未识别；
- `gpt-5.5-mini` 有空闲 Free 扩展时优先 Free 且不操作模型/推理 UI；没有 Free 时回退 `gpt-5.5 + low/极速`；
- mini 路由不会覆盖普通付费文本模型的 API Key 粘性绑定；付费模型不会被路由到明确的 Free 扩展；
- 两台普通扩展在线时，同一 API Key 首次随机分配、后续稳定复用同一扩展；旧绑定离线/禁用时才迁移；
- 显式 `client_id` 会覆盖并更新普通 API Key 粘性绑定；
- Chat Completions、Responses、Images、Voice、Realtime Voice 共享普通粘性设备路由；
- Chat Completions / Responses / Legacy Completions 的 `gpt-5.5` / `gpt-5.6-sol` 省略推理参数时必须使用 `medium`；
- 模型广场覆盖全部公开模型且实时状态正常；
- `gpt-5.5` 与 `gpt-5.6-sol` 的 `极速 / 中 / 高` 路由回归通过；
- 服务端控制台北京时间不得二次转换；
- 扩展 popup 显示版本来自 manifest；
- 请求诊断 `server_version` 等于当前服务端版本；
- 新生成/导出的时间字段包含 `+08:00`；
- 120 秒 idle 封账仍按真实 120 秒执行。
