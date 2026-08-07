# chat2api

把一个已登录 ChatGPT 的 Chrome 标签页转换为可远程调用、支持流式输出的 OpenAI 兼容 API。

> 这是浏览器自动化桥接，不是 OpenAI 官方 API。ChatGPT 页面结构变化可能导致选择器失效；请只在你自己的浏览器和账号上使用，并保护 API Key、配对码和扩展令牌。

## 当前能力

- `POST /v1/chat/completions`，支持流式和非流式。
- `model: "default"` 零操作路径：完全不碰模型 UI，直接使用 ChatGPT 当前选择。
- 指定模型家族与思考强度，例如 `gpt-5.6-sol-high`。
- 当前模型状态缓存 + composer 思考强度检测；相同模型连续请求可跳过模型菜单。
- 浏览器回传实际模型、是否零操作、模型/思考强度切换情况与详细耗时。
- Token 使用量采用 `chat2api-heuristic-v1` 本地估算，并明确标记 `estimated=true`。
- 服务端控制台 `/admin`：概览、API Key、请求记录、开发文档、测试场。
- 业务 API Key 可创建、停用/启用、撤销和设置有效期；服务器只保存 SHA-256 哈希。
- 请求记录支持分页、状态/模型/API Key/关键词筛选和单条诊断详情。
- 请求历史保存到 `data/request_history.jsonl`，不保存完整 prompt/response 正文。
- Chrome 现有 Profile 模式：ChatGPT 登录由用户自己维护。

## 服务端部署

```bash
cp .env.example .env
# 修改 CHAT2API_API_KEY / CHAT2API_PAIRING_CODE
docker compose up -d --build
```

公网部署请放在 HTTPS/WSS 反向代理后，并关闭 SSE 缓冲，例如 Nginx `proxy_buffering off;`。

启动后：

- `/admin`：服务端控制台
- `/developers`：开发文档入口
- `/docs`：FastAPI Swagger/OpenAPI
- `/healthz`：健康状态

### 管理员主密钥与业务 API Key

`.env` 中的 `CHAT2API_API_KEY` 是管理员主密钥，兼容调用 `/v1/*`，同时拥有管理、桌面客户端和配对相关权限。不要把它分发给普通 API 调用方。

在 `/admin` → **API Key** 页面创建业务 Key。业务 Key 形如：

```text
sk-chat2api-xxxxxxxxxxxxxxxxxxxxxxxx
```

完整明文只在创建时显示一次；服务端 `data/api_keys.json` 仅持久化哈希、前缀、状态和使用时间。业务 Key 只允许调用 `/v1/models` 与 `/v1/chat/completions`，不能读取管理面板数据、桌面 bootstrap 或 pairing code。

## Chrome 扩展

1. 打开 `chrome://extensions/`。
2. 开启开发者模式。
3. 加载仓库中的 `chrome_extension`。
4. 手动登录 ChatGPT。
5. 配对 chat2api 服务端并绑定目标标签页，或运行桌面客户端自动创建/绑定新聊天页。

## API 调用

默认模型，不触碰任何模型 UI：

```bash
curl -N https://YOUR_HOST/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "default",
    "messages": [{"role":"user","content":"你好"}],
    "stream": true
  }'
```

`model` 可以省略，省略时同样使用 `default`。`chatgpt-web` 保留为兼容别名。

指定模型：

```json
{
  "model": "gpt-5.6-sol-high",
  "messages": [{"role":"user","content":"请只回复测试成功"}],
  "stream": true
}
```

如果当前标签页已经处于同一个模型和思考强度，扩展会走 `state-match-zero-op`，不再重新打开模型菜单。

## usage 与诊断

响应中的标准数字字段：

```json
"usage": {
  "prompt_tokens": 12,
  "completion_tokens": 8,
  "total_tokens": 20
}
```

这些 Token 来自本地估算，不是 ChatGPT 官方 usage。`chat2api` 字段同时返回实际模型、耗时和调用 Key 身份：

