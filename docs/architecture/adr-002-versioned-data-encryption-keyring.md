# ADR-002：统一数据密钥与加密配置迁移

> **状态：** Proposed（待架构评审，不是已批准决策）  
> **日期：** 2026-11-11  
> **关联：** Issue #784 / Phase 0 / PR #785  
> **审计基线：** main 4ba1113e9b84b902453d785860b923906b80e9f2  
> **本 ADR 不修改生产代码，不执行真实数据迁移。**

## 1. 决策摘要

Chat2API 当前至少存在三条彼此独立的生产密钥路径：

1. **API Key 与旧用户支付配置：** data_secret.key。由 load_or_create_data_secret() 创建；v17_crypto_patch 将 ApiKeyStore 的 Fernet cipher 切换到该 data secret 并迁移可解密的旧 API Key ciphertext；PaymentConfigStore 也从用户控制台 installer 获得同一 data secret。
2. **MediaArchive：** app.main 构造 MediaArchive(data_dir, config.api_key)，并由 CHAT2API_API_KEY 直接派生 settings.enc 的 Fernet key。该变量已在 Settings 中标记为 deprecated migration input，默认值为空。
3. **PaymentChannelsStore：** 生产 installer 读取 .chat2api_data_secret；读取失败时退回 settings.api_key or "chat2api"。而 PaymentChannelsStore 本身在空 secret 时还有 "chat2api-payment-v106" fallback。它没有读取 data_secret.key。

这意味着“已有 data_secret.key”并不代表所有敏感配置已经使用它。尤其是 PaymentChannelsStore 在新安装且未配置旧 CHAT2API_API_KEY 时，会使用公开固定字符串 chat2api 作为密钥派生输入；MediaArchive 则使用空字符串作为派生输入。二者都不具备独立随机密钥保护。

**建议决策：** 建立唯一的 DataKeyring / SecretEncryptionService，统一管理根密钥、密钥 ID、用途隔离、迁移和轮换。所有存储模块只调用该服务，不自行选择文件名、默认口令或派生规则。

## 2. 当前源码事实

### 2.1 根密钥创建

app/api_keys.py::load_or_create_data_secret：

- 路径为 CHAT2API_DATA_DIR/data_secret.key。
- 文件不存在或内容为空时生成 secrets.token_urlsafe(48)。
- 通过固定临时路径 data_secret.tmp 写入后 replace。
- 尝试将最终文件 chmod 0600。
- 未检查现有加密数据是否存在，也未对“旧 key 文件丢失/被清空”与“首次启动”作区分。
- 没有跨进程锁；多个进程同时首次创建时可能竞争同一个临时文件和最终 key。

### 2.2 API Key

app/v17_crypto_patch.py 在 installer 阶段调用 load_or_create_data_secret，并包装 ApiKeyStore.load：

1. 先加载 api_keys.json。
2. 将 ApiKeyStore cipher 替换为 data_secret 派生的 Fernet。
3. 若旧 CHAT2API_API_KEY 非空，尝试用旧 cipher 解密 token_ciphertext，再用新 cipher 重加密。
4. 某条历史密文不能由旧 key 解密时会跳过，不撤销 API Key，也不报告完整迁移失败。

API Key 鉴权使用 token_hash，因此某些 ciphertext 迁移失败不会立即阻止旧 Key 认证，但会导致 secret reveal 不可用。

### 2.3 PaymentConfigStore

app/user_console_v104_patch.py 在 installer 阶段调用 load_or_create_data_secret，并将该值传给 PaymentConfigStore。该 Store 使用自己的 Fernet 派生逻辑保存 user_payment_config.json。

PaymentConfigStore.secret_key() 在 ciphertext 无法解密时返回空字符串，没有将“配置不存在”和“密钥不匹配/数据损坏”区分开。

### 2.4 MediaArchive

app/main.py 在生产 installer 链运行之前创建：

~~~python
media_archive = MediaArchive(config.data_dir, config.api_key)
~~~

app/media_archive_v173.py 派生 key 的逻辑为：

~~~python
SHA256("chat2api:media:settings:v1:" + master_secret)
~~~

- 密文文件：media_archive/settings.enc。
- _config() 在解密失败时抛出 RuntimeError("Cannot decrypt media settings; verify the administrator key")。
- 若 CHAT2API_API_KEY 为空，派生 key 仍然是确定值，而不是随机 data secret。
- v17_crypto_patch 没有迁移 MediaArchive cipher/settings.enc。
- MediaArchive 在基础 app 创建时就捕获了原始 config.api_key；之后 v17 管理认证 patch 对 Settings 的变更不会自动替换已经构造的 MediaArchive cipher。

### 2.5 PaymentChannelsStore

app/payment_channels_v106_patch.py::install_payment_channels_v106_patch 当前读取：

