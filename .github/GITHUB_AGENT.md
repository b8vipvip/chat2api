# GitHub Agent v4

chat2api 使用 GitHub Agent v4 管理高频 GitHub Actions。Agent 不替代业务代码审查；它负责资源治理、确定性 workflow 修复、故障分类和 AI Repair Brief。

## 核心规则

- `DETERMINISTIC_TEST`：pytest/assertion/compile/lint/contract 明确失败，必须由新 commit 修复，不自动 rerun。
- `INFRA_TRANSIENT`：Runner lost、DNS、connection reset、GitHub 502/503/504 等明显瞬时故障，最多 fresh rerun 一次。
- `GHOST_RUN`：长期 queued/in_progress 且无 job，或普通 cancel 后仍 active；执行 normal cancel -> recheck -> force-cancel，不进入代码 Recovery。
- `GITHUB_PLATFORM_GHOST`：force-cancel 仍被 GitHub 拒绝；保留证据并限流，不反复消耗 Runner。
- `SUPERSEDED`：PR/feature branch 已被更新 SHA 取代；取消旧 run，不恢复。
- `SIDE_EFFECTFUL`：Release/Deploy/Publish；串行执行，不盲目 replay。

## Concurrency

普通 workflow 同时监听 push + pull_request 时使用：

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

这样同一 PR 只保留最新 SHA，但 main 已经开始的正式验证不会被下一次 push 强杀。Release/Deploy/Publish 使用固定串行 group 和 `cancel-in-progress: false`。

## Fast/Full Gate

语法、compile、focused contract 等确定性快速检查应先于 Docker、完整 pytest、Windows/Android/package 等重型任务。chat2api 的 `Contract Migration Guard` 专门捕获 runtime ownership/版本迁移时旧测试仍引用已删除或旧 owner 文件的问题。

## Governor

Governor 是 stale/ghost 兜底，不是高频轮询器。标准 cadence 为每小时一次、错开整点的 `17 * * * *`；不再使用 `*/10`，避免每个仓库每天额外制造 144 次 Governor run。高频提交的热路径去重由各 workflow 自己的 concurrency 完成。

Governor 对普通 cancel 后仍 active 的 run 才使用 force-cancel。queued 且无 job 的 ghost 不触发 Recovery。默认分支已经 in-progress 的普通验证不会因为 duplicate 规则被取消。

## Recovery

`.github/scripts/actions_strategy_autofix.py` 只做机械可判断的修复：缺 workflow_dispatch、permissions、concurrency、PR-aware cancellation、job timeout 等。无法安全确定修法时创建 `[GitHub Agent][AI Repair] ...` Issue。Release/Deploy/Publish 不自动重放。

## Node24 baseline

使用 `actions/checkout@v7`、`actions/setup-python@v7`、`actions/setup-node@v7`、`actions/upload-artifact@v7` 等 Node24-native 官方 Actions。新增或修改 workflow 时不得重新引入已淘汰的 Node20 major。

## chat2api 阈值

- 主 CI：20 分钟。
- pytest 单测试：120 秒。
- Production image smoke：30 分钟。
- Release：20 分钟，其中 post-merge validation 最多等待 15 分钟。
- 普通 stale run：45 分钟。
- Release/Deploy/Publish：180 分钟。
- Governor：10 分钟单次上限，每小时调度一次。
- Recovery：20 分钟。

## AI 接手

接手 AI 必须先确认源 SHA 是否已被更新提交取代，读取完整失败日志，在独立分支修复，运行原失败测试、相关回归和 Actions Policy Check，并在 PR 中写明根因、改动和验证结果。
