# executor 读取数据库账户真值设计

## 1. 文档目标

本文档定义如何把 `executor` 的执行期账户真值从环境变量正式切换到数据库 `ExchangeAccount`，让执行链不再依赖 `OKX_* / BITGET_* / GATE_*` 这一类进程级共享凭证，而是基于任务所属用户的数据库账户记录动态装配执行所需凭证与代理。

本次目标是在不改变当前任务粒度、不增加账户 CRUD 后台、也不引入自动改派逻辑的前提下，把系统的执行主真值从：

- `worker_config.py` 中按交易所读取环境变量凭证
- `executor` 运行期按 `buy_exchange/sell_exchange` 从内存中截取凭证

推进到：

- `dispatcher` 继续基于数据库账户真值做派发前过滤
- `executor` 在消费节点任务时，基于 `user_id + exchange + env_mode` 从数据库读取当前用户的可执行账户
- `executor` 用数据库账户解密结果与代理真值装配执行凭证

这样系统就从“数据库账户只参与派发过滤”推进到“数据库账户同时成为执行期主真值”。

## 2. 范围

本次只做以下能力：

- 定义 `executor` 如何基于数据库 `ExchangeAccount` 读取执行期凭证与代理
- 定义当前 `spot_futures` 节点任务在 executor 侧的账户选择规则
- 定义数据库账户缺失、解密失败、代理失效等执行前失败语义
- 定义它与现有 `dispatcher` 账户过滤链、任务状态机、控制规则之间的职责关系

本次不做以下能力：

- 不改变当前任务粒度，仍是“用户 + 策略 + 机会”
- 不新增账户 HTTP CRUD 或管理后台
- 不把 `buy_account_id / sell_account_id` 强制写入当前节点任务 payload
- 不实现自动改派到其他 region 或其他 node
- 不实现复杂的多账户优选算法
- 不把环境变量凭证作为执行期静默回退来源

## 3. 背景与现状

当前系统已经具备以下基础：

