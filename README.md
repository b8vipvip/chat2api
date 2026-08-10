# chat2api

把已登录 ChatGPT 的 Chrome 页面转换为可远程调用、支持流式输出的浏览器 API 桥。

> 这是浏览器自动化桥接，不是 OpenAI 官方 API。ChatGPT 页面结构变化可能导致选择器失效；请只在你自己的浏览器和账号上使用，并保护 API Key、配对码和扩展令牌。

## 当前版本

- 服务端：`0.5.0`
- Chrome 扩展：`0.4.0`
- Windows 桌面客户端：已从当前运行链路移除/暂停开发

当前只需要两部分：

```text
chat2api server  <--- WebSocket --->  Chrome + chat2api extension + logged-in ChatGPT
```

Chrome 必须已经启动且扩展在线。API 请求本身可以让扩展创建新的 ChatGPT / ChatGPT Images 标签页，但不能在 Chrome 整个进程关闭时自行启动浏览器；此时服务端返回 503。

## 当前能力

- `POST /v1/chat/completions`：文本、视觉理解、文件理解，支持流式和非流式。
- `POST /v1/files`：把图片、PDF、文本和其它 ChatGPT 可接受文件传到服务端，再由扩展注入网页上传控件。
- OpenAI 风格 `image_url` 的 base64 `data:` URL 可直接作为聊天输入图片。
- `POST /v1/images/generations`：`model: "gpt-image"`，自动路由到 `https://chatgpt.com/images/`。
- 图片生成支持可选参考图片附件；优先返回 `b64_json`，页面资源无法直接读取时保留 URL fallback 与诊断。
- `model: "default"` 零操作路径：不碰模型 UI，直接使用 ChatGPT 当前选择。
- 指定模型和思考强度，例如 `gpt-5.6-sol-high`；相同模型连续请求可走零操作快速路径。
- Token 使用量采用 `chat2api-heuristic-v1` 本地估算，并明确标记 `estimated=true`。
- 服务端控制台 `/admin`：概览、API Key、请求记录、开发文档、自动测试场。
- 语音生成、语音对话尚未实现；测试场会明确标记 `skipped`，不会伪装成功。

## 服务端部署

```bash
cp .env.example .env
# 修改 CHAT2API_API_KEY / CHAT2API_PAIRING_CODE
docker compose up -d --build
```

公网部署请放在 HTTPS/WSS 反向代理后，并关闭 SSE 缓冲，例如 Nginx：

```nginx
proxy_buffering off;
add_header X-Accel-Buffering no;
```

入口：

- `/admin`：服务端控制台
- `/developers`：开发文档
- `/docs`：FastAPI Swagger/OpenAPI
- `/healthz`：健康状态

## Chrome 扩展

1. 打开 `chrome://extensions/`。
2. 开启开发者模式。
3. 加载仓库中的 `chrome_extension`。
4. 在这个 Chrome Profile 手动登录 ChatGPT。
5. 扩展里填写服务地址和 `CHAT2API_PAIRING_CODE`，点击“配对并连接”。
6. 可以手动绑定一个 ChatGPT 标签页；未绑定时 API 请求也可以由扩展创建新的 ChatGPT 标签页。

不再需要运行：

```powershell
.\scripts\run_desktop_client.ps1
```

旧 desktop_client / scripts 文件可以暂留在仓库用于历史参考，但服务端和扩展 0.5/0.4 的主链路都不依赖它们。

## API Key

`.env` 中 `CHAT2API_API_KEY` 是管理员主密钥。普通应用请在 `/admin` → **API Key** 中创建独立业务 Key。

新建业务 Key：

```text
sk-chat2api-xxxxxxxxxxxxxxxx
```

鉴权始终通过 SHA-256 哈希比较。为了满足管理页刷新后仍能重新复制 Key，0.5.0 起还会保存一份 **Fernet 加密密文**，加密密钥由管理员主密钥派生。管理页面重新复制时才由服务端解密。

注意：

- `data/api_keys.json` 不保存明文 Key。
- 0.4.0 以前创建的 Key 没有加密密文，无法恢复明文，但仍可正常鉴权；需要可复制副本时请新建 Key。
- 如果修改 `CHAT2API_API_KEY`，旧 Key 的哈希鉴权不受影响，但原来的加密副本将无法用新主密钥解密。
- 撤销 Key 时密文副本也会清除。

## 文本调用

```bash
curl -N https://YOUR_HOST/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"default",
    "messages":[{"role":"user","content":"你好"}],
    "stream":true
  }'
```

## 视觉理解 / 文件理解

先上传文件：

```bash
BASE64_DATA="$(base64 -w0 photo.png)"

curl https://YOUR_HOST/v1/files \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"filename\":\"photo.png\",\"mime_type\":\"image/png\",\"data_base64\":\"${BASE64_DATA}\",\"purpose\":\"vision\"}"
```

返回：

