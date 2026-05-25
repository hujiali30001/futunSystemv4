# 数据库真值与任务模型首版闭环设计

## 1. 文档目标

本文档定义当前系统从“Redis 运行链已跑通”进入“数据库真值首版闭环”的第一步设计。

本次目标不是一次性完成完整生产账本，而是在不打断现有 `scanner -> dispatcher -> executor` 主链的前提下，同时把以下两类能力正式接入数据库：

- 多用户账户与代理配置真值
- 套利任务真值与状态机

首版闭环要做到：

- `users / proxies / exchange_accounts / strategy_configs / arbitrage_tasks` 进入正式数据库边界
- `dispatcher` 为用户创建数据库任务
- 节点任务流携带数据库任务标识
- `executor` 按数据库任务状态推进执行结果

这样可以让系统从“只在 Redis 流里推进运行”升级到“运行过程有数据库主线真值可追踪”。

## 2. 范围

本次只做以下能力：

- 定义首版数据库真值范围
- 为用户、代理、交易账户、策略配置、套利任务定义首版模型边界
- 定义数据库任务状态机
- 定义 `dispatcher` 与 `executor` 的落库职责
- 定义 Redis 与数据库的职责分工

本次不做以下能力：

- 不实现完整订单事实账本
- 不实现成交明细、持仓快照、资产流水的首版落库
- 不实现管理后台页面
- 不实现数据库驱动的公告、控制面替换
- 不强制把现有 `.env.worker` 全量切换成数据库配置源
- 不实现复杂的重试编排与补单作业系统

## 3. 背景与现状

当前系统已经具备：