```json
"chat2api": {
  "api_key": {
    "key_id": "key_xxx",
    "name": "Mobile App",
    "kind": "managed"
  },
  "diagnostics": {
    "actual_model": "gpt-5.6-sol-high",
    "zero_op": true,
    "model_switched": false,
    "reasoning_switched": false
  },
  "timings": {
    "first_token_ms": 12000,
    "model_selection_ms": 0,
    "total_ms": 15000
  },
  "token_usage": {
    "estimated": true,
    "estimator": "chat2api-heuristic-v1"
  }
}
```

流式响应在最终 `finish_reason: "stop"` chunk 中携带同样的 `usage` 与 `chat2api` 诊断。

## 服务端控制台

打开：

```text
https://YOUR_HOST/admin
```

输入 `.env` 中的管理员主密钥后，可以使用：

- **概览**：扩展、桌面 Agent、模型、成功率、平均首 Token、累计估算 Token。
- **API Key**：创建业务 Key、设置有效期、停用/启用、撤销，查看请求次数和估算 Token。
- **请求记录**：按状态、模型、API Key、关键词筛选；查看单条 usage、timings、diagnostics 和错误。
- **开发文档**：动态显示当前站点 Base URL、curl/Python 示例、SSE 格式、参数与错误码。
- **测试场**：选择模型、Prompt 模式与流式开关，直接在线调用 API，并查看正文、原始 SSE、usage 和诊断。

管理员主密钥只保存在控制台当前标签页的 `sessionStorage`；测试场还可以临时粘贴一个业务 API Key 验证权限和调用效果。

## 管理 API

这些接口必须使用管理员主密钥：

```text
GET    /api/admin/overview
GET    /api/admin/keys
POST   /api/admin/keys
PATCH  /api/admin/keys/{key_id}
DELETE /api/admin/keys/{key_id}
GET    /api/admin/requests
GET    /api/admin/requests/{request_id}
```

创建 Key：

```bash
curl https://YOUR_HOST/api/admin/keys \
  -H "Authorization: Bearer ADMIN_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"Mobile App","expires_in_days":90}'
```

请求记录分页与筛选：

```text
GET /api/admin/requests?limit=50&offset=0&status=completed&model=gpt-5.6&key_id=key_xxx&q=keyword
```

## 持久化文件

```text
data/clients.json           扩展注册信息与扩展令牌哈希
data/api_keys.json          业务 API Key 哈希与元数据
data/request_history.jsonl  最近请求元数据、模型、Token、耗时与错误
```

请求历史默认不保存完整 Prompt 或 ChatGPT 回复正文，只记录字符数，降低敏感内容长期落盘风险。

## 安全

- `CHAT2API_API_KEY` 与 pairing code 使用不同随机值。
- 管理员主密钥不要分发给应用或第三方，只给业务方创建独立 API Key。
- 不要直接公网暴露 8765；推荐只监听 `127.0.0.1:8765` 后由 Nginx 转发。
- 不要分享 ChatGPT Cookie、扩展令牌或桌面客户端配置。
- 撤销 API Key 后不可恢复；需要重新创建新 Key。

## 测试

仓库包含 GitHub Actions CI，PR 会自动执行 Python compile、Chrome 扩展关键 JS 语法检查和 pytest：

```bash
python -m pip install -e ".[dev]"
pytest -q
```

## 已知限制

- Token 为本地估算，不是 ChatGPT 官方 token usage。
- 模型家族在网页中不总是直接显示，因此“相同模型零操作”依赖当前标签页会话缓存；用户手动操作 composer 模型控件会主动使缓存失效。
- ChatGPT DOM、菜单结构或文案变化后仍可能需要兼容更新，但模型选择已限制在 composer 范围并采用多级 fallback。
- 当前仍以文本输入/文本输出为主，图片与 Live 语音通道后续独立实现。
