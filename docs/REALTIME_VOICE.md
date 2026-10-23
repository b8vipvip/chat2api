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


## 语音通话期间的多模态输入（v0.22.182）

**语音生成**：`POST /v1/audio/speech` 的 `input` 在服务端加入管理员可编辑的 TTS 前缀，
然后进入当前 Chrome Worker 的 ChatGPT Voice；预设由后台「提示词配置 → 语音生成预设提示词」管理。
不调用任何独立的 TTS 模型。

**语音通话**：保留原有 `WS /v1/audio/realtime` 全双工 PCM 音频。
音频 binary frame 不变；语音通话中还可以发送带文件引用的用户消息，仍在同一个 Voice 会话内处理，不另起文本模型任务：

```json
{
  "type": "conversation.item.create",
  "item": {
    "id": "turn_001",
    "role": "user",
    "content": [
      {"type": "input_text", "text": "请看这张照片，直接用语音回答照片里有什么，不要结束通话。"},
      {"type": "input_image", "file_id": "file_先调用v1files上传获得ID"},
      {"type": "input_file", "file_id": "file_先调用v1files上传获得ID"}
    ]
  }
}
```

也支持 `{"type":"input_media","text":"请语音总结附件","attachments":[{"type":"input_image","file_id":"file_..."}]}`。

1. 先通过同一 API Key 调用 `POST /v1/files` 上传图片或文档，获得 `file_id`。
2. 通话中发送上述 JSON；服务端验证文件存在、API Key 所有权、MIME 类型与每轮最多 4 个附件。
3. Worker 复用现有的 Chrome 多模态附件上传控制器，确认 ChatGPT 附件已处理后发送文字，并验证新用户消息已出现在会话中。Voice WebRTC 音频不关闭或切换为另一个模型。
4. `input_media.queued` 仅表示已经投递给 Worker；`input_media.sent` 才表示网页已确认附件和本轮发送。任何 `VOICE_CALL_INPUT_FAILED` 只使该条输入失败，不会自动结束正在进行的通话。
5. ChatGPT 是否支持在**当前 Voice UI 状态**下选择并上传对应文件，是实际浏览器能力限制。若 ChatGPT 禁止该操作，返回明确的输入错误，不会伪称成功。

文件目前仅接受 PNG/JPEG/WebP/GIF、PDF、TXT、CSV、MD、JSON 和部分 Office 文档 MIME。
任意外链不会被作为文件下载；不接受跨 API Key 访问。上行语音依然只走 PCM binary frame。

### 测试场

选择「语音生成」会测试 TTS 前缀作用后的 `POST /v1/audio/speech`，并检查真实语音音轨/音频字节。
选择「语音通话」会对真实的服务端 ASGI WebSocket 路由启动 ChatGPT Voice，会验证文字或附图/文件的送达以及 24kHz PCM 回包。
若上传的是 16kHz 单声道 PCM16 WAV，将同时验证上行语音；未上传则标记警告，不假造音频。
该 ASGI 测试不等于公网反向代理 WS Upgrade 通过。
