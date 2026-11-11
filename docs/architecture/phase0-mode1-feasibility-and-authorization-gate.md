# Phase 0：模式 1 上游身份与授权可行性报告

> **状态：** Feasibility gate / NO-GO for hosted multi-user private-web proxy  
> **日期：** 2026-11-11  
> **关联：** Issue #784 / Phase 0 / PR #785  
> **审计对象：** Chat2API Mode 1（官方 ChatGPT 网页反向代理）  
> **结论边界：** 本报告是工程与公开条款核对，不是法律意见，也不代表 OpenAI 对 Chat2API 的个案批准。

## 1. 执行结论

### 1.1 当前 Mode 1 设计不能直接进入实现

#784 当前描述的模式 1 是：用户授权自己的 ChatGPT 上游会话，服务端按 user/tenant 隔离凭据，Chat2API 服务端作为统一反向代理转发官方 ChatGPT 网页及其私有 API。

该设计的核心路径涉及用户 ChatGPT session credential、服务端持久化、远程代理请求和官方网页私有协议。

**结论：当前托管式、多用户、服务端持有上游凭据的 Mode 1 必须 NO-GO，不能按 Mirror 的私有网页协议路线直接实现或上线。**

原因不是代理技术无法实现，而是当前公开的 OpenAI 支持路径与该设计存在明显边界冲突：

- OpenAI 的 Sign in with ChatGPT（SIWC）要求凭据只通过受支持的 sign-in flow 获取；不允许要求用户提供 ChatGPT 密码或 session cookie。
- SIWC Authentication Tokens 的持久化存储必须在本地并由用户控制，不能放在远程或托管环境。
- ChatGPT plan usage 的请求必须来自用户本地 runtime，或仅由该用户控制的 remote runtime；其他用户的活动不能触发该账号请求。
- 只能用于用户明确连接的应用，不得变成通用 API 或供其他工具/无关请求使用。
- 用户必须能够免费使用其 ChatGPT plan usage，不能为了使用其 plan 而支付应用费用或升级应用套餐。
- SIWC 对开源、本地/用户自托管应用有专门路径；付费或远程托管应用需要走 OpenAI interest / partner review 路径。商业网站 SIWC 目前也属于 selected commercial partners 的 limited trial。
- OpenAI Terms of Use 禁止分享账户凭据、让他人使用自己的账户，以及反向工程服务底层组件、自动/程序化提取数据或绕过保护措施。

因此，**“每个最终用户有自己的 Cookie”只能解决跨用户数据隔离，不能自动解决上游授权、托管位置、应用范围或商业使用资格问题。**

### 1.2 可以继续研究的合规替代路径

| 方案 | 技术路径 | 当前结论 |
|---|---|---|
| A. 托管式 Mode 1 私有网页反向代理 | Chat2API 服务端保存用户 session cookie/token，转发 chatgpt.com 私有 API 和网页资源 | **NO-GO**，除非 OpenAI 提供针对该架构的书面授权/合同批准；不得先实现再补授权 |
| B. 用户控制的本地/自托管 SIWC runtime | OpenAI 支持的 OAuth + PKCE；用户授权 ChatGPT plan scope；token 留在用户控制的 runtime；调用获准的 Responses API | **可做独立可行性 Spike**，但它不是官方网页反向代理，也不等于完整 ChatGPT Web UI |
| C. 托管商业 SIWC | OpenAI 认可的 website/partner integration；按批准的 scope、部署和商业条件运行 | 需要申请/等待 OpenAI partner approval；获批前不进入产品实现 |
| D. Mode 2 官方 API | 用户/组织 API key，调用正式 API；按 Chat2API 自己的套餐和计量策略 | 继续作为当前托管产品的主路线 |

## 2. 官方授权与条款核对（2026-11-11）

### 2.1 Sign in with ChatGPT Terms

OpenAI 的 SIWC Terms 生效日期为 2026-11-04。关键要求：

1. 只能通过 OpenAI 支持的 sign-in flow 获取和使用 SIWC access/refresh tokens。
2. 不得要求用户提供 ChatGPT password、session cookie 或不必要的认证数据。
3. SIWC Authentication Tokens 的持久化存储必须是 local and under the user's control，不能放在 remote or managed environment。
4. ChatGPT plan usage 禁止 pooling、transferring、reselling、gifting 或 sharing plan usage / Authentication Tokens。
5. 禁止使用一个用户的订阅满足另一个用户的请求。
6. 请求必须来自用户 local runtime 或仅由该用户控制的 remote runtime；请求必须由该认证用户的活动触发，或由其明确授权的 automation/background process 触发。
7. 只能在用户连接的应用中使用其 plan，不得提供 general-purpose API access。
8. 用户必须能免费通过 SIWC 使用其 ChatGPT plan；不能要求用户为 plan usage 支付费用或升级应用套餐。

