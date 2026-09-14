# GitHub Actions 策略 v3

本仓库所有 GitHub Actions 都必须遵循本策略。v3 不再只做“取消重复任务/限制超时”，而是形成 **预防 + 强制检查 + 运行时治理 + 历史异常恢复 + 自动修复/重新提交** 的闭环。

## 1. Workflow Guard

普通 CI / Test / Smoke 必须包含：

```yaml
permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true
```

并且每个实际运行的 Job 必须配置 `timeout-minutes`。普通验证建议 10～45 分钟。

Release / Deploy / Publish / Store Package 属于有副作用的串行任务，使用独立 concurrency group 与 `cancel-in-progress: false`，但仍受仓库级 180 分钟硬上限保护。

所有可恢复 Workflow 应保留 `workflow_dispatch`，这样 Strategy v3 能在自动修复后显式重新提交修复后的 ref，而不依赖 GITHUB_TOKEN push 是否会触发新工作流。

## 2. Actions Policy Check

`.github/workflows/actions-policy-check.yml` 自动检查新增或修改的 Workflow，至少要求：

- 明确 `permissions`；
- 明确 `concurrency`；
- 普通 Workflow 使用 `cancel-in-progress: true`；
- 发布类使用 `cancel-in-progress: false`；
- 每个 `runs-on` Job 存在 `timeout-minutes`；
- 存在 `workflow_dispatch` 恢复入口；
- Debug / diagnostic / one-shot / tmp 类 Workflow 不允许长期自动随 push/PR 运行。

老 Workflow 可以分阶段迁移，但只要再次被修改，就必须通过 v3 Policy Check。

## 3. Actions Governor

`.github/workflows/actions-governor.yml` 每 10 分钟扫描 `in_progress` 与 `queued` 任务，并执行：

1. 同一普通 `workflow + branch + event` 只保留最新一条；旧重复任务自动取消。
2. 普通任务超过 45 分钟自动取消。
3. Release / Deploy / Publish / Store Package 超过 180 分钟自动取消。
4. Strategy 内部 Workflow 最长 20 分钟，防止治理系统自己卡死。
5. 对真正的 stale / historical 异常任务，取消后必须自动启动 **Actions Recovery**，不能只停在 Cancel。

对于“已有更新版本正在运行”的 superseded duplicate，不会复活旧 Commit，因为最新运行本身已经是重新提交。

## 4. Actions Recovery：自动修复并重新提交

`.github/workflows/actions-recovery.yml` 接管 Governor 清理掉的异常长运行任务：

1. 自动收集原始 run metadata 与日志。
2. 从当前 main 加载最新版 v3 修复逻辑，因此即使异常任务来自策略上线前的旧 Commit，也能使用最新恢复规则。
3. 对 Workflow 层的安全问题自动修复：缺少 `workflow_dispatch`、`permissions`、`concurrency`、Job timeout 等。
4. 如果仓库提供 `.github/actions-recovery.sh`，还会执行项目级、确定性、幂等的修复规则；该 hook 可以根据 `ACTIONS_RECOVERY_LOG` 自动修复已知代码/测试故障模式。
5. 有修复内容时，自动建立 `actions-recovery/run-<run_id>` 分支并提交 Recovery PR。
6. 对无发布副作用的 Workflow，自动对修复后的 ref 进行 `workflow_dispatch`，即“修复后重新提交”。
7. 如果没有可安全修改的文件，最多对原 run 做一次 fresh-run rerun，用于恢复 Runner/网络/瞬时环境问题。
8. 自动恢复预算耗尽后，自动创建 `[Actions Recovery]` Issue，附带源 run、分支、原因和自动修复 PR。

### 重要安全边界

Strategy v3 可以自动修复 **确定性的策略/Workflow 问题**，也支持通过项目 hook 自动修复已经建立规则的业务/测试故障；但不会凭空猜测任意业务代码应该如何修改。

Release / Deploy / Publish / Store Package 不会被盲目自动重放，避免重复发布、重复部署或重复写入外部系统。v3 会提交安全修复并建立恢复事件，发布重试仍需要明确批准。

## 5. 历史卡死任务处理标准

历史异常任务不允许只执行“Cancel/Delete”。标准链路为：

```text
stale historical run
        ↓
Governor Cancel
        ↓
Actions Recovery
        ↓
日志取证
        ↓
安全自动修复
        ↓
Recovery branch / PR
        ↓
修复后的 Workflow 重新提交
        ↓
若不可修复 → bounded rerun / Recovery Issue
```

## 6. 本仓库默认阈值

- chat2api 主 CI：Job 上限 20 分钟；pytest 单测试超时 120 秒。
- 普通仓库级兜底：45 分钟。
- Release / Deploy / Publish / Store Package：180 分钟。
- Actions Governor：10 分钟。
- Actions Recovery：20 分钟。
- 同一个异常 run 的无修改 fresh-run 自动重试预算：最多 1 次。

## 7. 验收标准

只有同时满足以下条件，才算 Strategy v3 已生效：

- 同一 PR 连续提交时旧 CI 自动 `cancelled`；
- 普通 Workflow 不会无限运行；
- 历史异常长任务会触发 Recovery，而不是只被取消；
- 可安全修复的 Workflow 缺陷会生成修复提交/PR；
- 修复后的普通 Workflow 会被重新 dispatch；
- 自动修复无法继续时会生成明确 Recovery Issue；
- 新增/修改 Workflow 必须通过 Actions Policy Check。