~~~python
secret_path = data_dir / ".chat2api_data_secret"
try:
    data_secret = secret_path.read_text(encoding="utf-8").strip()
except OSError:
    data_secret = str(getattr(settings, "api_key", "") or "chat2api")
channels = PaymentChannelsStore(data_dir, data_secret)
~~~

PaymentChannelsStore.__init__ 再用 SHA256(secret) 派生 Fernet key；若直接传入空字符串，则使用 chat2api-payment-v106。

- 生产 installer 没有读取 data_secret.key。
- 本次审计没有确认当前 installer 链中创建/同步 .chat2api_data_secret 的逻辑。
- _decrypt() 在 Fernet 解密失败时返回空字符串，可能把密钥不匹配表现为“支付渠道尚未配置”。
- 当前 tests/test_payment_channels_v106.py 直接用固定测试 secret 构造 Store，覆盖密文隐藏和支付 rails，不覆盖生产 installer 的 secret source、旧 key 迁移或丢 key 情况。

**运行环境限制：** 本次只审计仓库代码，未读取任何生产 CHAT2API_DATA_DIR。因此不能确认生产数据卷中 .chat2api_data_secret、data_secret.key、settings.enc 是否存在，也不能仅凭代码推断某个现存生产密文使用了哪一个候选 key。

## 3. 问题与风险

| ID | 级别 | 问题 | 后果 |
|---|---|---|---|
| SEC-01 | P0 | MediaArchive 使用废弃环境变量派生 key，默认空字符串仍形成确定性 key | 新安装 S3 provider secrets 可由公开常量推导；旧环境变量变化后配置可能不可解密 |
| SEC-02 | P0 | PaymentChannelsStore 读取另一条 legacy path，缺失时 fallback 到公开字符串 chat2api | 新安装支付商户密钥可能由公开常量推导 |
| SEC-03 | P1 | PaymentChannelsStore / PaymentConfigStore 解密失败时返回空字符串 | 密钥丢失或错误可能静默显示“未配置”，运营人员可能覆盖原设置 |
| SEC-04 | P1 | data_secret.key 丢失/为空时自动生成新 key，没有 encrypted-data preflight | API Key / PaymentConfig ciphertext 可能不可恢复，错误被延迟发现 |
| SEC-05 | P1 | data_secret.key 创建使用固定临时文件且没有跨进程锁 | 多进程首次启动时可能产生竞争和密钥覆盖风险 |
| SEC-06 | P1 | 密钥文件与密文均位于同一个数据卷 | 整个数据卷泄露时，文件级加密无法提供密钥/数据分离保护 |
| SEC-07 | P1 | API Key migration 对不能解密的记录跳过且没有统一迁移报告 | 迁移看似完成，部分旧 Key secret 实际不可恢复 |
| SEC-08 | P1 | 没有统一的密钥 ID、用途标签、轮换状态和恢复演练 | 未来轮换时无法可靠区分每个 ciphertext 的 key source |

## 4. 建议目标设计

### 4.1 唯一密钥服务

引入唯一服务（暂定名 DataKeyring）：

- 应用启动时只初始化一次；所有 Store 注入同一个接口。
- 默认根密钥路径继续兼容现有 data_secret.key，避免无必要地重置现有 API Key / PaymentConfig ciphertext。
- 根密钥必须由 CSPRNG 生成，至少 256 bit 熵。
- 创建必须使用排他文件创建或跨进程锁、随机临时文件、fsync、原子 rename，并在创建后回读验证。
- 生产部署支持外部 KMS/Secret Manager 注入；本地文件模式只作为受控单机部署选项。
- 根密钥文件权限 0600；目录权限按部署策略限制。日志永远不输出 key 或明文 secret。
- 缺少 key 但发现加密数据时，必须 fail closed；不得静默生成替代 key。
- 首次空数据目录可以生成 key；空 key 文件、损坏 key 文件、权限不足或已有密文却缺 key 必须产生可操作的启动错误。

### 4.2 用途隔离与密文格式

根密钥不直接供每个 Store 自行 SHA256 派生。由 DataKeyring 用 HKDF-SHA256 按用途派生子密钥，例如：

- api_key.recoverable.v2
- payment_config.v2
- payment_channels.v2
- media_archive.settings.v2

每个加密值必须有可识别的版本和 key ID。推荐使用带版本的 envelope（具体序列化格式在实现 PR 固化）：

~~~json
{
  "format": "chat2api-encrypted-value",
  "version": 2,
  "key_id": "data-key-2026-01",
  "purpose": "media_archive.settings",
  "ciphertext": "..."
}
~~~

要求：