官方条款：
- https://openai.com/policies/sign-in-with-chatgpt-terms/

### 2.2 SIWC Developer Documentation

OpenAI 当前提供不同集成类型：

- Website sign-in：目前仅 selected commercial partners limited trial。
- Open-source app ChatGPT plan usage：面向 open-source / locally hosted apps，用户使用 OAuth flow 为该应用授权。
- Paid or remotely hosted app：官方文档要求完成 interest form；不能把 open-source local flow 直接复制到托管 SaaS。
- SIWC identity scopes 只用于身份识别；不会授权读取用户 ChatGPT conversations。
- ChatGPT plan usage 需要单独的 Responses API scopes；获批 scope 只允许合规的 Responses API inference，不授予用户 API key 或完整 ChatGPT Web 对话历史。

官方资料：
- https://developers.openai.com/siwc/quickstart
- https://developers.openai.com/siwc/token-sharing-open-source
- https://developers.openai.com/siwc/token-sharing-open-source/sign-in
- https://developers.openai.com/siwc/website
- https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt

### 2.3 Terms of Use

适用于个人 ChatGPT 服务的 Terms of Use（2026-01-01 生效）要求：

- 不得分享账户凭据或让他人使用自己的账户。
- 不得反向工程、反编译或试图发现服务底层源代码/组件。
- 不得自动或程序化提取数据或输出。
- 不得干扰服务、规避速率限制或绕过保护措施。

官方资料：
- https://openai.com/policies/terms-of-use/

**本报告不代替律师对具体业务、合同、地区和部署模式的审查。** 但从公开条款出发，当前托管式私有网页协议代理不能被默认视为已获准。

## 3. 对 defcron/mirror 参考项目的重新评估

Mirror 可作为协议研究参考，但不能作为 Chat2API Mode 1 的合规或多租户生产可行性证明。

从其当前公开 README / PROTOCOL / proxy.ts 可确认：

