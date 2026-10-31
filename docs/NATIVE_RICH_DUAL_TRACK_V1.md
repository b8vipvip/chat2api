# chat2api 原生富内容副轨 v1

## 核心契约
- 主轨保持 OpenAI Chat Completions / Responses 原文本、SSE、工具调用、媒体归档和唯一终态不变。Voice 不执行 DOM 快照。
- Worker 的 content_native_content_v1.js 仅提取请求绑定的安全节点树、样式白名单和图片索引；拒绝脚本、HTML、任意样式、外部 URL 和 Blob URL。
- Worker 按进度非阻塞发布 chat.native.snapshot；由独立服务端存储处理，绝不进入 Broker 终态流。
- GET /v1/native-content/{request_id} 返回最新 JSON 快照；GET /v1/native-content/{request_id}/events 提供原生副轨 SSE，事件为 native.snapshot 和 native.end。
- 使用原 API Key Bearer 认证，服务端核对当前请求的 key_id，单张快照服务器校验上限为 1MiB、3200 节点、24 层和 192K 字符（客户端渲染亦限 3200 节点，超限应回退 Markdown）。
- 只有原 MediaArchive 验证过的资产可以通过 assets[] 提供；img.media_index 是索引，不是上游图片 URL。
- chat2api 扩展元数据含 native_content.version / url / events_url；不在标准 OpenAI content 或 delta 中插入非标准 HTML。
- MV3C 通过服务器端的消息所属权校验后代理/持久化副轨，不能把站点级 Chat2API Key 暴露给浏览器。

## 客户端与回退
客户端必须校验版本、节点树与媒体归档索引，然后使用 React 受控节点渲染或严格沙箱；绝不能直接执行 DOM 原始 HTML 或脚本。副轨缺失、失败或快照与图片顺序不匹配时，应继续显示原标准 Markdown，不能阻塞标准回复或更改终态。

## 仍须实机验收
页面改版、原生交互组件、复杂布局及响应式排版不能仅凭本 schema 保证 1:1。应在 Worker 实机对图文、表格、文件、流式与并发场景验收后逐步扩大捕获范围。
- `media_mapping_complete` 不仅对比图片总数，还要求图片索引是从 `0` 到 `archived_assets - 1` 的完整无重复集合；否则不能显示原生预览，避免错误图片位置的假 1:1。

- 33da 实机 DOM 证据：2921 个节点、一个包含 111 个子节点的助手正文容器。为避免终态仅保留早期快照，单容器子节点上限统一调整到 160（Worker、服务端、后台），仍保持整个树最多 3200 节点、24 层、1 MiB 和不完整拒绝策略；归档图片索引不完整仍不能宣称原生预览 1:1。
