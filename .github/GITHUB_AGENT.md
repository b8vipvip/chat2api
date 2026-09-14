# GitHub Agent v3

> 原“GitHub Actions 策略 v3”已正式更名为 **GitHub Agent v3**。现有部分 Workflow / 脚本文件名仍保留 `actions-*` / `actions_strategy_*`，用于兼容已有 Actions 历史、检查上下文和自动化引用；它们现在都属于 GitHub Agent 的内部实现。

chat2api 的 GitHub Agent 是一个仓库内自治治理层：约束 GitHub Actions、发现重复/卡死任务、收集失败证据、执行安全修复、重新提交普通任务，并在无法安全自动完成时升级为 Recovery Incident。

## 1. 核心组成

### Workflow Guard

普通 CI / Test / Smoke 必须使用最小权限、同 PR/ref 自动取消旧运行，并为每个实际运行 Job 设置明确 `timeout-minutes`。Release / Deploy / Publish / Store Package 必须串行并禁止自动取消进行中的正式发布。

### Policy Check

当 Workflow 被新增或修改时，检查 `permissions`、`concurrency`、`cancel-in-progress`、`timeout-minutes`、`workflow_dispatch`，以及临时 Debug / diagnostic Workflow 是否被错误地长期自动触发。

### Governor

每 10 分钟扫描 `in_progress` / `queued` 任务：

- 普通同 workflow + branch + event 只保留最新运行；
- 普通任务达到仓库级超时阈值后释放 Runner；
- 发布类任务使用更长保护阈值；
- 真正 stale / historical 的任务取消后进入 Recovery，而不是只做 Cancel。

### Recovery

Recovery 会读取异常 run 的 metadata 和日志，从 main 加载最新版 GitHub Agent 修复逻辑，然后按安全级别决定：自动修复、重新 dispatch、fresh rerun，或创建 Recovery Incident。

## 2. GitHub 原生能力与 GitHub Agent 的关系

GitHub Actions 原生提供运行控制能力，例如读取日志、Cancel、Re-run、重新运行失败 Job、`workflow_dispatch` 等。它本身不会理解任意业务代码，也不会自动推断“应该修改哪一行代码”。

GitHub Agent 使用这些原生 API 作为执行底座，再叠加仓库自己的判断、修复和验证逻辑。

GitHub 另外提供 Copilot cloud agent / coding agent 等 AI 能力，可以分析失败并修改代码；这属于独立的 coding-agent 能力，不等同于 GitHub Actions 自身自动修复。

## 3. 当前自动修复能力

### A. 确定性 Workflow 修复：已启用

当前 `.github/scripts/actions_strategy_autofix.py` 可以机械、安全地修复：

- 缺少 `workflow_dispatch`；
- 缺少最小 `permissions`；
- 缺少 `concurrency`；
- 普通任务缺少 `cancel-in-progress: true`；
- 发布类任务需要串行保护；
- `runs-on` Job 缺少 `timeout-minutes`。

这些修复不依赖 AI，可以直接创建 recovery branch / PR 并重新提交普通 Workflow。

### B. 项目已知故障规则：接口已预留

Recovery 支持项目级 `.github/actions-recovery.sh`。当某种测试/代码故障已经有确定、幂等的修法时，可以把修复规则写进该脚本，让 GitHub Agent 根据 `ACTIONS_RECOVERY_LOG` 自动改代码、测试或配置。

### C. 任意业务代码智能修复：需要 coding-agent provider

对于“测试失败但修法需要理解业务逻辑”的情况，仅靠 shell/Python 规则无法可靠推断正确修改。要让 GitHub Agent 自动改任意业务代码，需要接入 coding-agent provider，例如 GitHub Copilot cloud agent，或 GitHub Agentic Workflows 支持的 coding agent。

建议安全链路：

```text
失败/卡死任务
  ↓
GitHub Agent 取证
  ↓
确定性修复能解决？ ── 是 → 修改 → Recovery PR → CI
  ↓ 否
需要理解业务代码？ ── 是 → 委派 coding agent
  ↓
只允许修改 recovery/PR 分支
  ↓
执行测试/Policy Check
  ↓
通过 → PR 等待合并
失败 → 限次迭代 / Recovery Incident
```

默认禁止 AI 直接写 `main`，禁止自动盲目重放有外部副作用的 Release / Deploy / Publish。

## 4. 历史卡死任务恢复

```text
历史/当前卡死任务
      ↓
Governor 判定 stale
      ↓
Cancel 释放 Runner
      ↓
Recovery 自动取证
      ↓
确定性 Workflow / 项目规则修复
      ↓
Recovery branch + PR
      ↓
普通任务重新 dispatch
      ↓
无法安全修复 → fresh rerun（限次）/ Recovery Incident
```

GitHub Agent 的目标不是“删除失败记录”，而是：**释放资源、定位原因、修复可安全修复的问题、重新提交验证，并保留审计链路。**

## 5. chat2api 默认安全边界

- 主 CI：Job 上限 20 分钟。
- pytest 单测试超时：120 秒。
- 普通仓库级 Governor 兜底：45 分钟。
- Release / Deploy / Publish / Store Package：180 分钟。
- Governor：10 分钟。
- Recovery：20 分钟。
- 无修改 fresh-run 自动重试：最多 1 次。
- 任意代码智能修复：未接入 coding-agent provider 前，不声称自动完成。

## 6. 兼容说明

以下旧名称暂时作为内部兼容名称继续保留，不代表产品名仍叫“GitHub Actions 策略”：

- `actions-governor.yml`
- `actions-recovery.yml`
- `actions-policy-check.yml`
- `actions_strategy_autofix.py`
- `validate_actions_strategy.py`

从现在开始，对外和文档统一称为 **GitHub Agent**。
