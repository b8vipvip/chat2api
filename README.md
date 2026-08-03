# chat2api

把一个已登录 ChatGPT 的 Chrome 标签页转换为可远程调用、支持流式输出的 OpenAI 兼容 API。

本项目参考了 `b8vipvip/ALiver` 中 `chrome_extension` 的页面控制思路：在扩展中定位 ChatGPT 输入框、写入文本、点击发送，并监听助手回复。chat2api 将其拆分为独立服务端和 Chrome 扩展，并增加远程鉴权、客户端配对、请求路由、SSE 流式返回、取消与超时控制。

> 注意：这是浏览器自动化桥接，不是 OpenAI 官方 API。ChatGPT 页面结构变化可能导致选择器失效；使用时应遵守 ChatGPT 服务条款、账号限制和所在地法律。不要把浏览器会话 Cookie、扩展令牌或配对码交给第三方。

## 架构

```text
远程程序 / OpenAI SDK
        |
        | HTTPS: /v1/chat/completions (stream=true/false)
        v
chat2api FastAPI 服务
        |
        | WSS: chat.request / chat.delta / chat.completed
        v
Chrome 扩展 -> ChatGPT 网页（自动退出语音模式并切换文本输入）
```

## 功能

- OpenAI 兼容 `POST /v1/chat/completions`
- SSE 流式响应（`stream: true`）
- 非流式完整响应
- 多扩展注册；通过 `client_id` 或 `X-Chat2API-Client` 选择目标浏览器
- 只有一个扩展在线时可自动选择
- 扩展自动切换到文本模式、填写并发送远程文本
- 页面回复增量捕获、超时、取消、断线错误回传
- API Key、独立配对码、扩展独立令牌
- Docker 与 Windows PowerShell 启动脚本

## 1. 启动服务端

```bash
cp .env.example .env
# 必须编辑 .env，修改 CHAT2API_API_KEY 和 CHAT2API_PAIRING_CODE
docker compose up -d --build
```

Windows 本地开发：

```powershell
Copy-Item .env.example .env
notepad .env
.\scripts\run_dev.ps1
```

启动后访问：

- `http://127.0.0.1:8765/docs`
- `http://127.0.0.1:8765/healthz`

远程部署必须放在 HTTPS 反向代理后面，使扩展使用 `wss://`。推荐仅开放 443，并限制防火墙来源。

## 2. 安装 Chrome 扩展

1. 打开 `chrome://extensions/`。
2. 开启“开发者模式”。
3. 点击“加载已解压的扩展程序”。
4. 选择本仓库的 `chrome_extension` 目录。
5. 打开并登录 `https://chatgpt.com/`。
6. 点击扩展图标，填写服务地址、`.env` 中的配对码和扩展名称。
7. 点击“配对并连接”。
8. 若同时打开多个 ChatGPT 标签页，在目标页点击“绑定当前 ChatGPT 标签页”。

## 3. 远程调用

非流式：

```bash
curl http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "chatgpt-web",
    "messages": [{"role": "user", "content": "用一句话介绍你自己"}],
    "stream": false
  }'
```

流式：

```bash
curl -N http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "chatgpt-web",
    "messages": [{"role": "user", "content": "写一个三句话的小故事"}],
    "stream": true
  }'
```

Python OpenAI SDK：

```python
from openai import OpenAI

client = OpenAI(
    api_key="YOUR_API_KEY",
    base_url="http://127.0.0.1:8765/v1",
)

stream = client.chat.completions.create(
    model="chatgpt-web",
    messages=[{"role": "user", "content": "你好"}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

## 多浏览器选择

查询已注册客户端：

```bash
curl http://127.0.0.1:8765/api/clients \
  -H "Authorization: Bearer YOUR_API_KEY"
```

调用时添加顶层 `client_id`，或请求头：

```text
X-Chat2API-Client: ext_xxxxx
```

## 消息历史模式

默认 `prompt_mode=last_user`：发送 system/developer 指令和最后一条 user 消息，适合让浏览器中的同一个 ChatGPT 会话继续保持上下文。

设置 `prompt_mode=full` 会把传入的全部消息序列化后一次性发送，适合无状态 API 调用，但会在长期使用中增加重复上下文。

## 安全建议

- `CHAT2API_API_KEY` 与 `CHAT2API_PAIRING_CODE` 必须使用不同的高强度随机值。
- 公网使用 HTTPS/WSS，不要直接暴露 8765 明文端口。
- 反向代理关闭缓冲，否则 SSE 看起来会“假流式”；Nginx 可设置 `proxy_buffering off`。
- 只在你自己的 Chrome 与 ChatGPT 账号上安装扩展。
- `data/clients.json` 保存扩展令牌的 SHA-256 哈希，但仍应限制服务器文件权限。
- 当前每个扩展同一时间只处理一个请求，避免多个远程调用写入同一网页会话。

## 测试

```bash
python -m pip install -e ".[dev]"
pytest -q
```

## 已知限制

- ChatGPT 前端 DOM 或按钮文案调整后，可能需要更新 `chrome_extension/content.js` 的选择器。
- 当前仅支持文本输入与文本输出，不处理图片、文件或工具调用。
- 页面生成过程中出现大幅文本重写时，流式增量可能不完全等同于最终文本；非流式结果以最终页面文本为准。
- usage 中 token 数暂时返回 0，因为网页端没有可靠的 token 计数接口。
