# chat2api 常见开发 Bug 与解决方案

本文记录 **已经在真实环境重复出现过**、容易在后续功能叠加时再次引入的故障模式。开发新 Patch 前应先检查这里；出现相似现象时优先按“快速定位”执行，不要先继续叠加新的定时器、MutationObserver 或补丁层。

## 1. 管理列表在两种样式之间反复闪烁

### 典型现象

- Worker / Linux Worker / 扩展管理表格每 1～2 秒在两种内容或两套列结构之间切换。
- 同一单元格一会显示旧文本（例如 `Linux · x86-64`），一会显示新控件（例如 `并发 / 备用 / 保存 / 刷新`）。
- 用户看起来像页面“刷新”，但网络请求、Worker WebSocket 本身可能完全正常。
- 修改某个列后，另一个历史 Patch 又把该列恢复成旧内容。

### 已发生过的历史案例

1. `659b9b9b9af9cecd08370ef581656acbe4b7c963`：扩展列抖动。修复方式是移除周期性 DOM churn，改成幂等、事件驱动的列布局更新。
2. `f6cee62e2744127cadc690242257283a282b0fda`：Linux Worker 表格结构闪烁。修复方式是建立 stable table owner，避免多个层同时重建表格。
3. `882d1f7a4890c4a14ef3c16d7651c1a2e48824c3` / PR #165：Linux Worker 的代理列与网络健康 overlay 竞争重绘。修复方式是移除独立代理列，把状态统一交给网络列单一 owner。
4. v0.22.39：`admin_v21_6.js` 每 1.5 秒把 `platform` 单元格写成平台文本，`admin_v21_5.js` 每 2 秒又把同一 `platform` 单元格写成 `Worker 窗口`编辑器，形成确定性的双 owner 振荡。

### 根因模型：Multiple Render Owners

只要同一个 DOM 区域同时满足下面任意两项，就应首先怀疑 render-owner 冲突：

- 两个 Patch 都对同一 `td/th/tbody` 执行 `innerHTML = ...` / `textContent = ...`；
- 一个定时器重绘，另一个 MutationObserver 在看到变化后再次重绘；
- base renderer 重建 `tbody`，overlay 又按固定周期重建同一行；
- 新 Patch 只改“显示内容”，但旧 Patch 仍然认为自己拥有该列；
- 通过 CSS / observer / polling 多层“抢回”同一个 cell。

这不是“刷新频率太高”这么简单。真正的问题是 **同一结构存在两个或更多权威写入者**。

### 正确修复原则

**规则 A：一个结构区域只能有一个 Structural Owner。**

例如 Worker 管理页：

- `platform / Worker 窗口`：只能由 Worker-window owner 创建结构；
- `network`：只能由 health owner 更新；
- `chatgpt`：只能由 health owner 更新；
- 列排序/隐藏：由 column-layout owner 负责，但不得重写业务 cell 内容。

**规则 B：创建结构与刷新数据分离。**

推荐：

```text
第一次：创建 editor DOM
后续：只修改 input.value / textContent / data-* / class
```

禁止：

```text
setInterval(() => cell.innerHTML = fullTemplate(), 1500)
```

**规则 C：优先事件驱动，而不是结构轮询。**

可接受：

- base renderer 完成后调用明确的 refresh hook；
- `MutationObserver` 只监听 `tbody` 的直接 row replacement；
- 使用 `requestAnimationFrame` 合并一次事件循环内的多次刷新；
- 更新前比较旧值和新值，没变化就不写 DOM。

禁止：

- observer 监听自己刚刚修改的 subtree，然后再次修改同一区域；
- 两个不同脚本分别启动 1.5 秒 / 2 秒定时器维护同一列；
- 为了“抢赢旧 UI”不停重写 `innerHTML`。

### 快速定位

出现表格闪烁时按顺序检查：

