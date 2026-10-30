# GPTAuto 统一智能更新规则（本仓库强制执行）

对任何 AI / ChatGPT / GPTAuto 修复与版本更新，首先读取 [GPTAuto 统一策略](.github/GPTAUTO_SMART_UPDATE.md)，然后遵守其 Git SHA 绑定的更新分类、Fast CI、按需编译、人工点击部署、宿主机唯一更新器及回滚规则。不能凭 PR 标题或版本号自动触发完整 Docker Build；无法验证安全热更新前置条件时，必须保持已有正式构建/发布保护。下文产品专属安全约束继续生效。

# AI 开发入口

本仓库已启用 **GitHub Agent v3**。本文件是进入仓库进行开发、排障和 CI 修复时的 AI 入口说明，适用于整个仓库。

## GitHub Actions / CI / 测试异常的处理顺序

当任务涉及 GitHub Actions、CI、测试失败、卡死、排队异常或历史异常 Run 时，AI 必须先：

1. 阅读 [`.github/GPTAUTO_SMART_UPDATE.md`](.github/GITHUB_AGENT.md)。
2. 搜索未关闭的 `[GitHub Agent][AI Repair]` Issue，优先找到与当前 Workflow / Run / 分支对应的问题单。
3. 阅读问题单指向的完整 Source Run、失败 Job / Step、日志和对应 Commit；Issue 中的日志摘要只用于定位，不能替代完整日志。
4. 检查源 Commit 是否已经被更新提交或已有修复取代，避免修复过时代码。
5. 分析根因后再修改代码、测试、配置或 Workflow；不得只为“让 CI 变绿”而削弱断言、跳过测试或掩盖异常。
6. 在独立修复分支提交修改并建立/更新 PR；默认禁止直接写 `main`。
7. 运行原失败测试、相关回归测试以及 GitHub Agent Policy Check，并在 PR 中记录根因、改动、验证结果和剩余风险。
8. Release / Deploy / Publish / Store Package 等有副作用 Workflow 禁止自动盲目重放。

## 与 GitHub Agent 的分工

GitHub Agent 会自动执行巡检、卡死任务治理、取证和确定性修复。若问题需要理解业务逻辑而无法安全自动修复，Agent 会生成标准化 **AI Repair Brief** Issue。

AI 的职责是读取这些证据并完成需要代码推理的修复。不要接入或假设存在任何 coding-agent provider；以仓库中的 Issue、Actions Run、代码和测试为事实来源。

## 普通开发任务

如果任务与 Actions/CI 异常无关，按仓库现有 README、文档、测试和代码约定工作。修改前先理解相关模块，修改后运行与改动范围匹配的测试，避免无关重构。
