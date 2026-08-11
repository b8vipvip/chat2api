# chat2api 开发规范

## 时间标准：统一使用北京时间

chat2api 的产品运行环境、控制台、测试报告与扩展运行日志统一以 **Asia/Shanghai（北京时间，UTC+08:00）** 作为人可读时间标准。

### 必须遵守

- 所有新增的人可读 ISO 时间字段必须带 `+08:00`，例如：`2026-08-11T17:28:27.792+08:00`。
- 服务端持久化数据中的 `created_at`、`updated_at`、`recorded_at`、`started_at`、`finished_at`、`last_seen_at`、`last_used_at`、`revoked_at`、文件创建时间等统一使用北京时间。
- Chrome 扩展的状态时间、运行日志 `at`、run 的 `started_at` / `ended_at` / `last_activity_at`、日志 part 的 `saved_at`、日志导出 `generated_at` 统一使用北京时间。
- 读取旧版本 UTC (`Z` / `+00:00`) 持久化时间时，应在加载或对外输出时转换为 `Asia/Shanghai`，不能要求用户手工加 8 小时。
- 前端展示和导出的日志文件不得把浏览器所在时区当作 chat2api 的产品时区；即使浏览器运行在其它地区，chat2api 的产品时间仍固定为北京时间。
- Python 代码优先使用 `app/timezone_utils.py` 的 `beijing_now_iso()` / `to_beijing_iso()`。
- Chrome 扩展需要生成时间字符串时使用固定 `+08:00` 的 Beijing helper，不使用浏览器本地时区推导产品时间。

### 不应“加 8 小时”的值

以下值是时区无关的绝对计时或耗时，不应做时区换算：

- `Date.now()` / Unix epoch milliseconds；
- `chrome.alarms` 的 `when`；
- 120 秒 run idle deadline 的 epoch 数值；
- `performance.now()`；
- `*_ms` 耗时、延迟、超时和 duration。

这些字段可以额外提供对应的北京时间说明字段，但底层数值必须保持原义，否则会破坏超时、封账和性能统计。

## 模型与推理强度路由

公开文本模型 ID 只使用：

- `gpt-5.6-sol`
- `gpt-5.5`

推理强度使用 OpenAI 风格：

- `low` → ChatGPT `极速`
- `medium` → ChatGPT `中`
- `high` → ChatGPT `高`

扩展路由原则：

1. 优先被动读取当前 composer DOM 与可信同页缓存；模型和推理强度均匹配时必须走 zero-op，不重新打开菜单。
2. ChatGPT 可能把当前状态合并显示为 `5.5 高` 之类的 composer pill；被动探测必须能同时从该控件识别模型和推理强度。
3. 模型 UI 切换后，旧选择器二次打开菜单可能看不到已选模型。若 composer DOM 已明确证明目标模型，应使用被动 DOM 恢复验证，不能仅因菜单二次验证失败而中止请求。
4. 推理强度不一致时优先走快捷键、键盘和原生 range 等 no-click 路径；只有这些路径均不可用时才 click fallback。
5. 在模型和推理强度最终验证通过之前不得发送 prompt；验证通过后必须继续进入 request controller，不能因为已经完成模型切换而漏掉 prompt 注入。

## 运行日志

- 运行日志自动静默保存在 `chrome.storage.local`；导出按钮只是查看/导出入口，不是日志记录开关。
- 日志按 API Key 独立 sessionize，同 Key 最后一个请求结束后 120 秒无新请求才封账。
- 日志目标 part 大小约 200 KiB，只能在完整 JSONL 记录之间换卷，禁止拆分单条 JSON。
- 运行日志中的人可读时间直接以北京时间为 canonical time；不要再把 UTC `Z` 作为主时间再附加 `*_local` 让用户换算。

## 回归要求

涉及模型路由、推理强度、时间字段或运行日志的改动，提交前至少验证：

- Python compile；
- 所有扩展 JavaScript `node --check`；
- pytest 全量通过；
- 同模型同强度 zero-op；
- 同模型不同强度能够切换后继续发送 prompt；
- 不同模型切换完成后能够继续设置推理强度并发送 prompt；
- 新生成/导出的时间字段包含 `+08:00`；
- 120 秒 idle 封账仍按真实 120 秒执行。