- 项目自述为 unofficial，依赖 ChatGPT private web protocol，协议可能随时变化。
- README 的 quickstart 要求用户从 ChatGPT session 页面复制 session token，粘贴到 Mirror controls。
- Mirror 将该 credential 加密存入本地 server store，再使用它获取短期 access token。
- README 明确说项目 designed for local use，不是 remote or multi-user deployment；远程部署还需要额外认证、TLS、CSRF 和安全审查。
- PROTOCOL.md 表示接口结构来自真实浏览器流量分析，并记录 /backend-api/* 私有端点、Sentinel、conduit、Turnstile 和 SSE event 格式。
- proxy.ts 会重写上游 Origin/Host/Referer/User-Agent，注入浏览器侧 shim，转发 Cookie/Bearer 和 WebSocket。
- Mirror 的 WARP egress、浏览器挑战处理和协议适配属于该项目自己的实现，不是 OpenAI 的公开、稳定或获批代理接口。

参考：
- https://github.com/defcron/mirror
- https://github.com/defcron/mirror/blob/main/PROTOCOL.md
- https://github.com/defcron/mirror/blob/main/apps/server/src/proxy.ts

**明确禁止作为本专项实现目标的做法：**

- 收集或要求用户粘贴 ChatGPT session cookie。
- 把一个用户的上游凭据存入 Chat2API 共享托管服务，供多个用户/租户发起请求。
- 复制私有网页 API 协议并将其包装成通用 API。
- 通过 header fingerprint spoofing、验证码/Turnstile solver、挑战绕过或流量伪装来规避上游保护措施。
- 通过共享账号、账号池、轮换账号或转移 plan usage 提高可用量。

## 4. Chat2API 当前产品形态的匹配度

当前 #784 同时定义了用户账户、tenant/workspace、计费、API Key、用户控制台和集中管理端。该形态意味着 Chat2API 服务端是一个多用户控制面，而不是天然由每个最终用户单独控制的本地 runtime。

即使每个 user_id 都保存自己的上游 token：

- token 仍然存储在运营方管理的数据卷/密钥库中；
- 请求仍由 Chat2API 服务端代表用户向上游发起；
- 其他用户活动是否可能触发该 token，需要依赖服务端调度和授权；
- 如果用户必须为使用 ChatGPT plan 而支付 Chat2API 套餐费用，会触及 SIWC no-charge 条件；
- Chat2API /v1 作为通用 API 暴露时，不能直接复用 SIWC plan token。

所以现有 ProxySession / upstream credential reference 设计不能简单加密后就认为满足 SIWC。

## 5. 推荐架构决策

### ADR-M1-001：托管私有网页反向代理保持关闭

- Mode 1 的 chatgpt.com 私有网页 reverse proxy 不进入生产实现。
- 不接收 ChatGPT session cookie/password。
- 不将 SIWC tokens 存在 Chat2API 远程 managed environment。
- 不使用私有 web protocol 作为通用 API backend。
- 除非获得 OpenAI 明确书面授权/partner approval，否则此状态保持 NO-GO。

### ADR-M1-002：把 SIWC 作为独立、可选的用户控制 Runtime Adapter 评估

如果项目仍希望让用户使用自己的 ChatGPT plan，可另行设计 SIWC adapter：

- 通过官方 OAuth/OIDC + PKCE + supported client registration。
- 使用 OpenAI 授予的 scope，而不是 session cookie。
- 每个用户/工作区/host 使用独立的 client registration / ext_agent_host_id。
- 凭据只保存在用户控制的 local runtime 或该用户控制的 self-hosted VM；不得复制到 Chat2API 共享 SaaS credential vault。
- 只向该已连接应用提供合规的 Responses API inference，不开放为通用 OpenAI API key。
- 请求只由该用户的交互或明确授权的后台任务触发。
- 不得把 SIWC plan usage 放进 Chat2API 的付费套餐门槛；用户必须能免费使用其 plan usage。
- 对托管商业版本先完成 OpenAI interest/partner approval，再评估服务端架构。

这条路线是一个新的授权型 AI provider adapter，不是当前 Mode 1 官方网页代理。

### ADR-M1-003：Mode 2 保持现有官方 API 主路线

- 当前托管产品继续使用官方 API key / 已授权的 API 项目。
- 计量、账单、quota、model routing 继续沿现有 API 体系执行。
- 如果 SIWC local adapter 未来获准，只能在独立用户控制 runtime 中运行，不得让它绕过当前服务端 API 计量/权限边界。

## 6. Phase 0 可行性结论与工作项

| 工作项 | 结论 | 状态 |
|---|---|---|
| 现有 Worker Cookie 直接共享给 ProxySession | 不允许 | Rejected |
| 用户粘贴 ChatGPT session cookie 到 Chat2API | 不允许作为实现路线 | Rejected |
| 托管多租户服务端持有 SIWC tokens | 当前公开规则不支持 | No-Go pending approval |
| 复制 Mirror 私有协议并对外提供 API | 不作为本项目生产路线 | Rejected |
| OpenAI SIWC local/open-source integration | 有官方支持路径，限其授权范围 | Candidate |
| 用户控制的 self-hosted runtime | 文档承认 self-hosted VM host；仍须满足用户控制、token 保护和用途限制 | Candidate |
| Chat2API paid/remote hosted SIWC | 需要 OpenAI interest / partner approval | Pending |
| Mode 2 official API | 当前可继续 | Supported path |

### Phase 0 退出条件

- [x] 查阅并记录 2026-11-04 生效的 SIWC Terms。
- [x] 查阅当前 SIWC developer quickstart 与 open-source plan usage 适用范围。
- [x] 对照 Mirror 当前 README、PROTOCOL 与 proxy implementation。
- [x] 明确托管式多用户私有网页代理的 NO-GO 门禁。
- [ ] 项目负责人确认是否接受：Mode 1 私有网页代理保持关闭，转而单独评估 SIWC local/self-hosted adapter。
- [ ] 若目标是付费或远程托管 SIWC，完成 OpenAI interest/partner approval 后再进入设计。
- [ ] 法务/合规审核业务模式、收费方式、数据处理与服务条款适用性。

**最终建议：** 不要把 Mode 1 的实现从“服务端 Cookie Proxy”换个名字继续做。先选择合规的授权形态；没有授权的托管私有网页代理不进入 Phase 1。