- 旧格式只用于读取/迁移，不再用于新写入。
- 解密必须校验 purpose，禁止把支付配置 ciphertext 当作媒体配置 ciphertext 使用。
- 轮换期间可保留旧 key 作为 decrypt-only key；新写入只使用 active key。
- Store 不允许在缺 key 时使用硬编码 fallback。
- 加密失败、purpose 不匹配或 ciphertext 损坏必须返回明确错误，不得把空字符串当作成功解密。

### 4.3 Legacy key resolver

迁移阶段只允许一个显式的 LegacyKeyResolver，按数据类型登记候选来源：

| 数据 | 旧 key 候选来源 |
|---|---|
| MediaArchive settings.enc | 迁移操作员显式提供的旧 CHAT2API_API_KEY；若旧部署当时确实为空，必须通过显式参数声明空值候选 |
| ApiKeyStore token_ciphertext | 旧 CHAT2API_API_KEY，遵循现有 v17 migration 逻辑 |
| PaymentConfigStore | 当前 data_secret.key；若 key 文件丢失，需由备份恢复，不得盲目尝试覆盖 |
| PaymentChannelsStore | 若存在则尝试旧 .chat2api_data_secret；其次尝试旧 CHAT2API_API_KEY；再按当前生产 fallback 候选 chat2api。只有 decrypt + schema validation 成功才认定匹配 |
| PaymentChannelsStore 单元构造 fallback | 仅对有证据使用空参数直接构造的历史数据尝试 chat2api-payment-v106；不得作为新写入默认值 |

候选 key 只用于解密；任何迁移结果都写入新的用途隔离 key。候选来源、命中情况和迁移计数可以审计，但不得记录候选 key、明文配置或 ciphertext。

## 5. 迁移流程

迁移必须是显式、可预览、可回滚的操作，不得在普通 API 请求期间偷偷执行。

### 5.1 Preflight / dry-run

1. 进入维护窗口，停止所有 API 实例和 Worker 入口；确保只有一个迁移进程。
2. 对数据目录建立排他迁移锁。
3. 读取并校验根密钥；如果 keyring 已存在但损坏，立即停止。
4. 枚举加密资产：api_keys.json 中的 token_ciphertext、user_payment_config.json、user_payment_channels_v106.json、media_archive/settings.enc。
5. 记录每个资产是否存在 ciphertext、候选 key 是否能解密、schema 是否通过验证、是否有无法识别的记录。
6. 只输出文件路径、记录数、候选 key 标签、成功/失败计数和错误类别；不输出密文或明文。
7. 只要任一已存在的密文无法被识别，就禁止 apply，除非操作者明确选择隔离该记录并接受人工恢复。

### 5.2 备份

- 备份原始密钥文件和全部受影响的密文文件。
- 备份文件权限 0600，备份目录不可由 Web 进程公开访问。
- 生成 manifest：文件相对路径、大小、SHA-256、备份时间、审计基线、迁移工具版本。
- manifest 不包含任何密钥或明文。
- 备份成功并回读校验后才允许 apply。

### 5.3 Apply

对每个 Store：

1. 使用对应 LegacyKeyResolver 找到可解密的候选 key。
2. 解密并校验数据类型、必需字段和版本。
3. 使用 DataKeyring 对应 purpose 的 active key 重新加密。
4. 写入随机临时文件，设置 0600，flush + fsync。
5. 从临时文件回读，用新 key 解密并与原始明文做字节级/结构级比较。
6. 原子替换目标文件；保留原文件备份。
7. 更新迁移 manifest 的状态。中断后只能从明确的 per-file 状态恢复，不能重复破坏已迁移文件。
8. 所有文件完成后执行全量 readback 验证；只有全部成功才标记 migration complete。

### 5.4 特别处理

- **MediaArchive：** 若 settings.enc 不存在，按新格式创建默认配置；若存在但旧 key 无法解密，必须停止，不得重置成 local backend 或空 S3 配置。
- **PaymentChannels：** ciphertext 非空但解密失败时必须显式报错，不能返回空字符串并让后台误以为商户凭据未配置。
- **PaymentConfig：** data_secret.key 不存在但 user_payment_config.json 存在 ciphertext 时，不得自动创建新 key 并继续；要求恢复原 key 或执行受控的凭据重设流程。
- **API Key：** token_hash、Key ID、owner、scopes、quota、usage ledger 均不得变化；只允许替换 recoverable ciphertext。某个旧 ciphertext 无法解密时，Key 仍可按 hash 认证，但必须报告该 Key 无法 reveal，且不得伪造新 token。
- **空旧密钥：** MediaArchive 的旧派生算法允许空字符串作为输入。只有操作者明确确认旧部署确实以空 CHAT2API_API_KEY 创建该文件时才允许尝试；不能把当前 env 缺失自动解释为旧 key 为空。

### 5.5 Rollback

