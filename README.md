# chat2api

把已登录 ChatGPT 的 Chrome 页面转换为可远程调用的浏览器 API 桥，提供 OpenAI 风格文本接口、多模态、图片、普通 Voice 和 GPT Live 实时语音能力。

> chat2api 是浏览器自动化桥接，不是 OpenAI 官方 API。ChatGPT 页面结构、模型菜单、上传控件、Images 或 Voice UI 变化都可能影响浏览器侧执行。请只在你自己的浏览器和账号上使用，并保护管理员密码、业务 API Key、扩展配对码和扩展令牌。

## 当前版本契约

chat2api 有多个独立兼容面，不再用一个版本号混合表示全部组件：

- Python package：`0.7.1`
- Server runtime / console：`0.21.4`
- Chrome Bridge：`0.7.8`
- Realtime Voice protocol：`chat2api-live-v1`
- 生产入口：`app.entry:app`

生产服务提供机器可读版本接口：

```text
GET /version
```

完整版本规则见 `docs/VERSIONING.md`。

**不要把 `app/main.py` 中历史基础层的 `APP_VERSION` 当作生产运行时版本。** Docker 和正式部署入口均使用 `app.entry:app`，由它安装当前全部兼容层后形成最终运行时。

## 当前架构

当前主运行链只需要服务端和 Chrome Bridge：

```text
External App / Bot / SDK
        |
        | HTTP / SSE / WebSocket
        v
chat2api Server (app.entry:app)
        |
        | authenticated WebSocket
        v
Chrome + chat2api extension + logged-in ChatGPT
        |
        v
ChatGPT Web / Images / Voice / WebRTC
```

Windows `desktop_client` 已退出当前主运行链并保留作历史参考。Chrome 进程必须已经运行，扩展必须在线，ChatGPT 页面必须处于已登录状态；如果没有可用扩展，服务端会返回离线/容量错误，而不会自行启动整个 Chrome 进程。

Chrome Bridge 使用同一套 Manifest V3 扩展代码支持 Windows、Linux 和 macOS。Linux 推荐使用独立、持久化的 Chrome/Chromium Profile 保存扩展身份与 ChatGPT 登录态；首次登录由用户在可见窗口中手动完成，不保存 ChatGPT 密码，也不自动处理 CAPTCHA/2FA。详细方案见 `docs/EXTENSION_NETWORK_LINUX.md`。

## 当前公开模型

文本与多模态：

```text
gpt-5.6-sol
gpt-5.5
gpt-5.5-mini
```

图片：

```text
gpt-image
```

语音：

```text
gpt-live
gpt-live-mini
```

`gpt-live-mini` 当前是 `gpt-live` 的兼容别名，两者使用同一条 ChatGPT Voice / WebRTC 浏览器执行链路；当前不声明 mini 在速度、音质、成本或资源占用上存在独立差异。

`default` 和 `chatgpt-web` 已不再作为公开文本模型 ID。调用方应显式使用当前模型目录中的 canonical ID。

所有公开 GPT 模型 ID 会规范为小写、连字符形式，例如：

```text
GPT-5.5 Mini  -> gpt-5.5-mini
GPT-5.6 Sol   -> gpt-5.6-sol
```

## 文本 / Responses / Completions

当前提供：

```text
POST /v1/chat/completions
POST /v1/responses
POST /v1/completions
GET  /v1/models
GET  /v1/models/{id}
```

`gpt-5.6-sol` 和 `gpt-5.5` 支持 OpenAI 风格推理强度：

```text
low     -> ChatGPT 极速
medium  -> ChatGPT 中
high    -> ChatGPT 高
```

调用方省略 reasoning 时，当前运行时统一使用 `medium`，不继承浏览器页面上一次手工留下的推理强度。

示例：

```bash
curl -N https://YOUR_HOST/v1/chat/completions \
  -H "Authorization: Bearer YOUR_MANAGED_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"gpt-5.6-sol",
    "reasoning_effort":"medium",
    "messages":[{"role":"user","content":"你好"}],
    "stream":true
  }'
```

## GPT-5.5 Mini / Free 账户路由

`gpt-5.5-mini` 是面向 ChatGPT Free 账户的逻辑模型，并声明：

```text
text
vision
file-understanding
```

路由规则：