```json
{
  "id": "file_xxx",
  "filename": "photo.png",
  "mime_type": "image/png"
}
```

再调用聊天：

```json
{
  "model": "default",
  "messages": [{"role":"user","content":"请分析附件内容"}],
  "attachments": [{"file_id":"file_xxx"}],
  "stream": true
}
```

每个请求当前最多 4 个附件，每个文件最大 20 MiB。图片/PDF/文本文档等是否最终可被理解，仍取决于 ChatGPT 当前网页账号和该文件类型是否接受。

图片还支持：

```json
{
  "role": "user",
  "content": [
    {"type":"text","text":"描述这张图"},
    {"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}
  ]
}
```

为避免服务端 SSRF，当前不主动抓取任意远程 `http(s)` 图片 URL；请使用 base64 data URL 或 `/v1/files`。

## 图片生成

模型 ID：

```text
gpt-image
```

接口：

```bash
curl https://YOUR_HOST/v1/images/generations \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"gpt-image",
    "prompt":"生成一张月光下的小狐狸",
    "response_format":"b64_json"
  }'
```

执行链：

```text
API request
→ Chrome extension
→ reuse/create https://chatgpt.com/images/
→ wait for #prompt-textarea
→ optional reference upload
→ send prompt
→ monitor new generated image
→ capture image bytes when possible
→ return b64_json / URL fallback
```

参考图：先 `/v1/files` 上传，然后：

```json
{
  "model":"gpt-image",
  "prompt":"参考这张图，生成一个新版本",
  "attachments":[{"file_id":"file_xxx"}],
  "response_format":"b64_json"
}
```

当前 `n` 固定为 1。图片页面 DOM / 资源加载方式变化时可能需要继续适配。

## usage 与诊断

文本/多模态请求会返回估算 usage：

```json
"usage": {
  "prompt_tokens": 12,
  "completion_tokens": 8,
  "total_tokens": 20
}
```

并返回：

```json
"chat2api": {
  "diagnostics": {
    "actual_model": "gpt-5.6-sol-high",
    "zero_op": true,
    "attachments_count": 1,
    "attachment_prepare_ms": 430
  },
  "timings": {
    "first_token_ms": 12000,
    "model_selection_ms": 0,
    "attachment_prepare_ms": 430,
    "total_ms": 15000
  },
  "token_usage": {
    "estimated": true,
    "estimator": "chat2api-heuristic-v1"
  }
}
```

## 自动测试场

`/admin` → **测试场** 支持下拉选择：

- 文本
- 视觉理解
- 文件理解
- 图片生成
- 语音生成
- 语音对话
- 全部测试

测试场会自动：

1. 运行标准化测试用例。
2. 监测 HTTP/浏览器错误。
3. 记录首 Token、总耗时、usage 和 diagnostics。
4. 检测首包 > 30 秒、总耗时 > 90 秒、无正文、缺诊断等质量问题。
5. 生成 passed / warning / failed / skipped 结论。
6. 把报告保存到 `data/test_runs.jsonl`。
7. 在“测试记录”中查看历史报告。

语音两项当前会显示 `skipped / 尚未实现语音桥接`，用于让“全部测试”如实反映能力缺口。

## 请求记录与持久化

```text
data/clients.json           扩展注册信息与扩展令牌哈希
data/api_keys.json          业务 Key 哈希、加密副本与元数据
data/request_history.jsonl  请求模型、类型、附件数、Token、耗时、错误
data/test_runs.jsonl        自动测试报告
data/files/                 API 上传的临时/持久附件
```

请求历史默认不保存完整 Prompt 或 ChatGPT 回复正文，只记录字符数和诊断。

## 安全

- 管理员主 Key 与 pairing code 使用不同随机值。
- 不要直接公网暴露 8765；推荐只监听 `127.0.0.1:8765`，由 Nginx HTTPS/WSS 代理。
- 普通应用使用独立业务 Key，不分发管理员主密钥。
- 服务端不会主动抓取任意远程图片 URL，避免 SSRF。
- 扩展下载附件使用独立扩展 token，不需要接触业务 API Key。
- 不保存 ChatGPT Cookie、邮箱密码或验证码。

## CI

PR 会自动执行：

- Python compile
- Chrome 扩展关键 JavaScript `node --check`
- 管理控制台内联 JavaScript `node --check`
- pytest

## 已知限制

- Token 是本地估算，不是 ChatGPT 官方 token usage。
- ChatGPT DOM、模型菜单、上传控件和 Images 页面变化后可能需要适配。
- 视频文件可以通过 `/v1/files` 传输，但是否接受和是否进行视频理解取决于当前 ChatGPT 网页能力；尚未把它标记为稳定能力。
- 图片生成的最终图片抓取依赖页面中新生成资源；若浏览器无法直接读取其字节，会返回 URL fallback 和诊断。
- Chrome 进程必须已经打开；当前版本不再提供桌面客户端自动启动 Chrome。
- 语音生成和实时语音对话尚未实现。
