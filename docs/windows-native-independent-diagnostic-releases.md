# Windows 原生页面诊断：独立更新机制

## 设计目标

与 Linux 的 `native-page-diagnostic-releases` 独立更新通道对齐：
每次仅修复 ChatGPT 历史 DOM 捕获、消息归属选择器、表格/图片识别、
富媒体结构导出时，不再重新构建完整 chat2api 服务端或 Windows Worker 扩展。
管理员在「Worker 工具 → 原生页面诊断」选择在线 Windows `ext_` Worker，
点击「更新诊断工具」即可安装，经 Worker 本地 SHA-256 回执确认后生效。

## 首次引导（仅一次）

1. 先把 **服务端和 Windows Chrome Worker 扩展**升级至支持热更新的版本（此版本是功能引擎的首次安装，不可省略）。
2. 需要 Chrome **135+** 提供 `chrome.userScripts.execute`；Chrome **138+** 在
   `chrome://extensions` → chat2api Worker →「详情」→ **Allow User Scripts / 允许用户脚本** 打开相应开关。
   该开关由 Chrome 管理，服务端和扩展均不能代替用户自动授权。
3. 确保扩展正常连接服务端，选择它对应的 `ext_` Worker，先「检查版本」，然后「更新诊断工具」。
   更新成功后，后台会显示独立诊断版本（如 `1.0.0`）和安装摘要；
   可执行一次历史 URL 诊断，导出 ZIP 验证 `collector_version`。
4. 更新后重启 Chrome，扩展会从 `chrome.storage.local` 校验加载已安装脚本；不需要再次点击更新。

## 后续发布流程

开发人员只修改 **`native-page-diagnostic-releases`** 分支中
`scripts/windows_native_page_collector_v1.js`，将顶部 `DIAGNOSTIC_TOOL_VERSION` 递增，
并在该分支进行 JS 语法及 DOM 模拟测试（不要修改相同版本的代码）。
生产服务端使用固定 GitHub Contents API 文件路径获取内容，
校验 Git blob SHA-1、字节大小、固定源码版本标记和 SHA-256。
没有管理员可输入的远程代码 URL；无 GitHub 访问则报错，不回退至旧文件冒充最新。

- Linux 原有脚本 `scripts/native_page_diagnostic_v1.py` 和发布流程不变。
- Windows 点击更新只通过已登录、已认证且在线的 Worker WebSocket 发送工具源码和摘要；
  扩展再次计算 SHA-256，检查版本不得倒退或同版本变更，检测 User Scripts 权限，
  保存到扩展本地存储后返回受身份/更新 ID 绑定的结果。
- **只有**本地返回与服务端摘要完全一致、`updated=true/false` 合法，
  服务端才报告安装成功。未收到回执、连接断开、用户脚本权限关闭都属于明确失败。
- 正式诊断仍由内置扩展负责创建/保护/关闭诊断窗口，读取历史页面则优先用
  `chrome.userScripts.execute({target,js:[{code}],world:"USER_SCRIPT"})` 执行独立工具。
  只在经过验证的 ChatGPT /c/ URL 上触发，仍有大小、角色边界、域名、分块、身份限制。
- 未安装独立工具的 Worker 会运行内置只读 DOM 采集器。安装后如 User Scripts 权限关闭，
  不会悄然改用旧采集器假装新版本成功，而是报告权限/执行错误。

## 范围限制

Chrome Manifest V3 明确禁止通过 `eval`、远程 `importScripts`、字符串式
`chrome.scripting.executeScript` 等方式更新扩展后台任意 JavaScript。
`userScripts` 是官方允许的受限例外，目的是在隔离的页面上下文执行用户授权的脚本。

因此，**不重新部署主服务和扩展**适用于页面采集算法、DOM 探针、选择器、
视觉证据结构这类属于 userScripts 页面脚本的修复；
若要修改扩展后台的 WebSocket、窗口生命周期、权限或协议，则仍需要升级扩展包。
若未来希望这些也独立热修复，应另行设计一次性安装的 Windows 本地原生辅助程序，
不应通过 Web Worker 远程执行任意下载代码来绕过 Chrome 权限。

Chrome 官方文档：
- https://developer.chrome.com/docs/extensions/reference/api/userScripts/
- https://developer.chrome.com/docs/webstore/program-policies/mv3-requirements
