# chat2api 版本契约

chat2api 同时包含服务端 Python 包、分层运行时/控制台、Chrome Bridge 和实时语音协议。它们是不同兼容面，**不再要求使用同一个版本号**，但必须通过统一契约明确各自含义。

## 当前版本面

- Python package：`0.7.1`
- Server runtime / console：`0.22.171`
- Chrome Bridge：`0.22.171`
- Chrome Bundle / manifest：`0.22.171`
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
    "runtime_version": "0.22.171",
    "expected_runtime_version": "0.22.171",
    "entrypoint": "app.entry:app",
    "runtime_aligned": true
  },
  "chrome_bridge": {
    "version": "0.8.1",
    "bundle_version": "0.22.171",
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
