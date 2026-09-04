# chat2api 版本契约

chat2api 同时包含服务端 Python 包、分层运行时/控制台、Chrome Bridge 和实时语音协议。它们是不同兼容面，**不再要求使用同一个版本号**，但必须通过统一契约明确各自含义。

## 当前版本面

- Python package：`0.7.1`
- Server runtime / console：`0.22.58`
- Chrome Bridge wire protocol：`0.8.1`
- Chrome Bundle / manifest：`0.8.27`
- Realtime Voice protocol：`chat2api-live-v1`
- 生产入口：`app.entry:app`

## 为什么有多个版本号

`app/main.py` 是历史基础应用层，后续能力通过 `app/entry.py` 依序安装兼容补丁形成最终生产运行时。因此基础层中的旧 `APP_VERSION` 不能再被当作生产版本号。

各版本面的语义如下：

- **Python package version**：Python 发布/安装层版本，必须与 `app.__version__` 和 `pyproject.toml` 一致。
- **Server runtime / console version**：最终 `app.entry:app` 在全部兼容层安装完成后的运行时版本。
- **Chrome Bridge wire protocol version**：服务端与浏览器扩展之间的兼容协议版本。
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
    "runtime_version": "0.22.58",
    "expected_runtime_version": "0.22.58",
    "entrypoint": "app.entry:app",
    "runtime_aligned": true
  },
  "chrome_bridge": {
    "version": "0.8.1",
    "bundle_version": "0.8.27",
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
8. Worker 内容运行时升级时，manifest、bundle marker、content runtime contract、programmatic bootstrap 和 background runtime preflight 必须在同一次发布中对齐；涉及页面 MAIN-world 能力时还必须把 MAIN-world marker 纳入 runtime contract。