- [models.py](file:///d:/old/FuRunSystemV4/models.py) 中 `ExchangeAccount` 已建模：
  - `exchange`
  - `env_mode`
  - `api_key_ciphertext`
  - `secret_ciphertext`
  - `passphrase_ciphertext`
  - `proxy_id`
  - `is_enabled`
  - `is_auto_trade_enabled`
  - `market_type_scope`
  - `account_region`
- [dispatch_user_repository.py](file:///d:/old/FuRunSystemV4/app/db/dispatch_user_repository.py) 与 [account_repository.py](file:///d:/old/FuRunSystemV4/app/db/account_repository.py) 已把数据库账户真值接入 `dispatcher` 候选发现与运行时过滤
- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py) 中 `dispatcher` 已能基于账户真值完成：
  - 交易所覆盖过滤
  - 自动交易过滤
  - `market_type_scope` 过滤
  - `account_region` 过滤
- [redis_flow.py](file:///d:/old/FuRunSystemV4/app/runtime/redis_flow.py) 中 executor 仍通过 `credentials_by_exchange` 使用环境变量凭证执行

但当前仍有明显缺口：

1. 数据库账户真值只参与“是否派发”，不参与“如何执行”
2. `executor` 仍使用进程级环境变量共享凭证，无法表达“同一交易所不同用户不同凭证”
3. 账户字段如 `proxy_id / account_region / market_type_scope / is_auto_trade_enabled` 虽已参与 dispatcher 过滤，但执行期并未再次基于同一真值装配凭证
4. 若未来要做真实多用户执行，当前环境变量模型会成为最核心瓶颈

这意味着数据库账户虽然已经成为派发链的重要真值，但尚未真正成为执行链真值。

## 4. 问题定义

如果继续保持现状，会有以下问题：

1. `executor` 无法按用户级账户执行，只能按交易所级共享凭证执行
2. `dispatcher` 与 `executor` 的账户真值来源不一致，形成“派发看 DB、执行看 env”的双真值
3. 账户代理、区域和密文凭证即使在 DB 中存在，也不会在执行期真实生效
4. 任务状态虽然已经进入数据库，但最关键的执行凭证仍不受数据库账户真值控制

因此，本次必须把数据库 `ExchangeAccount` 正式接入 `executor` 的执行主链。

## 5. 设计目标

本次设计满足以下目标：

1. `executor` 执行期凭证主真值改为数据库账户
2. 环境变量凭证不再作为执行期静默回退来源
3. 当前 `dispatcher` 已做的账户过滤语义，在 `executor` 侧至少做最小一致性兜底
4. 执行前失败原因能清晰区分“账户缺失”“解密失败”“代理失效”等类型
5. 现有任务状态机、节点任务流和控制规则保持兼容

## 6. 方案比较

### 6.1 方案 A：executor 完整接管数据库账户真值

做法：

- `dispatcher` 继续只负责“哪些任务可以被派发”
- `executor` 消费任务后，按 `user_id + exchange + env_mode` 从数据库读取账户
- 解密数据库账户密文并读取代理，直接组装交易所适配器所需凭证

优点：

- 真正把数据库账户闭环到执行层
- 后续多用户真实执行能力才能成立
- 不再依赖环境变量共享凭证这一历史过渡模式

缺点：

- 范围最大
- 需要明确账户选择、解密、代理装配和失败语义

### 6.2 方案 B：只让 executor 先选择数据库账户，但仍用环境变量凭证执行

做法：

- executor 先从数据库确定“该用哪类账户”
- 实际 API key/secret 仍来自环境变量

优点：

- 改动较小

缺点：

- 会形成更隐蔽的双真值
- 最关键的执行凭证仍不在 DB
- 后续还需要再做一次真正迁移

### 6.3 方案 C：只做数据库账户只读校验

做法：

- executor 继续使用环境变量执行
- 仅额外读取 DB 校验账户是否存在、是否匹配

优点：

- 风险最小

缺点：

- 收益也最小
- 不能解决“执行主真值仍不在数据库”的核心问题

### 6.4 推荐方案

本次采用方案 A。

原因：

- 当前这条主线已经逐步把数据库字段从“存在”推进到“真实生效”
- 若 executor 仍不读取 DB 真值，系统最关键的执行阶段依然停留在过渡模型
- 与其再做一层过渡，不如直接把执行主真值闭环掉，但首版严格控制范围

## 7. 核心设计决策

### 7.1 首版执行真值边界

本次只覆盖当前 `spot_futures` 执行链。

首版明确如下边界：

- `executor` 的执行期主凭证来源改为数据库账户
- 环境变量凭证不再作为执行期静默回退来源
- 当前节点任务 payload 继续使用现有字段：
  - `user_id`
  - `buy_exchange`
  - `sell_exchange`
  - `symbol`
  - `task_uuid`
  - `strategy_config_id`
- 本次不强制把 `buy_account_id / sell_account_id` 写入 payload

说明：

- 这意味着首版 executor 仍需在消费任务时做一次数据库账户选择
- `dispatcher` 和 `executor` 的账户选择逻辑不要求在本次完全锁死到同一条记录 ID
- 后续若需要把两边彻底绑定，可再引入账户 ID 进入 payload

### 7.2 executor 账户选择规则

`executor` 在消费任务后，应按以下维度查询账户：

- `user_id`
- `exchange`
- `env_mode`

并且只允许选择满足以下条件的账户：

- `is_enabled = true`
- `is_auto_trade_enabled = true`
- `market_type_scope` 与当前执行场景兼容
- `account_region` 与当前 executor 节点 `region` 兼容

首版兼容语义沿用 dispatcher 现有规则：

- `market_type_scope` 仍按 `{spot, swap}` 允许集合判断
- `account_region = default` 视为全局兼容
- `account_region = executor.region` 视为兼容

说明：

- 这样可以保证 executor 不会绕过 dispatcher 的关键账户真值语义
- 本次不要求实现复杂优选算法，只要能够稳定选择“当前可执行账户”即可

### 7.3 多账户场景的首版处理

首版不做复杂优选，但必须定义确定性行为。

建议规则：

- 按数据库主键 `id` 升序选择第一条满足条件的账户记录
- 买边、卖边各自独立选择

说明：

- 这是最小可解释规则
- 后续若要做按标签、优先级、余额、地区等优选，可在此基础上演进

### 7.4 凭证与代理装配

executor 需要基于数据库账户装配出交易所适配器所需执行参数。

至少包括：

- `api_key`
- `secret`
- `passphrase`（若交易所需要）
- `proxy`（若账户绑定了 `proxy_id`）

装配步骤：

1. 读取目标账户记录
2. 解密 `api_key_ciphertext / secret_ciphertext / passphrase_ciphertext`
3. 若 `proxy_id` 不为空，则读取代理记录并组装代理配置
4. 按交易所适配器当前要求生成 credentials/proxy 结构

说明：

- 本次不改变交易所适配器的公共调用接口
- 只改变 executor 在调用前如何准备 credentials/proxy

### 7.5 Worker 启动与依赖注入边界

当前 `WorkerApp.run()` 在启动时会统一从环境变量加载交易所凭证。

本次对 executor 的边界应调整为：

- `scanner` 仍可继续使用环境变量凭证
- `dispatcher` 不需要交易所执行凭证
- `executor` 不再要求启动时预加载整套交易所环境变量凭证

因此：

- `worker_service.py` 需要为 executor 注入数据库 session factory
- 需要为 executor 注入账户读取能力
- 需要为 executor 注入密文解密能力
- 需要为 executor 注入代理读取能力

说明：

- 本次是 executor 专属迁移，不要求 scanner 同步切换到 DB 账户真值
- 启动期的 env 凭证校验逻辑应对 executor 角色做有界收缩，避免 executor 因缺少环境变量而错误启动失败

### 7.6 与 dispatcher 的关系

本次保持以下职责分层：

- `dispatcher` 负责根据数据库账户真值决定“这条机会是否值得为该用户创建任务”
- `executor` 负责根据数据库账户真值决定“这条已派发任务应使用哪条账户记录执行”

两者关系：

- `dispatcher` 是派发前过滤
- `executor` 是执行前装配与兜底

说明：

- executor 侧仍应做最小兜底校验，而不是假设 dispatcher 永远正确
- 若执行时发现账户已失效或被修改，应明确失败，而不是静默放过

### 7.7 执行前失败语义

本次需要把“执行前就无法装配账户真值”的情况与“交易所 API 调用失败”区分开来。

建议至少区分以下失败类型：

- `executor_account_not_found`
- `executor_account_decrypt_failed`
- `executor_account_proxy_invalid`
- `executor_account_region_mismatch`
- `executor_account_market_type_invalid`

这些失败的共同特点：

- 发生在真正调用交易所 API 之前
- 属于执行期账户真值失败

处理原则：

- 明确记录结构化日志
- 若存在 `task_uuid`，则任务状态进入 `FAILED`
- 错误信息中保留足够的排障字段

### 7.8 与任务状态机的关系

本次不改变任务状态机的大框架：

- `DISPATCHED`
- `EXECUTING`
- `SUCCEEDED`
- `FAILED`
- `BLOCKED`

但 executor 执行链需要明确：

- 只有在账户真值成功装配后，才进入真正的执行阶段
- 若账户真值装配失败，应作为执行失败落到 `FAILED`

说明：

- 本次不新增新的任务状态值
- 通过任务错误信息与结构化日志区分失败类型即可

### 7.9 与现有 payload 的关系

本次继续复用现有 payload 字段，不额外要求新增账户 ID。

原因：

- 当前 payload 已包含 executor 反查 DB 账户所需的最小信息
- 先做最小闭环，避免一次改动 dispatcher、payload、executor 三层绑定语义

但后续可演进为：

- `dispatcher` 在任务创建时选定 `buy_account_id / sell_account_id`
- 节点任务 payload 明确透传这两个字段
- `executor` 只按 ID 取账户，不再重复选择

本次不属于这个阶段。

### 7.10 与环境变量凭证的关系

本次明确如下边界：

- 环境变量凭证不再作为 executor 执行期主真值
- executor 在运行时不应因为缺少交易所环境变量凭证而默认失败
- 若数据库账户装配失败，不应偷偷回退到环境变量继续执行

说明：

- 这能避免“测试环境看起来成功，实际仍在使用共享 env 凭证”的假闭环
- scanner 保持原状，不要求同步迁移

## 8. 测试与验证

本次应至少覆盖以下验证：

1. executor 能基于 `user_id + buy_exchange + sell_exchange + env_mode` 从 DB 读取买卖两边账户并执行
2. 一边账户缺失时，任务明确失败，不回退 env 凭证
3. 一边账户密文解密失败时，任务明确失败
4. 一边账户代理配置无效时，任务明确失败
5. `default` 区域账户在当前 executor region 下可执行
6. 区域不兼容账户在 executor 侧再次兜底失败
7. `market_type_scope` 不兼容账户在 executor 侧再次兜底失败
8. 当前 payload 不携带 account ID 时，executor 仍能稳定完成账户选择
9. 现有任务状态流和节点消费主链不回归

远端验证至少覆盖：

1. 正常 canary 账户可成功执行
2. 删掉或破坏一边数据库账户后，任务明确失败
3. 恢复数据库账户后，任务重新成功
4. 验证执行期确实不再依赖环境变量凭证

## 9. 风险与兼容性

本次方案的主要风险在于：

1. executor 与 scanner 的凭证来源将暂时不同，一个来自 DB，一个来自 env
2. 当前 payload 未携带账户 ID，首版 executor 仍需在执行期自行选择账户
3. 解密与代理读取链路一旦接入，会把更多失败显式暴露出来
4. 若远端历史账户数据不完整，切换后会更早失败

对应缓解方式：

- 明确本次只迁移 executor，不要求 scanner 同步切换
- 用确定性规则锁定首版账户选择
- 用本地测试与远端 canary 验证覆盖账户缺失、恢复、解密失败等路径
- 禁止静默回退 env 凭证，确保问题能真实暴露

## 10. 结论

本次设计把 `executor` 的执行期账户真值从环境变量迁移到数据库 `ExchangeAccount`：

- dispatcher 继续负责基于 DB 账户决定是否派发
- executor 开始基于 DB 账户决定如何执行
- 环境变量凭证退出 executor 的执行主链
- 当前任务状态机、payload 粒度和控制规则保持不变

这是把“数据库账户真值”从派发链推进到执行链的关键一步，也是当前多用户真实执行能力成立的必要前提。
