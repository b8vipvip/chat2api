# chat2api 版本契约

chat2api 同时包含服务端 Python 包、分层运行时/控制台、Chrome Bridge 和实时语音协议。它们是不同兼容面，**不再要求使用同一个版本号**，但必须通过统一契约明确各自含义。

## 当前版本面

- Python package：`0.7.1`
- Server runtime / console：`0.22.189`
- Chrome Bridge：`0.22.189`
- Chrome Bundle / manifest：`0.22.189`
- Realtime Voice protocol：`chat2api-live-v1`
- 生产入口：`app.entry:app`

## 为什么有多个版本号

`app/main.py` 是历史基础应用层，后续能力通过 `app/entry.py` 依序安装兼容补丁形成最终生产运行时。因此基础层中的旧 `APP_VERSION` 不能再被当作生产版本号。

各版本面的语义如下：

- **Python package version**：Python 发布/安装层版本，必须与 `app.__version__` 和 `pyproject.toml` 一致。
- **Server runtime / console version**：最终 `app.entry:app` 在全部兼容层安装完成后的运行时版本。
- **Chrome Bridge version**：服务端与浏览器扩展之间的兼容协议版本。
- **Chrome Bundle version**：`chrome_extension/manifest.json` 中实际交付的浏览器扩展包版本。
- **Realtime Voice protocol version**：外部实时语音客户端所依赖的 wire protocol 版本；当前为 chat2api 自定义协议，不等同于 OpenAI Realtime API wire protocol。

## 机器可读接口

生产服务提供：

```text
GET /version
```

当前正式契约的关键字段示例：

```json
{
  "object": "chat2api.version",
  "contract_version": 1,
  "server": {
    "package_version": "0.7.1",
    "runtime_version": "0.22.189",
    "expected_runtime_version": "0.22.189",
    "entrypoint": "app.entry:app",
    "runtime_aligned": true
  },
  "chrome_bridge": {
    "version": "0.8.1",
    "bundle_version": "0.22.189",
    "multimodal_revision": 85
  },
  "protocols": {
    "realtime_voice": "chat2api-live-v1"
  }
}
```

`runtime_aligned=false` 表示最终 FastAPI app 的实际版本与代码中声明的运行时契约出现漂移，应视为发布检查失败信号。

## 修改规则

以后涉及版本升级时必须同时遵守：

1. Python package 版本变化时，同步修改 `app/__init__.py` 和 `pyproject.toml`。
2. Chrome Bundle 发布时，以 `chrome_extension/manifest.json` 为真值，并同步 `app/runtime_contract.py` 中的 Bundle contract；wire protocol 仅在桥接协议兼容面变化时升级。
3. Server runtime / console 版本变化时，同步最新运行层和 `app/runtime_contract.py`。
4. Realtime wire protocol 只有在外部客户端协议兼容性发生变化时才升级，不随普通服务端修复自动升级。
5. `app/entry.py` 必须最后安装 `runtime_contract`，确保 `/version` 描述的是最终生产 app，而不是历史基础层。
6. README 只引用版本契约中的当前值，不再单独维护一套互相矛盾的“当前版本”。
7. `tests/test_runtime_contract.py` 必须保持通过，用来阻止 package、manifest、protocol 和文档再次漂移。
8. Worker 内容运行时升级时，bundle marker、content runtime contract、programmatic bootstrap 和 background runtime preflight 必须在同一次发布中对齐；如果采用兼容热修复并保持 manifest 版本不变，服务端 Worker 自动同步必须依据 `chrome_extension/` 路径变化强制刷新 Worker payload。涉及页面 MAIN-world 能力时还必须把 MAIN-world marker 纳入 runtime contract。
9. 自动处理 ChatGPT 页面弹窗只能执行无副作用的关闭/跳过动作；涉及授权、连接健康数据、付款、登录、验证、删除账号等动作必须保持人工确认，且 runtime preflight 必须把当前 UI hygiene revision 纳入完整性检查。

## v0.22.168：Worker 工具中心和图片证据探针

- 服务端及 Chrome Worker bundle 统一升级为 `0.22.168`，Python package 版本保持 `0.7.1`。
- 新增 `Worker 工具` 管理页，可分别配置全局和单个 Worker 的 P0 图片、DOM 证据探针。
- 该诊断工具默认关闭。使用时先打开图片交付探针和聊天 DOM 探针，再发起新的图片相关请求。
- 文本聊天请求由 `request-v6`、专用图片请求由 `image-v3` 在真正的请求终态之前发送只读证据；成功与失败决策仍由原有控制器独占。
- `请求记录 → 查看对话 → 查看探针` 可跳转到证据时间线，`Worker 工具` 页面可通过请求 ID 下载诊断 ZIP。
- 证据不上传提示词正文、网页完整 HTML、图片字节或带签名的图片地址。单次运行事件和存储周期设有上限。
- 保留设计文档 `docs/worker-tools-probe-platform-design-v1.0.md`。网络、模型状态及选择器专项探针仍属于 P1，不能视为已实施。
- 新版首次启用需确认服务端版本和对应 Worker 版本均为 `0.22.168`，并在 TX03 实机复测。

## v0.22.169：P1 网络、模型与页面兼容探针

- Server Runtime / Console、Chrome Bridge、Worker Bundle 统一为 `0.22.169`；Python package 保持 `0.7.1`。
- 新增 `network_evidence_v1`：网络流是否出现、摘要事件数、字节数、终态存在性和未验证中断。
- 新增 `model_tool_state_v1`：模型选择器、推理控件、图片编辑及文件上传入口的选择器命中计数。
- 新增 `selector_compat_v1`：输入框、发送/停止按钮、聊天区与图片容器等页面结构计数。
- P1 三类探针沿用 P0 的管理员身份认证、请求归属、按 Worker 策略、最大事件数、证据 ZIP 和唯一请求终态控制权。
- 默认不启用；仅采集数值/布尔值，不保存 SSE 原文、登录信息、图片 URL 或整页 HTML。
- 详细范围、剩余 P2 工作和实机复测建议见 `docs/worker-tools-probe-implementation-status.md`。

