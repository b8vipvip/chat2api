# chat2api 常见开发 Bug 与解决方案

本文记录 **已经在真实环境重复出现过**、容易在后续功能叠加时再次引入的故障模式。开发新 Patch 前应先检查这里；出现相似现象时优先按“快速定位”执行，不要先继续叠加新的定时器、MutationObserver 或补丁层。

## 1. 管理列表在两种样式之间反复闪烁

### 典型现象

- Worker / Linux Worker / 扩展管理表格每 1～2 秒在两种内容或两套列结构之间切换。
- 同一单元格一会显示旧文本（例如 `Linux · x86-64`），一会显示新控件（例如 `并发 / 备用 / 保存 / 刷新`）。
- 用户看起来像页面“刷新”，但网络请求、Worker WebSocket 本身可能完全正常。
- 修改某个列后，另一个历史 Patch 又把该列恢复成旧内容。
- DevTools Network 可能同时出现稳定周期的管理接口请求，例如 `/api/admin/extensions` 与 `/api/admin/capacity-v57` 成对重复。

### 已发生过的历史案例

1. `659b9b9b9af9cecd08370ef581656acbe4b7c963`：扩展列抖动。修复方式是移除周期性 DOM churn，改成幂等、事件驱动的列布局更新。
2. `f6cee62e2744127cadc690242257283a282b0fda`：Linux Worker 表格结构闪烁。修复方式是建立 stable table owner，避免多个层同时重建表格。
3. `882d1f7a4890c4a14ef3c16d7651c1a2e48824c3` / PR #165：Linux Worker 的代理列与网络健康 overlay 竞争重绘。修复方式是移除独立代理列，把状态统一交给网络列单一 owner。
4. v0.22.39：`admin_v21_6.js` 每 1.5 秒把 `platform` 单元格写成平台文本，`admin_v21_5.js` 每 2 秒又把同一 `platform` 单元格写成 `Worker 窗口`编辑器，形成确定性的双 owner 振荡。
5. v0.22.40：虽然 `platform / Worker 窗口` 已经只有一个 structural owner，但 health owner 仍每 1.5 秒无条件执行 `textContent = ...`，而 `admin_extension_columns.js` 又用 `subtree:true` 观察整张表的 childList。未变化的文本节点也被反复替换，触发 column-layout observer 重新布局；同时 health poll 每次再调用 Worker-window owner，形成 `/extensions -> /capacity-v57` 的链式管理请求。这属于 **Self-invalidating Presentation Poll**，本质仍是 owner 边界不清晰。

### 根因模型：Multiple Render Owners

只要同一个 DOM 区域同时满足下面任意两项，就应首先怀疑 render-owner 冲突：

- 两个 Patch 都对同一 `td/th/tbody` 执行 `innerHTML = ...` / `textContent = ...`；
- 一个定时器重绘，另一个 MutationObserver 在看到变化后再次重绘；
- base renderer 重建 `tbody`，overlay 又按固定周期重建同一行；
- 新 Patch 只改“显示内容”，但旧 Patch 仍然认为自己拥有该列；
- 通过 CSS / observer / polling 多层“抢回”同一个 cell。

这不是“刷新频率太高”这么简单。真正的问题是 **同一结构存在两个或更多权威写入者**。

### 第二种根因：Self-invalidating Presentation Poll

即使 structural owner 已经唯一，也可能继续闪烁：

```text
poller 定时读取状态
    ↓
无条件执行 cell.textContent = sameValue
    ↓
旧 text node 被替换，产生 childList mutation
    ↓
全表 subtree MutationObserver 认为布局变了
    ↓
column layout / overlay 再执行
    ↓
下一个 poll 重复
```

因此“只保证一个 `innerHTML` owner”还不够，**展示层写操作必须幂等，observer 也必须区分结构变化与纯展示变化**。

如果 Network 同时稳定出现：

```text
/api/admin/extensions
/api/admin/capacity-v57
/api/admin/extensions
/api/admin/capacity-v57
...
```

