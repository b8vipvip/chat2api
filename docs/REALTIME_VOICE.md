# chat2api 实时语音协议

## 模型

- `gpt-live`：当前推荐的实时双向语音模型 ID。
- `gpt-live-mini`：兼容别名。目前服务端会把它映射到 `gpt-live`，两者使用同一条 ChatGPT Voice / WebRTC 浏览器执行链路。当前不承诺 `gpt-live-mini` 在速度、音质、成本或资源占用上与 `gpt-live` 存在差异。

## 协议定位

实时入口：`WS /v1/audio/realtime`

协议版本：`chat2api-live-v1`

这是 chat2api 自己的浏览器桥实时语音协议，不是 OpenAI Realtime API 的 wire-compatible 协议。普通 OpenAI-compatible 文本、图片等接口不受影响。

## 鉴权

业务调用只使用控制台创建的 managed API Key。旧 `CHAT2API_API_KEY` 不允许直接建立实时语音 WebSocket。

Native / Android / iOS / Desktop WebSocket 客户端可以在握手时直接发送：

```text
Authorization: Bearer YOUR_MANAGED_API_KEY
```

浏览器原生 `WebSocket` 无法设置自定义 Authorization Header，因此应先：

```text
POST /v1/audio/realtime/sessions
Authorization: Bearer YOUR_MANAGED_API_KEY
```

服务端返回一个 60 秒、一次性的 `session_token`，然后浏览器使用：

```text
wss://HOST/v1/audio/realtime?session_token=rt-chat2api-...
```

不得把长期 managed API Key 直接放进 WebSocket URL。

## 会话开始

WebSocket 建立后，第一帧必须是 JSON：

```json
{
  "type": "session.start",
  "model": "gpt-live",
  "instructions": "请进行自然、简短的实时语音对话"
}
```

可选 `client_id` 用于显式指定某个在线扩展；未指定时按当前 API Key 的扩展粘性路由选择空闲扩展。

## 上行音频

`session.ready` 后，客户端持续发送 WebSocket binary frame：

- PCM16 little-endian
- 单声道
- 16000 Hz

建议每帧 20–100 ms，避免一次发送很大的音频块。

## 下行音频

服务端将 ChatGPT Voice 的远端 WebRTC 音频重采样为：

- PCM16 little-endian
- 单声道
- 24000 Hz

并作为 WebSocket binary frame 持续返回。客户端应边收边播放，不要等 `response.audio.done` 后一次性播放。

## JSON 事件

可能收到：

```text
session.ready
input_audio_buffer.speech_started
input_audio_buffer.speech_stopped
transcript.final
response.created
response.text.delta
response.audio.started
response.audio.done
response.interrupted
response.done
session.closed
error
pong
```

音频本体使用 binary frame，不放进 JSON/base64，以避免外部 APP 端额外的 base64 开销。

## 控制帧

取消当前桥接输出：

```json
{"type":"response.cancel"}
```

结束实时会话：

```json
{"type":"session.finish"}
```

保活：

```json
{"type":"ping","timestamp":1234567890}
```

## 运行条件

实时语音能否成功依赖以下条件同时满足：

1. 至少一个已配对 Chrome Bridge 当前在线且空闲；
2. 该扩展对应的 ChatGPT 页面已经登录，并且 ChatGPT Voice 功能可用；
3. 浏览器允许 ChatGPT Voice 建立 WebRTC；
4. 外部客户端按上述 PCM 格式持续发送音频，并能区分 JSON text frame 与 binary audio frame；
5. 反向代理支持 WebSocket Upgrade 且不会把长连接按普通 HTTP 请求超时强制切断。