## v0.22.170：图片生成结果交付、测试场恢复与可信媒体超时扩展

- 集成 #434、#435 和 #436：图片专用请求对不存在的 SSE 字段保留空值；后台测试场从服务端请求记录核对终态，浏览器连接中断后通过管理员权限恢复已归档图片。
- 生成图片归档于独立 FileStore；后台请求轮询只返回紧凑媒体标识，通过有权限的媒体接口加载，不再在状态查询中返回多 MB base64。
- 普通 Chat Completions 的同一 Worker 请求出现可信图片生成交接证据、且用户没有明确设置 timeout 时，原默认 300 秒超时可延长至 600 秒；普通文本及显式客户端超时不变。
- Worker request-v6 与服务端 RequestBroker 分别保持唯一终态决策权；图片交接标记、canvas 与探针事件不能直接产生 completed。
- 探针支持 60/120/240/480 秒检查点。TX03 实机仍需验证真实媒体、HTTP 完成、请求历史状态和客户端预览的一致性。


## v0.22.171：TX03 图片归属、天然尺寸捕获及自适应媒体超时修复

- 集成 PR #438：图片探针确认 1254×1254 blob 图片完成加载，但 DOM 显示尺寸不足 96px，旧提取器未能将其识别为本轮图片。仅在当前请求的可信图片工具交接后，允许天然宽高均不小于 256px 的新增主聊天区图片参与验证捕获。
- 继续排除侧边栏、用户上传、历史媒体及不可信来源；仅在图片实际传输并通过媒体验证后，由 request-v6 唯一请求控制器提交完成终态。
- 测试场普通对话模式取消硬编码的 timeout:300：普通文本默认 300 秒、可信图片生成交接使用原服务端 600 秒上限；图片专用接口保持显式 600 秒。
- 自动化回归、完整 CI、契约检查和生产镜像冒烟是合并与发布门槛；TX03 实机仍需验证实际图片字节交付与测试场预览。

## v0.22.172：TX03 图片处理命令误终态与媒体来源诊断修复

- 集成 PR #440：普通聊天请求明确要求生成图片时，如果网络终态内容是完整的本地 PIL 图片处理命令（例如 `bash -lc python ... /mnt/data/...png`），该命令不是图片实体，也不能作为 HTTP 200 成功图片回复。
- 唯一请求终态控制器 `request-v6` 在限定图像创建请求和确切 PIL 命令条件下继续验证本轮新增图片；成功必须有可传输的 PNG/JPEG/WebP/GIF 字节或合格来源。历史图片、用户上传及未校验的图像节点不予交付。
- 无法取得真实图片时以明确的错误终态结束，避免把构图代码误报为已生成图片。不会新增调度器，不会改变默认超时和独占终态决策权。
- 诊断补充图片主区域候选、提交前基线命中数、候选门槛排除数和传输失败统计，不记录完整 URL、网页 HTML 或图片原始数据。
- CI 增加 TX03 实例命令回归、原 Windows Worker 终态契约迁移；实机依旧需要验证 blob 图片字节真实传输、服务端状态以及测试场预览一致性。

## v0.22.173：生成图片同域转存与云对象存储配置

- 统一聊天流式与非流式图片交付：将 Markdown 的 Base64 raster 数据解析验证、归档到服务端，并以 chat2api 自身域名下的缩略图 URL 交付。任何尚未完成的内嵌图片数据不可逐字发送给下游客户端。
- 由 SQLite 管理转存媒体索引；将原图、384px WebP 缩略图分开保存。媒体下载需要不可猜测的随机令牌；服务端加密保存 S3 兼容云存储凭据，HTTP API 不回显密钥。
- 控制台新增“转存管理”：选择本地/S3 兼容对象存储、默认转存开关、归档统计及真实写入/读取测试。AWS S3、Cloudflare R2、MinIO 可通过 S3 兼容协议配置；其他云数据库和非 S3 后端尚未实现，未标为可用。
- 专用 /v1/images/generations 的 URL 返回使用自己归档的资源，而显式 response_format=b64_json 仍遵循兼容 API 语义。
- 注意：旧 FileStore 文件上传流程仍保留独立本地存储；已生成图片的旧版历史媒体不会自动追溯迁移。新云端存储需要管理员配置凭据并点击“测试存储连接”验证。
- 配套 mv3c Markdown 图片预览修复在 mv3c PR #17，缩略图点击时打开原图。两边应升级后实机交叉验证。
## v0.22.189：每个独立请求使用独立 Worker 窗口

- 每个 `chat.request`、`image.request`、`voice.request` 使用唯一请求路由键，并从 Worker 持续预热的备用窗口池中领取不同的 ChatGPT 窗口；不同请求 ID 不复用同一聊天上下文。
- 请求成功、失败或取消后，窗口在 5 秒后关闭；窗口关闭后删除请求级路由记录，随后由唯一的持久窗口池控制器补足备用窗口。
- `voice.live.start` 使用一个专属窗口承载整个连续语音会话；只有收到 stop/断开后才开始 5 秒关闭倒计时。
- 5 秒关闭使用内存计时器，并配置持久 Chrome alarm 作为扩展 Service Worker 暂停/重启后的兜底。
- Server Runtime、Chrome Bridge、Worker Bundle 和 manifest 统一升级为 `0.22.189`；Python package 保持 `0.7.1`。