先检查是否存在这样的跨 owner fan-out：

```text
health poll
  -> fetch extensions
  -> render health
  -> call worker-window refresh hook
  -> fetch capacity
```

一个周期性 owner 不应隐式启动另一个 owner 的周期性请求链。

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

**规则 C：所有 presentation 写入必须幂等。**

推荐：

```js
if (cell.textContent !== nextText) cell.textContent = nextText;
if (input.value !== nextValue) input.value = nextValue;
```

不要写成：

```js
cell.textContent = nextText; // 即使值完全没变也重建 text node
```

尤其当祖先节点上存在 `MutationObserver({childList:true, subtree:true})` 时，无条件 `textContent` 会把“状态轮询”变成“结构变更事件源”。

**规则 D：周期性数据 owner 不得链式启动第二个周期性 owner。**

- health poll 只刷新 health；
- Worker-window capacity 在首次加载、配置保存、显式刷新或真实 row replacement 时更新；
- 如果以后需要高频联合状态，建立一个明确的 snapshot owner / endpoint，而不是 `poll A -> refresh B -> fetch C`。

**规则 E：优先事件驱动，而不是结构轮询。**

可接受：

- base renderer 完成后调用明确的 refresh hook；
- `MutationObserver` 只监听 `tbody` 的直接 row replacement；
- 使用 `requestAnimationFrame` 合并一次事件循环内的多次刷新；
- 更新前比较旧值和新值，没变化就不写 DOM；
- 页面不可见时暂停健康轮询。

禁止：

- observer 监听自己刚刚修改的 subtree，然后再次修改同一区域；
- 两个不同脚本分别启动 1.5 秒 / 2 秒定时器维护同一列；
- 为了“抢赢旧 UI”不停重写 `innerHTML`；
- health poll 每次都调用另一个 owner 的远程数据刷新。

### 快速定位

出现表格闪烁时按顺序检查：

1. 搜索目标列 key，例如 `platform` / `network` / `proxy`。
2. 搜索所有 `innerHTML`、`textContent`、`replaceChildren`、`appendChild`、`insertBefore`。
3. 搜索 `setInterval`、递归 `setTimeout`、`MutationObserver`。
4. 为每个写入点标记 owner；如果同一个 cell 有两个 owner，先合并 owner，不要调刷新间隔。
5. 在 DevTools Network 看是否有固定周期的接口请求；若两个 endpoint 总是成对出现，沿调用链检查是否一个 poller 在触发另一个 owner。
6. 在 observer 上确认 `target`：如果只是 `TD/TH` 内文本被替换，却触发全表 layout，说明 observer 范围/过滤规则过宽。
7. 查看最近是否有“新 overlay”叠在 stable table / health center / column-layout 上。

### 必须有的回归测试

涉及管理列表结构时至少增加：

- 静态 owner contract：同一列不能被两个功能层写结构；
- 禁止新结构 owner 使用周期性 `innerHTML` 重建；
- Node/DOM contract（适用时）：重复执行 refresh 后 editor DOM identity 不变化；
- 旧健康层不得再写已转交给新 owner 的列；
- presentation renderer 必须比较旧值/新值后再写 `textContent`；
- 一个周期性 poller 不得链式调用另一个远程 refresh owner；
- 页面 hidden 时周期性管理健康请求必须暂停；
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
- [ ] presentation 写入在值未变化时不会替换 text node / editor DOM。
- [ ] 一个 poller 不会隐式调用第二个远程 refresh owner。
- [ ] 页面隐藏/切到其它管理页时周期性轮询会暂停。
- [ ] 数据刷新不会反复销毁并重建 input/button/editor DOM。
- [ ] 更新相同数据时 DOM 写操作是幂等的。
- [ ] 新旧列迁移后，旧 owner 已显式 retire，而不是继续后台运行。
- [ ] 有自动化测试锁定 owner contract，后续版本改列时会直接 CI 失败。

如果其中任意一项回答“不确定”，先查历史 Patch 和本文件，再继续开发。
