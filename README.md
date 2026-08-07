# chat2api

把一个已登录 ChatGPT 的 Chrome 标签页转换为可远程调用、支持流式输出的 OpenAI 兼容 API。

> 这是浏览器自动化桥接，不是 OpenAI 官方 API。ChatGPT 页面结构变化可能导致选择器失效；请只在你自己的浏览器和账号上使用，并保护 API Key、配对码和扩展令牌。

## 当前能力

- `POST /v1/chat/completions`，支持流式和非流式。
- `model: "default"` 零操作路径：完全不碰模型 UI，直接使用 ChatGPT 当前选择。
- 指定模型家族与思考强度，例如 `gpt-5.6-sol-high`。
- 当前模型状态缓存 + composer 思考强度检测；相同模型连续请求可跳过模型菜单。
- 手动点击 composer 模型/思考控件后自动使缓存失效，下一次指定模型重新验证。
- 浏览器回传 `actual_family`、`actual_reasoning`、`actual_model`、是否零操作、是否切换模型/思考强度。
- 请求耗时诊断：标签页就绪、状态检测、模型选择、首 token、生成、总耗时。
- Token 统计：ChatGPT 网页不提供官方 usage，因此使用 `chat2api-heuristic-v1` 估算并明确标注 `estimated=true`。
- 管理面板 `/admin`：扩展/桌面客户端、模型目录、最近请求、耗时、token、错误。
- 请求历史保存到 `data/request_history.jsonl`。
- Chrome 现有 Profile 模式：ChatGPT 登录由用户自己维护。

## 服务端部署

```bash
cp .env.example .env
# 修改 CHAT2API_API_KEY / CHAT2API_PAIRING_CODE
docker compose up -d --build
```

公网部署请放在 HTTPS/WSS 反向代理后，并关闭 SSE 缓冲，例如 Nginx `proxy_buffering off;`。

启动后：

- `/docs`：OpenAPI 文档
- `/healthz`：健康状态
- `/admin`：管理面板

管理面板本身不直接泄露数据；打开后输入 `CHAT2API_API_KEY`，浏览器只把它保存在当前标签页的 `sessionStorage`，面板数据接口仍使用 Bearer 鉴权。

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

非流式响应包含标准数字字段：

```json
"usage": {
  "prompt_tokens": 12,
  "completion_tokens": 8,
  "total_tokens": 20
}
```

同时 `chat2api` 字段会明确说明这些 token 是估算值，并返回模型与耗时诊断：

```json
"chat2api": {
  "diagnostics": {
    "requested_model": "gpt-5.6-sol-high",
    "actual_family": "gpt-5.6-sol",
    "actual_reasoning": "high",
    "zero_op": true,
    "model_switched": false,
    "reasoning_switched": false,
    "state_source": "session-cache+composer"
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

## 管理 API

```bash
curl https://YOUR_HOST/api/admin/overview \
  -H "Authorization: Bearer YOUR_API_KEY"
```

包含在线扩展、桌面客户端、模型目录、聚合统计与最近 100 个请求。请求历史 API：

```text
GET /api/admin/requests?limit=100
```

## 安全

- API Key 与 pairing code 使用不同随机值。
- 不要直接公网暴露 8765；推荐只监听 `127.0.0.1:8765` 后由 Nginx 转发。
- 不要分享 ChatGPT Cookie、扩展令牌或桌面客户端配置。
- `data/clients.json` 保存扩展令牌哈希；`data/request_history.jsonl` 保存请求元数据、模型、耗时和 token 统计，不保存完整 prompt/response 正文。

## 已知限制

- Token 为本地估算，不是 ChatGPT 官方 token usage。
- 模型家族在网页中不总是直接显示，因此“相同模型零操作”依赖当前标签页会话缓存；用户手动操作 composer 模型控件会主动使缓存失效。
- ChatGPT DOM、菜单结构或文案变化后仍可能需要兼容更新，但模型选择已限制在 composer 范围并采用多级 fallback。
- 当前仍以文本输入/文本输出为主，图片与 Live 语音通道后续独立实现。
