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


## 采样视频帧输入（chat2api-live-v1）

当前可在同一 Voice 会话中发送有序的图像帧快照。它复用已验证的 FileStore 图片附件和 ChatGPT Voice 多模态输入路径，不会把视频伪装成 PCM，也不会启动第二个文本请求。

1. 客户端从自己的摄像头/视频源按需采样 JPEG、PNG 或 WebP 帧（建议 1–2 FPS；服务端硬限制为最多 5 FPS）。
2. 使用同一 managed API Key 和 `files` scope，将每帧通过 `POST /v1/files` 上传，JSON 请求指定 `purpose: "vision"`，保存返回的 `file_id`。
3. 在已 ready 的实时 WebSocket 中发送：

```json
{
  "type": "input_video.frame",
  "item_id": "camera-frame-000001",
  "sequence": 1,
  "timestamp_ms": 1000,
  "file_id": "file_由上传接口返回的ID",
  "text": "请结合连续画面描述当前发生的事情"
}
```

4. `input_video.frame.queued` 只表示已排队；只有 `input_video.frame.sent` 才表示 Worker 已确认该帧对应的图片和文字进入 ChatGPT Voice 会话。确认事件回传同一 `sequence` 和 `timestamp_ms`。
5. 每帧必须是当前 API Key 所有的单张 JPEG/PNG/WebP 图片，单帧不超过 2 MiB。序号和时间戳必须严格递增；每个会话最多 64 帧、最长时间轴 6 小时。重复、乱序、超速、跨 Key 或不支持的图片会被拒绝。
6. 为避免在 ChatGPT 页面上传尚未完成时堆积视频帧，客户端必须等待当前帧的 `input_video.frame.sent` 确认后再发送下一帧。

**重要边界：这不是原生 WebRTC 视频轨道透传。** 每个采样帧会成为同一 Voice 会话中的独立图像输入，因此模型看到的是有时间戳的连续快照，而不是连续视频像素流；帧率、清晰度和同步精度由客户端采样策略及上传延迟决定。当前不支持 MP4/任意视频文件上传、音视频帧级同步或将摄像头轨道直接桥接进 ChatGPT WebRTC。原生视频轨道仍需单独开发和真实浏览器验收。
### 测试场

选择「语音生成」会测试 TTS 前缀作用后的 `POST /v1/audio/speech`，并检查真实语音音轨/音频字节。
选择「语音通话」会对真实的服务端 ASGI WebSocket 路由启动 ChatGPT Voice，会验证文字或附图/文件的送达以及 24kHz PCM 回包。
若上传的是 16kHz 单声道 PCM16 WAV，将同时验证上行语音；未上传则标记警告，不假造音频。
该 ASGI 测试不等于公网反向代理 WS Upgrade 通过。


## 用量与计费边界

实时语音会在会话开始时执行音频请求的 token 配额准入预留；成功结束时，服务端按已确认的用户文字/语音转写和已完成的助手文字回复估算文本 Token，并写入用量账本。输入/输出音频字节数会记录在诊断字段中，但当前 Worker/ChatGPT Voice 协议没有提供可核验的音频 Token 用量，因此不会把字节数伪造为 Token。

对有钱包计费的用户，gpt-live / gpt-live-mini 只有在管理员明确配置为零费率时才允许调度；正费率会 fail-closed，直到上游提供可验证的音频用量。文本 Token 仍按现有估算规则结算。
