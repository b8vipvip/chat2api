# GitHub Actions 策略 v2.0

本策略同时适用于新项目与已经出现 Actions 堆积、重复运行、长时间卡死的老项目。目标不是只提供一份 YAML 模板，而是建立一套持续生效的资源治理规则。

## 1. 强制规则

### 1.1 自动 CI / Test / Smoke 必须取消同一工作流同一分支或 PR 的旧运行

建议统一使用：

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true
```

Release / Deploy / Publish 可以使用 `cancel-in-progress: false`，但必须有独立串行组，并受仓库级 Actions Governor 的 180 分钟硬上限保护。

### 1.2 核心验证 Job 必须设置显式超时

常规建议：

- 单元测试 / Lint：10～20 分钟
- 构建 / Smoke：20～45 分钟
- Release / Deploy：可以更长，但不得无限运行

Python pytest 还应使用测试级超时，避免单个测试把整个 Job 挂死。

### 1.3 权限最小化

普通 CI 默认：

```yaml
permissions:
  contents: read
```

只有需要取消 Actions 的治理工作流才授予 `actions: write`；只有发布 Release 的工作流才授予 `contents: write`。

### 1.4 Debug / 临时诊断工作流禁止长期自动触发

临时诊断完成后必须改为 `workflow_dispatch`，并设置较短超时。不得让 Debug Workflow 跟随每次 push / PR 无限增长。

## 2. Actions Governor：老项目与异常运行的自动治理

仓库必须存在 `.github/workflows/actions-governor.yml`。

Governor 每 10 分钟扫描当前仍处于 `in_progress` 或 `queued` 的运行，并执行：

1. **重复运行清理**：普通 CI/Test/Smoke 对同一 workflow + branch + event 只保留最新一条，其余自动取消。
2. **异常长运行清理**：普通工作流运行达到 45 分钟仍未结束，自动取消。
3. **发布类保护**：名称包含 Release / Deploy / Publish / Store Package 的工作流允许更长时间，但达到 180 分钟仍未结束时自动取消。
4. **历史异常兼容**：Governor 查询的是仓库当前真实运行状态，因此即使任务是在本策略落地之前启动，也能被识别并清除，不要求旧任务自身包含新配置。
5. **治理工作流自身防堆积**：Governor 本身使用 concurrency 且 Job 超时为 10 分钟。

这条规则用于处理类似“旧 CI 已运行一小时以上、多个相同 PR CI 同时占用 Runner”的事故。

## 3. 老项目迁移顺序

1. 先加入 Actions Governor。
2. 给主 CI / Test / Smoke 加 `concurrency + cancel-in-progress`。
3. 给核心 Job 加 `timeout-minutes`。
4. 给测试框架增加单测试超时。
5. 将临时 Debug Workflow 改为手动触发。
6. 合并后由 Governor 自动清理合并前遗留的异常运行。
7. 再触发一轮全新的 CI 验证策略是否生效。

## 4. 验收标准

策略只有同时满足以下条件才算真正落地：

- 同一 PR 连续 push 两次，旧 CI 自动进入 `cancelled`。
- 普通 CI 不会无限运行，仓库级硬上限为 45 分钟。
- Release / Deploy 等串行工作流最长不超过 180 分钟。
- 单个 pytest 卡死不会拖死整个 CI。
- 历史遗留的长时间 `in_progress` / `queued` 任务能被 Governor 自动取消。
- 临时诊断不再随每次 push / PR 自动运行。
- 最新一代正式 CI 最终给出明确 `success` 或 `failure`，而不是长期 `in_progress`。

## 5. 本仓库策略

chat2api 的主 CI 使用 20 分钟 Job 超时、pytest 120 秒单测试超时，并对同一 PR / ref 自动取消旧 CI。Production image smoke 与 Release 仍按各自业务语义运行，但统一受 Actions Governor 的仓库级资源治理约束。