1. 有在线、空闲、已识别为 Free 的扩展时，优先使用 Free 扩展。
2. Free 原生路径直接使用页面默认模型，不打开模型或 reasoning UI。
3. 没有可用 Free 扩展时，可路由到其它可用扩展，实际执行 `gpt-5.5 + low/极速`。
4. 无论 Free 原生还是付费回退，对外响应模型 ID 都保持 `gpt-5.5-mini`；实际执行细节放在 chat2api diagnostics 中。
5. Mini 的模型专用路由不会覆盖普通付费模型的 API Key -> Extension 粘性绑定。

更多规则见 `docs/FREE_ACCOUNT_ROUTING.md`。

## 文件、视觉与多模态

先上传文件：

```bash
BASE64_DATA="$(base64 -w0 photo.png)"

curl https://YOUR_HOST/v1/files \
  -H "Authorization: Bearer YOUR_MANAGED_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"filename\":\"photo.png\",\"mime_type\":\"image/png\",\"data_base64\":\"${BASE64_DATA}\",\"purpose\":\"vision\"}"
```

然后在 Chat Completions 中引用：

```json
{
  "model": "gpt-5.5-mini",
  "messages": [
    {"role": "user", "content": "请分析附件内容"}
  ],
  "attachments": [
    {"file_id": "file_xxx"}
  ],
  "stream": true
}
```

OpenAI 风格 `image_url` 的 base64 `data:` URL 也可作为图片输入。为避免服务端 SSRF，chat2api 不主动抓取任意远程 `http(s)` 图片 URL；请使用 base64 data URL 或 `/v1/files`。

当前是否能理解某种具体文件/视频格式，最终仍取决于登录账号对应 ChatGPT Web 的当前能力。视频传输存在，但不作为稳定能力承诺。

## 图片生成

模型：

```text
gpt-image
```

接口：

```text
POST /v1/images/generations
```

示例：

```bash
curl https://YOUR_HOST/v1/images/generations \
  -H "Authorization: Bearer YOUR_MANAGED_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"gpt-image",
    "prompt":"生成一张月光下的小狐狸",
    "response_format":"b64_json"
  }'
```

支持可选参考图片附件。浏览器能读取新生成图片字节时优先返回 `b64_json`；无法读取时保留 URL fallback 和诊断信息。当前 `n` 固定为 1。

## 普通 Voice

文本生成语音：

```text
POST /v1/audio/speech
```

单轮音频输入 / Voice 输出：

```text
POST /v1/audio/conversations
```

两者都通过 Chrome 中真实 ChatGPT Voice / WebRTC 执行并返回捕获的音频与 transcript。普通 Voice 与 GPT Live 都需要扩展在线、ChatGPT Voice 可用以及浏览器允许建立 WebRTC。

## GPT Live 实时双向语音

实时入口：

```text
WS /v1/audio/realtime
```

协议：

```text
chat2api-live-v1
```

Native / Android / iOS / Desktop WebSocket 客户端可以在握手时使用 managed API Key：

```text
Authorization: Bearer YOUR_MANAGED_API_KEY
```

浏览器原生 `WebSocket` 不能设置 Authorization Header，因此先调用：

```text
POST /v1/audio/realtime/sessions
```

取得 60 秒一次性的 session token，再连接：

```text
wss://HOST/v1/audio/realtime?session_token=rt-chat2api-...
```

会话第一帧：

```json
{
  "type": "session.start",
  "model": "gpt-live",
  "instructions": "请进行自然、简短的实时语音对话"
}
```

音频格式：

```text
上行：PCM16 little-endian / mono / 16000 Hz
下行：PCM16 little-endian / mono / 24000 Hz
```

活动中的 GPT Live 会话还支持文本控制输入：

```json
{"type":"input_text","text":"把刚才内容整理成三点"}
```

也支持 OpenAI 风格的 `conversation.item.create` 输入形状、`response.cancel`、barge-in、ping 和 session finish。

**`chat2api-live-v1` 是 chat2api 自己的协议，目前不是 OpenAI Realtime API wire-compatible 协议。** 详细说明见 `docs/REALTIME_VOICE.md`。

## 扩展并发与 Conversation Workers

服务端采用统一并发计数：文本、视觉、文件、图片、普通 Voice 和 GPT Live 每个活动请求都占 1 个并发槽。

默认：

```text
max_concurrency = 3
```

控制台可配置范围：