- apply 前备份必须完整。
- 任意文件验证失败，整个迁移保持未完成状态，应用不恢复对受影响密文的写入。
- 回滚恢复原始 ciphertext 和与之匹配的原始 key。
- 不删除旧环境变量、不删除旧 key 文件、不清理备份，直到至少经过一个完整发布周期和人工确认。
- 禁止通过“删除 settings.enc 重新初始化”作为迁移失败的自动恢复方式。

## 6. 测试矩阵（实现 PR 必须覆盖）

### 6.1 Fresh install

- 空数据目录，无旧 env：生成随机根密钥；所有 Store 共享同一 DataKeyring 接口，但用途派生 key 不同。
- 两个独立数据目录生成的 key 不相同。
- 新写入的 MediaArchive、PaymentConfig、PaymentChannels 密文均使用版本化 envelope。
- 无任何硬编码 fallback 可用于新写入。

### 6.2 Legacy migration

- MediaArchive 用非空旧 CHAT2API_API_KEY 写入 settings.enc，迁移后 S3 bucket/region/endpoint/credentials 与 public_base_url 完整保留。
- MediaArchive 用空旧 key 写入 settings.enc；未显式确认空候选时 dry-run 阻止，显式确认后可迁移。
- PaymentChannels 用 .chat2api_data_secret、旧 CHAT2API_API_KEY、生产 fallback chat2api 分别生成的 ciphertext 都能被识别并迁移。
- PaymentConfig 用当前 data_secret.key 保持可解密；丢失 key 时不能静默生成替代 key。
- API Key 迁移后 token_hash 不变，旧 Key 认证仍成功，recoverable secret 能按策略 reveal 或被一次性 Key 方案取代。
- 迁移前后 provider config 的非秘密字段和密钥字段逐项一致。

### 6.3 Failure / recovery

- 错误旧 key、截断 ciphertext、错误 Fernet token、错误 purpose、损坏 JSON 都必须 fail closed。
- 任意失败都不覆盖原文件，不丢失订单/Key/媒体索引。
- 文件替换中断、进程崩溃、重复 apply、重复启动都必须幂等。
- 缺失/空白/权限不足的 data_secret.key 与已存在 ciphertext 并存时必须报错，不得生成新 key。
- 两个进程同时首次启动和同时迁移时只有一个能获得锁；另一个明确等待或退出。
- 测试日志、异常文本、HTTP 错误和审计事件不得包含明文 provider secret 或根密钥。
- chmod 0600 和临时文件权限在 Linux 容器环境验证。
- 备份恢复演练必须证明原密文可由原 key 解密。

### 6.4 Production-like integration

- 使用临时数据卷从旧版本布局启动完整 app.entry:app，而不是只直接实例化 Store。
- 运行迁移前后支付配置读取、支付 Provider credential test、MediaArchive test_backend、图片存储/读回、API Key 认证和 reveal 流程。
- 迁移后重启容器并再次验证。
- 真实生产数据只在用户批准的维护窗口和备份验证后执行。

## 7. 分阶段发布

| 阶段 | 行为 | 禁止事项 |
|---|---|---|
| R0：观察 | 增加 key source diagnostics 与 dry-run，不改变读写 | 不自动迁移、不重置 |
| R1：兼容读取 | DataKeyring 读取新格式和明确登记的旧格式；新写入使用 v2 | 不删除旧 key |
| R2：显式迁移 | 维护窗口 dry-run → backup → apply → readback | 不在普通请求期间迁移 |
| R3：稳定期 | 保留 decrypt-only 旧 key，监测迁移完成率 | 不提前删除备份 |
| R4：移除旧路径 | 经过一个完整发布周期、人工确认和恢复演练后移除旧 fallback | 不静默回退到常量 |

## 8. 未决问题

1. 生产数据目录中是否存在 .chat2api_data_secret、data_secret.key 和 media_archive/settings.enc？本轮没有生产文件访问权限，必须由部署方提供脱敏文件清单/哈希，而不是提供密钥。
2. 生产 CHAT2API_API_KEY 历史上是否为空、固定默认值还是自定义值？迁移必须从部署记录或操作员显式输入确定。
3. 是否允许 MVP 继续以本地文件保存根密钥，还是本次直接接入 KMS/Secret Manager？
4. 迁移维护窗口与备份保留周期由谁批准？

## 9. 决策请求

建议批准以下方向后再开始实现：

- [ ] 单一 DataKeyring 是所有 Store 的唯一加密入口。
- [ ] MediaArchive 与 PaymentChannelsStore 迁移为 P0。
- [ ] PaymentConfigStore 与 ApiKeyStore 纳入同一 keyring，但保留各自 purpose。
- [ ] 缺 key / 解密失败一律 fail closed，不再使用公开 fallback 或静默空值。
- [ ] 迁移采用 dry-run、备份、原子写入、readback、rollback 的显式流程。
- [ ] 首个实现 PR 只增加服务、迁移命令和测试；不自动迁移任何真实生产数据。