- 公共机会流、节点任务流、用户路由和控制面都已跑通
- [models.py](file:///d:/old/FuRunSystemV4/models.py) 中已经存在基础 ORM 雏形：
  - `User`
  - `Proxy`
  - `ExchangeAccount`
  - `ArbitrageTask`
  - `RiskLimitRule`
  - `Announcement`
- [tasks.py](file:///d:/old/FuRunSystemV4/app/trading/tasks.py) 中已有最小 `ExecutionTask`
- [executor.py](file:///d:/old/FuRunSystemV4/app/trading/executor.py) 中已有最小异步执行器

但当前仍然存在明显缺口：

1. 运行主链还没有真正围绕数据库任务真值推进
2. `users / exchange_accounts / proxies` 只有模型，没有正式仓储与读取链路
3. 节点任务流里没有稳定的数据库任务主键
4. `executor` 的成功、失败、阻断结果没有回写数据库任务状态

这意味着当前系统更像“Redis 驱动的运行样机”，还不是“数据库真值驱动的多用户运行系统”。

## 4. 问题定义

如果继续停留在当前状态，会有以下问题：

1. 任务生命周期只能从流和日志侧间接观察，不能从数据库直接追踪
2. 用户账户、代理、交易账户没有正式数据库读取边界，难以长期扩展
3. 后续做任务查询、失败重放、对账和后台管理时，缺少稳定主键和事实主线
4. Redis 既承担运行缓存，又在事实层面被迫承担“唯一线索”，不利于长期治理

因此，本次必须先把数据库引入到“配置真值 + 任务真值”这两个最关键的面上。

## 5. 设计目标

本次设计满足以下目标：

1. 数据库成为用户账户与套利任务的正式真值层
2. Redis 继续作为运行缓存与事件流，而不是任务事实唯一来源
3. `dispatcher` 创建数据库任务并生成节点执行消息
4. `executor` 围绕数据库任务状态推进执行结果
5. 范围收敛到一个可实施的首版，不把订单、成交、持仓全部拖进来

## 6. 方案比较

### 6.1 方案 A：用户账户真值 + 任务真值最小闭环

做法：

- 数据库首版先覆盖：
  - `users`
  - `proxies`
  - `exchange_accounts`
  - `strategy_configs`
  - `arbitrage_tasks`
- `dispatcher` 创建任务
- `executor` 回写任务状态
- Redis 继续保留机会流、节点任务流、路由和控制缓存

优点：

- 同时覆盖“用户账户”和“任务模型”
- 与当前已跑通主链最贴近
- 范围可控，最适合作为下一阶段首版

缺点：

- 订单、成交、持仓、资金流水仍留到后续阶段

### 6.2 方案 B：完整交易事实账本首版

做法：

- 在方案 A 基础上，再一起引入：
  - `orders`
  - `fill_records`
  - `position_snapshots`
  - `risk_events`
  - `asset_transfers`

优点：

- 更接近长期完整生产形态

缺点：

- 范围过大
- 会显著放慢当前后端主线推进

### 6.3 方案 C：纯数据层先行

做法：

- 先只做 ORM、仓储、迁移和数据库配置
- 运行主链暂不接入任务落库

优点：

- 数据层可以先整理干净

缺点：

- 无法快速形成业务闭环
- 用户很难直观看到数据库已进入主链

## 7. 推荐方案

推荐采用 `方案 A：用户账户真值 + 任务真值最小闭环`。

原因：

- 用户已明确希望“用户账户”和“任务模型”两者都做
- 当前系统最值钱的下一步不是铺开全部表，而是先让主链围绕数据库任务运转起来
- 该方案能把数据库引入主链，同时避免范围失控

## 8. 数据库真值边界

### 8.1 本次数据库真值对象

本次数据库正式真值定义为：

- `users`
- `proxies`
- `exchange_accounts`
- `strategy_configs`
- `arbitrage_tasks`

其中：

- `users / proxies / exchange_accounts / strategy_configs` 属于配置真值
- `arbitrage_tasks` 属于任务真值

### 8.2 Redis 保留职责

Redis 在本次之后继续负责：

- 公共机会流
- 节点任务流
- 用户路由
- 控制面缓存
- 运行时健康状态与短期缓存

原则：

- Redis 负责“快速流转”
- 数据库负责“正式事实”

## 9. 核心模型设计

### 9.1 users

沿用当前 [User](file:///d:/old/FuRunSystemV4/models.py#L30-L39) 模型方向，至少保留：

- `id`
- `username`
- `status`
- `risk_level`
- `home_region`
- `is_trading_enabled`
- `created_at`
- `updated_at`

说明：

- `status` 用于表达账号生命周期
- `is_trading_enabled` 是用户级交易总开关

### 9.2 proxies

沿用当前 [Proxy](file:///d:/old/FuRunSystemV4/models.py#L41-L52) 模型方向，至少保留：

- `id`
- `proxy_type`
- `host`
- `port`
- `username`
- `password_ciphertext`
- `region`
- `provider`
- `health_status`
- `created_at`
- `updated_at`

说明：

- 代理本身与用户、账户分离
- `exchange_accounts.proxy_id` 决定某个账户实际使用哪条代理

### 9.3 exchange_accounts

沿用当前 [ExchangeAccount](file:///d:/old/FuRunSystemV4/models.py#L55-L84) 模型方向，至少保留：

- `id`
- `user_id`
- `exchange`
- `account_label`
- `market_type_scope`
- `env_mode`
- `api_key_ciphertext`
- `secret_ciphertext`
- `passphrase_ciphertext`
- `proxy_id`
- `is_enabled`
- `is_auto_trade_enabled`
- `account_region`
- `created_at`
- `updated_at`

说明：

- 一个用户可有多个交易账户
- 一个交易账户可绑定一条代理
- 首版仍显式区分 `testnet / mainnet`

### 9.4 strategy_configs

本次新增正式策略配置表：

- `strategy_configs`

建议字段：

- `id`
- `user_id`
- `strategy_type`
- `name`
- `symbol_scope_json`
- `exchange_scope_json`
- `target_quote_amount`
- `open_spread_bps_threshold`
- `close_spread_bps_threshold`
- `max_single_task_notional`
- `is_enabled`
- `created_at`
- `updated_at`

说明：

- 这张表是用户策略真值
- 当前首版只要求能表达“目标金额 + 阈值 + 启停”
- 不要求一次性覆盖全部高级策略参数

### 9.5 arbitrage_tasks

沿用并扩展当前 [ArbitrageTask](file:///d:/old/FuRunSystemV4/models.py#L86-L108) 模型方向。

首版至少保留：

- `id`
- `task_uuid`
- `user_id`
- `strategy_config_id`
- `opportunity_id`
- `env_mode`
- `task_type`
- `symbol`
- `spot_exchange`
- `derivative_exchange`
- `target_notional`
- `expected_spread_bps`
- `expected_funding_bps`
- `status`
- `status_reason`
- `idempotency_key`
- `home_region`
- `created_at`
- `updated_at`

建议新增字段：

- `dispatched_at`
- `started_at`
- `finished_at`
- `worker_node_id`

说明：

- `task_uuid` 是运行链主键
- 节点任务流必须携带 `task_uuid`
- `status_reason` 用于记录失败、阻断或缩量原因

## 10. 任务状态机

### 10.1 首版主状态

首版建议至少支持以下状态：

- `CREATED`
- `DISPATCHED`
- `EXECUTING`
- `SUCCEEDED`
- `FAILED`
- `BLOCKED`

### 10.2 状态语义

- `CREATED`
  - `dispatcher` 已在数据库创建任务
  - 但尚未成功写入节点任务流

- `DISPATCHED`
  - 节点任务流写入成功
  - 等待对应执行节点消费

- `EXECUTING`
  - `executor` 已开始处理该任务

- `SUCCEEDED`
  - 本次执行完成，首版只要求表达“执行器成功返回”

- `FAILED`
  - 本次执行出现异常或执行结果失败

- `BLOCKED`
  - 被控制规则或前置条件阻断，没有进入实际执行

### 10.3 首版不做的状态

本次不做过细状态，例如：

- `PARTIALLY_FILLED`
- `REPAIRING`
- `RECONCILING`
- `CANCELLED`
- `EXPIRED`

这些状态保留到后续事实账本阶段扩展。

## 11. 运行链落库职责

### 11.1 dispatcher

`dispatcher` 的首版职责改为：

1. 从 Redis 公共机会流读取机会
2. 根据路由和控制规则筛出目标用户
3. 为每个目标用户在数据库创建一条 `arbitrage_tasks`
4. 成功写入节点任务流后，将任务状态更新为 `DISPATCHED`

如果在创建任务后、写入节点流前失败：

- 状态保留或更新为 `FAILED`
- `status_reason` 记录写流失败原因

如果在控制规则层被拦截：

- 可选择直接创建一条 `BLOCKED` 任务，或者不落库

本次明确选择：

- `dispatcher` 在决定为某个用户生成执行任务时，就应创建数据库任务
- 若后续因控制规则阻断，则该任务进入 `BLOCKED`

这样数据库能完整反映“本来要给该用户执行，但被系统规则拦下”的事实。

### 11.2 executor

`executor` 的首版职责改为：

1. 从节点任务流取到带 `task_uuid` 的执行任务
2. 先将任务状态置为 `EXECUTING`
3. 执行成功后置为 `SUCCEEDED`
4. 执行失败后置为 `FAILED`
5. 若执行前二次控制命中，则置为 `BLOCKED`

说明：

- `executor` 必须按 `task_uuid` 更新数据库任务
- 如果找不到对应任务，应记录运行事件并拒绝执行

## 12. 节点任务流载荷设计

当前节点任务流已包含：

- `user_id`
- `source_message_id`
- 机会相关字段

本次必须新增：

- `task_uuid`

建议同时保留：

- `strategy_config_id`
- `env_mode`

原则：

- Redis 任务流是数据库任务的运行态投影
- 流里不再是“只有机会，没有任务真值标识”

## 13. 仓储与边界设计

本次建议新增三类仓储边界：

### 13.1 用户账户读取仓储

职责：

- 按用户加载启用中的交易账户
- 关联代理配置
- 返回运行时可消费的账户与代理对象

### 13.2 策略配置读取仓储

职责：

- 按用户加载已启用策略
- 给 `dispatcher` 提供任务创建所需策略参数

### 13.3 套利任务仓储

职责：

- 创建任务
- 更新状态
- 按 `task_uuid` 查询任务
- 保证幂等键唯一

## 14. 幂等与一致性

### 14.1 idempotency_key

`arbitrage_tasks.idempotency_key` 保留唯一约束。

建议首版构造来源至少包含：

- `user_id`
- `opportunity_id`
- `task_type`
- `strategy_config_id`

作用：

- 避免同一机会在短时间内为同一用户重复创建同类任务

### 14.2 数据库与 Redis 顺序

首版建议顺序：

1. 数据库创建任务，状态 `CREATED`
2. Redis 写入节点任务流，携带 `task_uuid`
3. 成功后数据库更新任务为 `DISPATCHED`

如果步骤 2 失败：

- 任务状态更新为 `FAILED`

这样可以保证：

- 数据库里不会出现“执行过了但没有任务记录”
- Redis 只是运行投递，不是任务真值源头

## 15. 迁移策略

本次建议采用增量迁移，而不是推翻现有运行模式。

### 15.1 首版读取策略

- 用户账户与策略配置先允许数据库为空
- 当前已有 `.env.worker` 的系统级配置暂时继续保留
- 当数据库中存在正式用户账户与策略配置时，优先走数据库读取边界

### 15.2 运行发布策略

- 先在测试环境接入数据库任务创建与状态回写
- 再在远端主服务器与执行节点联调
- 控制链、路由链保持现状，不在本次更换真值来源

## 16. 测试要求

本次实现后，至少需要以下测试：

1. ORM 模型列定义测试
2. 仓储层 CRUD 与幂等测试
3. `dispatcher` 创建数据库任务并写入 `task_uuid` 的测试
4. `executor` 成功、失败、阻断回写任务状态的测试
5. 数据库为空或缺少任务时的失败路径测试

## 17. 非目标约束

本次明确不做：

- 不把订单和成交明细一起落库
- 不把持仓快照、资金划转、风控事件一起拉进来
- 不实现后台任务查询接口
- 不实现 Alembic 之外的复杂迁移工具链扩展
- 不做数据库替换控制面真值

## 18. 结论

本次采用“用户账户真值 + 任务真值最小闭环”的数据库首版方案。

首版将：

- 正式引入 `users / proxies / exchange_accounts / strategy_configs / arbitrage_tasks`
- 让 `dispatcher` 创建数据库任务
- 让节点任务流携带 `task_uuid`
- 让 `executor` 回写任务状态

这样可以在不打断现有 Redis 主链的前提下，把数据库正式引入系统主线，为后续订单账本、后台查询和长期治理打下稳定基础。
