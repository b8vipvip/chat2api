# GitHub Agent v3

> 原“GitHub Actions 策略 v3”已正式更名为 **GitHub Agent v3**。GitHub Agent **不内置、不调用 coding-agent provider**。它负责发现问题、释放资源、做确定性修复，并把不能安全自动修复的问题整理成 **AI Repair Brief**，由用户指定的 AI 再读取仓库、Issue、Run 日志并完成代码修复。

## 1. 职责边界

GitHub Agent 负责四件事：

1. **预防**：Workflow Guard / Policy Check 约束权限、并发、超时、恢复入口。
2. **治理**：Governor 清理重复、卡死、历史异常的 queued / in_progress 任务。
3. **确定性修复**：Recovery 自动修复可以机械判断的 Workflow 缺陷，以及仓库中已经编码的幂等修复规则。
4. **问题交接**：无法安全确定修法时，生成标准化 AI Repair Brief，而不是让 Agent 猜业务代码。

## 2. GitHub 原生能力与 Agent

GitHub Actions 原生提供日志、Cancel、Re-run、workflow_dispatch、分支/提交/PR/Issue 等执行能力（受 token 权限约束），但这些能力本身不会理解任意业务代码，也不会自动推断正确修法。

GitHub Agent 使用 GitHub 原生 API 做执行底座；业务代码需要推理时，Agent 只整理证据并交给用户选择的 AI。

## 3. 自动修复层级

### L0：运行控制

- 同一普通 workflow + branch + event 去重；
- stale run 先 Cancel 释放 Runner；
- 普通任务、发布任务、Agent 自身都有硬超时。

### L1：Workflow 确定性修复

`.github/scripts/actions_strategy_autofix.py` 可自动修复：

- 缺少 `workflow_dispatch`；
- 缺少最小 `permissions`；
- 缺少 `concurrency`；
- 普通任务缺少 `cancel-in-progress: true`；
- 发布类任务缺少串行保护；
- `runs-on` Job 缺少 `timeout-minutes`。

修复后自动建立 recovery branch / PR，并对无副作用 Workflow 重新 dispatch。

### L2：项目已知修复规则

可使用 `.github/actions-recovery.sh` 编码已确认、确定、幂等的项目修复规则。它可以读取 `ACTIONS_RECOVERY_LOG`，修改源代码、测试或配置，然后提交 Recovery PR。

### L3：AI Repair Brief（不接 provider）

如果问题需要理解业务逻辑、跨文件推理、判断测试与实现谁有问题，GitHub Agent **不自行调用任何 coding agent**。Recovery 会创建/更新：

`[GitHub Agent][AI Repair] <workflow> run <run_id>`

Issue 中整理：

- 源 Run、Workflow、分支、Commit SHA、事件、尝试次数；
- Governor / Recovery 判定原因；
- 失败 Job / Step 摘要；
- 高信号错误行摘要；
- Agent 已做过的自动操作；
- 是否属于 Release / Deploy 等有副作用任务；
- 自动修复 PR（如有）；
- 给 AI 的修复任务说明、约束和验收标准。

用户随后只需要让 AI **读取该 Issue + 源 Run 日志 + 对应 Commit/分支**，继续排查和修改代码。

## 4. 标准恢复链路

```text
异常 / 卡死 / 历史 run
        ↓
Governor 判定 duplicate / stale
        ↓
Cancel 释放 Runner
        ↓
Recovery 收集 metadata + jobs + logs
        ↓
L1 Workflow 确定性修复？
   ├─ 是 → Recovery PR → 重新验证
   └─ 否
        ↓
L2 项目规则可确定修复？
   ├─ 是 → 修改代码/测试 → Recovery PR → CI
   └─ 否
        ↓
生成 AI Repair Brief Issue
        ↓
用户让 AI 读取 Issue / Run / 代码并修复
        ↓
AI 建修复分支 + PR + CI
```

对于明显的 Runner / 网络 / 临时环境故障，Agent 可以保留一次受限 fresh rerun；无论是否重试，只要没有确定性修复，都会留下 AI Repair Brief，避免问题丢失。

## 5. AI Repair Brief 的 AI 操作规范

接手的 AI 应：

1. 先确认源 Run 是否已经被更新 Commit 取代，避免修复过时代码。
2. 阅读完整 Run 日志和失败 Job/Step，不只依赖 Issue 摘要。
3. 在独立修复分支修改代码，默认禁止直接写 `main`。
4. 运行原失败测试、相关回归测试和 GitHub Agent Policy Check。
5. PR 中说明根因、改动、验证结果和剩余风险。
6. Release / Deploy / Publish / Store Package 不允许自动盲目重放。

## 6. chat2api 默认阈值

- 主 CI：Job 上限 20 分钟。
- pytest 单测试超时：120 秒。
- 普通仓库级 Governor 兜底：45 分钟。
- Release / Deploy / Publish / Store Package：180 分钟。
- Governor：10 分钟。
- Recovery：20 分钟。
- 明确瞬时故障 fresh rerun：最多 1 次。

## 7. 兼容说明

`actions-governor.yml`、`actions-recovery.yml`、`actions-policy-check.yml`、`actions_strategy_autofix.py` 等旧 `actions-*` 命名继续作为 GitHub Agent 内部兼容实现名称，不代表产品名仍叫“GitHub Actions 策略”。