1. 搜索目标列 key，例如 `platform` / `network` / `proxy`。
2. 搜索所有 `innerHTML`、`textContent`、`replaceChildren`、`appendChild`、`insertBefore`。
3. 搜索 `setInterval`、`setTimeout` 循环、`MutationObserver`。
4. 为每个写入点标记 owner；如果同一个 cell 有两个 owner，先合并 owner，不要调刷新间隔。
5. 查看最近是否有“新 overlay”叠在 stable table / health center / column-layout 上。

### 必须有的回归测试

涉及管理列表结构时至少增加：

- 静态 owner contract：同一列不能被两个功能层写结构；
- 禁止新结构 owner 使用周期性 `innerHTML` 重建；
- Node/DOM contract（适用时）：重复执行 refresh 后 editor DOM identity 不变化；
- 旧健康层不得再写已转交给新 owner 的列；
- `node --check` + pytest 全量。

## 2. 请求一直 Running，但 Worker 槽位不释放

### 典型现象

- 请求已被服务端 dispatch，控制台长期显示 `running`。
- `active_request_details` 中某请求年龄已经超过 `timeout_seconds`，`deadline_remaining_seconds = 0`，仍占用 Worker 并发槽位。
- 后续请求可能还能进入剩余槽位，但可用并发会越来越少。
- 浏览器日志里存在 routing / window creation / rate-limit guard 异常，却没有对应 `chat.error` / `image.error` / `voice.error` 回到服务端。

### v0.22.39 真实案例

`conversation_dispatch.js` 先执行 `resolveRoutedTab()`，再进入 `background.js` 自带的请求 try/catch。

当 `background_rate_limit_guard_v52.js` 在 Worker window 创建或 routed tab 分配时抛出 `chatgpt_rate_limited`，异常发生在 base handler 的 try/catch **之外**。旧代码只让 Promise rejection 最终进入 `console.error`，没有发送 terminal event，因此服务端 Broker 看不到终态，容量不会及时释放。

### 正确修复

- routed allocation / page dispatch 边界必须有最终 try/catch；
- 任何在请求建立后发生的不可恢复异常，都必须转换为 exactly-once terminal event；
- rate-limit terminal event 必须保留 `retry_after_ms` / 错误文字，供服务端建立 Worker 共享 cooldown；
- 只有 terminal event 或明确 cancel/release 才能结束 Broker active slot；
- 不能只打印浏览器 console error。

### 快速区分 UI 闪烁与 Running 请求

二者通常 **没有因果关系**：

- 管理列表闪烁发生在管理员浏览器 DOM；
- ChatGPT 请求执行发生在 Worker Chrome + Extension + WebSocket + Broker。

管理页的轮询最多增加少量管理 API 请求，不会把一个请求变成 `dispatched_waiting_submission` 数百秒。

如果请求长期 Running，优先检查：

1. `Request dispatched to extension` 后有没有 `Browser dispatch acknowledged`；
2. 有没有 `ChatGPT submission confirmed`；
3. 有没有 `generation started / delta / completed`；
4. Worker `extension-runtime.log` 是否有 route/window/rate-limit exception；
5. exception 后是否出现对应 terminal event；
6. Broker 是否打印 `request released`。

## 3. 新 Patch 开发前的 UI Owner Checklist

提交涉及管理员表格/列表的改动前必须确认：

- [ ] 我能明确说出目标 cell/tbody 的唯一 structural owner 是哪个文件。
- [ ] 没有另一个历史 Patch 在定时写同一 cell。
- [ ] MutationObserver 不会观察并反馈自己产生的 subtree 修改。
- [ ] 数据刷新不会反复销毁并重建 input/button/editor DOM。
- [ ] 更新相同数据时 DOM 写操作是幂等的。
- [ ] 新旧列迁移后，旧 owner 已显式 retire，而不是继续后台运行。
- [ ] 有自动化测试锁定 owner contract，后续版本改列时会直接 CI 失败。

如果其中任意一项回答“不确定”，先查历史 Patch 和本文件，再继续开发。