```text
1 - 32
```

配置持久化到：

```text
data/concurrency.json
```

服务端把当前 worker limit 下发给 Chrome Bridge；同一逻辑 API Key 可以分配多个独立 conversation worker/tab，降低同扩展并发请求互相覆盖的风险。

降低并发值不会取消已经开始的请求，新请求会等待容量；提高并发值会立即让后续请求使用新的容量上限。

## Model Affinity 与 Warm Pool

服务端根据最近请求历史统计常用文本 `model + reasoning` 组合，并向扩展提供最多两个 affinity preset。

Chrome Bridge 默认每 10 分钟刷新 affinity，并为高频组合准备最多两个 warm slot。请求到达时优先命中已准备且被动验证通过的精确组合，以减少冷页面加载和模型 UI 操作。

从 Chrome Bridge 0.7.8 起，浏览器启动后仍会立即尝试连接服务端；只有认证 WebSocket 已被服务端接受、浏览器出口国家为非 `CN`，并且当前 Chrome Profile 已被动确认存在可用 ChatGPT Composer 时，才允许后台主动创建 warm window。若当前没有 ChatGPT 页面，扩展会先创建一个未聚焦的登录就绪检测窗口；已登录时确认 Composer 后自动切入 warm pool，需要登录时 popup 可复用并聚焦该窗口让用户手动完成登录/CAPTCHA/2FA。中国大陆、离线、网络探测失败或登录状态未确认时不主动预热，但真实 API 请求到来后的按需创建兜底保持不变。

最终发送 prompt 前，模型和 reasoning 的真实页面状态验证仍然是权威结果；预热或缓存不能绕过最终验证。

## 管理员、API Key 与扩展配对

从 v0.17 起，管理员身份、业务 API Key 和扩展身份已经分离。

管理员控制台使用：

```text
CHAT2API_ADMIN_USERNAME
CHAT2API_ADMIN_PASSWORD
```

登录后由服务端签发 HttpOnly session cookie。

业务 `/v1/*` 调用只使用控制台创建的 managed API Key。旧的 `CHAT2API_API_KEY` 不再作为管理员或业务调用凭据，只保留作一次升级迁移输入。

扩展配对码也在管理员控制台创建和管理。首次成功配对后扩展保存自己的 `device_id`、`client_id` 和 client token，后续浏览器重启/扩展重载使用设备凭据自动重连。

`CHAT2API_PAIRING_CODE` 同样只保留作旧版本一次性迁移输入。

## API Key -> Extension 粘性路由

普通请求默认保持业务 API Key 对 Chrome Extension 的粘性：

1. 第一次请求从可用扩展中选择一台并记录。
2. 后续继续使用同一扩展，保持浏览器环境连续性。
3. 原扩展离线/禁用时才迁移到其它可用扩展。
4. 显式 `client_id` / `X-Chat2API-Client` 可以指定扩展。
5. `gpt-5.5-mini` 的 Free 优先路由是独立的模型专用规则，不污染普通付费模型粘性绑定。

Chat、Responses、Images、Voice 和 Realtime Voice 共用同一套路由基础。

## 部署

复制环境配置：

```bash
cp .env.example .env
```

至少设置强管理员密码和公网地址，然后：

```bash
docker compose up -d --build
```

Docker 生产入口为：

```text
uvicorn app.entry:app
```

公网部署请放在 HTTPS/WSS 反向代理后。SSE 建议关闭代理缓冲；GPT Live 还要求代理支持 WebSocket Upgrade，并避免把长连接按普通短 HTTP 超时强制断开。

入口：

```text
/admin       服务端控制台
/developers  开发文档
/docs        FastAPI Swagger/OpenAPI
/healthz     健康状态
/version     版本契约
```

## Chrome Bridge

同一扩展包支持 Windows、Linux 和 macOS；Linux 无需单独编译扩展。Linux 生产 worker 推荐一个持久 Chrome/Chromium Profile 对应一个扩展身份和一个 ChatGPT 登录态，完整说明见 `docs/EXTENSION_NETWORK_LINUX.md`。

