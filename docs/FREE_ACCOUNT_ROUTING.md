# ChatGPT Free 账户路由规范

## 公开模型

`gpt-5.5-mini` 是 chat2api 面向 ChatGPT Free 账户暴露的逻辑文本模型。

- Free 账户的 ChatGPT 页面没有可选择的模型 family 和推理强度控件。
- 当请求 `gpt-5.5-mini` 且存在在线、空闲、已识别为 `free` 的扩展时，服务端优先选择 Free 扩展。
- Free 扩展收到该请求后直接使用页面默认模型，不打开或操作模型/推理强度 UI。
- 当没有可用 Free 扩展时，服务端从其它在线、空闲扩展中随机选择一个；扩展实际执行 `gpt-5.5 + low/极速`。
- 无论走 Free 原生路径还是付费账户回退路径，对外 OpenAI-compatible 响应中的 `model` 均保持调用方请求的 `gpt-5.5-mini`。
- `gpt-5.5-mini` 不接受独立推理强度语义；调用方即使提供 `reasoning_effort`，Free 原生路径也不会操作推理 UI，付费回退固定使用 `low/极速`。

## Free 账户自动识别

Chrome Bridge 在首次配对注册、WebSocket hello 和周期状态上报时都会被动识别当前 ChatGPT 账户类型。

识别只读取当前 ChatGPT 页面已有 DOM / bootstrap 数据，不点击账户菜单，不读取或上传账号邮箱、昵称等个人信息。

服务端扩展元数据至少包含：

- `account_type`: `free | paid | unknown`
- `account_detection_version`
- `account_detection_strategy`
- `account_detection_confidence`

Free 账户状态上报时只声明 `gpt-5.5-mini` 为当前默认文本模型，并移除 `model-selection` / `reasoning-selection` 能力标记。

## 路由隔离

`gpt-5.5-mini` 的 Free 优先路由是模型专用路由，不得覆盖 API Key 的普通付费模型粘性路由，否则一次 mini 请求可能把后续 `gpt-5.5` / `gpt-5.6-sol` 错误粘到 Free 扩展。

普通 `gpt-5.5` / `gpt-5.6-sol` 请求应排除已明确识别为 Free 的扩展；仅 `paid` 或 `unknown` 扩展参与可选择模型的文本路由。

## 管理控制台

“扩展管理”中的原“绑定设备”区域统一命名为 **扩展列表**，并显示账户类型：

- `Free`
- `付费`
- `未识别`

账户类型来自扩展最近一次状态上报，并随 ChatGPT 登录账户变化自动刷新。