1. 打开 `chrome://extensions/`。
2. 开启开发者模式。
3. 加载仓库中的 `chrome_extension`。
4. 登录 `/admin` 创建扩展配对码，并在扩展 popup 填写服务地址、配对码和扩展名称完成首次配对。
5. popup 若显示 `ChatGPT：需要登录` 或登录状态未确认，点击 `打开 ChatGPT 登录窗口`，在可见窗口中手动完成首次登录、CAPTCHA 和 2FA。
6. 等待 popup 显示 `ChatGPT：已登录，可用 · Composer 已确认`；后续同一 Chrome Profile 会复用正常网页登录态。
7. 配对成功后使用设备凭据自动重连，不需要每次重新输入配对码。
8. 运行一次 popup 的页面 Smoke Test；Linux 建议再重启一次浏览器验证 Profile 登录态、扩展自动重连和外网主动预热均可恢复。

扩展 popup 的版本显示直接读取 `chrome.runtime.getManifest().version`，不要通过硬编码文字判断当前扩展版本。

## 请求记录、诊断与测试场

请求记录和自动测试会持续记录：

- request / response ID
- API Key ID（不保存原始 secret）
- 模型和请求类型
- 附件数量
- 首 Token / 总耗时等 timings
- 浏览器 diagnostics
- 估算 usage
- 错误信息

默认不保存完整用户 Prompt 或 ChatGPT 回复正文。

主要数据：

```text
data/api_keys.json
data/request_history.jsonl
data/test_runs.jsonl
data/files/
data/concurrency.json
```

Chrome Bridge 自身运行日志静默保存在 `chrome.storage.local`，按 API Key sessionize，并以完整 JSONL 记录边界分卷；手动导出只是查看/诊断入口，不是日志记录开关。

## Token usage

ChatGPT Web 不提供可作为 OpenAI 官方账单 usage 的权威 token 计数，因此 chat2api 使用本地 `chat2api-heuristic-v1` 估算。

响应中的标准数字字段会填充，但 metadata 会明确标记：

```json
{
  "estimated": true,
  "estimator": "chat2api-heuristic-v1"
}
```

不要把它当作 OpenAI 官方计费数据。

## 时间标准

chat2api 的人可读运行时间、控制台时间、测试报告和扩展运行日志统一使用 Asia/Shanghai（北京时间，UTC+08:00）。耗时、Unix epoch、`performance.now()` 等绝对/单调计时不进行时区换算。

详细开发要求见 `docs/DEVELOPMENT.md`。

## CI / 回归

仓库的 CI 和回归测试覆盖方向包括：

- Python compile
- pytest
- Chrome Extension 关键 JavaScript `node --check`
- Admin Console JavaScript `node --check`
- 模型路由和 reasoning
- Free account routing
- 多模态
- GPT Live
- worker dispatch
- model affinity / warm pool
- 网络区域与 Linux 平台检测
- ChatGPT 登录就绪检测和首次登录窗口协调
- 并发配置
- 性能关键路径
- 版本契约

版本相关改动必须保持 `tests/test_runtime_contract.py` 通过，防止 `app.__version__`、`pyproject.toml`、Chrome manifest、Live protocol 和 README 再次漂移。

## 已知限制

- 这是 ChatGPT Web 自动化桥，不是官方 OpenAI API；DOM/UI 改版是主要兼容风险。
- GPT Live 当前使用 `chat2api-live-v1`，不是 OpenAI Realtime wire-compatible。
- Chrome 整个进程关闭时，当前服务端不会自行启动浏览器；至少一个已配对扩展必须在线。
- 图片结果抓取依赖浏览器页面中新生成的资源，不能读字节时可能返回 URL fallback。
- 视频文件可上传，但视频理解仍不作为稳定能力保证。
- Token usage 为本地估算。
- 历史 `v7...v21.4` patch stack 仍存在；新代码应优先进入正式模块，避免继续无边界叠加全局 middleware / wrapper / MutationObserver。

## 开发原则

1. 生产行为以 `app.entry:app` 为准，不只看 `app/main.py`。
2. 修改旧函数前检查后续 patch 是否重新 wrapper/覆盖。
3. Chrome background script 的加载顺序属于运行逻辑的一部分。
4. 任何模型快速路径都不能绕过发送前最终 model/reasoning 验证。
5. 不新增观察整个控制台并在 callback 中反复重写 DOM 的无限 MutationObserver。
6. 新功能同时补 pytest、关键 JS syntax check 和运行 diagnostics。
7. 稳定能力逐步从历史 patch stack 收敛到正式模块，而不是继续无限新增 patch。
