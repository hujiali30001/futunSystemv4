# 账户级交易所覆盖接入 dispatcher 设计

## 1. 文档目标

本文档定义如何把数据库中的账户级交易所覆盖规则正式接入 `dispatcher`，让机会分发不再只判断“用户有没有账户”，而是进一步判断“该用户的账户是否覆盖当前机会涉及的买卖交易所”。

本次目标是在不改变执行链凭证来源的前提下，把 `dispatcher` 的用户过滤精度从“用户级资格”推进到“账户级交易所覆盖”，做到：

- `dispatcher` 继续按数据库自动发现候选用户
- 每条机会在进入策略匹配前，先检查用户账户是否同时覆盖 `buy_exchange` 与 `sell_exchange`
- 只覆盖一边交易所的用户不会进入该机会的任务创建链
- `DispatchUserRepository` 保持用户级发现职责，不变成机会感知仓储
- 现有 `strategy_configs -> arbitrage_tasks -> executor` 主链保持稳定

这样系统就从“用户有账户就可能被分发”推进到“用户账户真正覆盖当前机会交易所才会被分发”。

## 2. 范围

本次只做以下能力：

- 定义账户级交易所覆盖的运行时过滤规则
- 定义 `dispatcher` 如何使用 `AccountRepository` 做机会级双边覆盖判断
- 定义缺失买边/卖边账户时的跳过语义和运行事件
- 定义与现有数据库用户发现层、策略匹配层的职责边界

本次不做以下能力：

- 不把账户发现仓储改造成机会感知查询
- 不把执行链凭证来源切到数据库账户
- 不把代理读取切换到数据库账户级代理
- 不实现账户级 HTTP CRUD 或管理后台
- 不实现 `market_type_scope`、`account_region`、`is_auto_trade_enabled` 的运行时过滤

## 3. 背景与现状

当前系统已经具备以下基础：

- [dispatch_user_repository.py](file:///d:/old/FuRunSystemV4/app/db/dispatch_user_repository.py) 已能按 `env_mode` 发现“有启用账户且有启用策略”的用户
- [account_repository.py](file:///d:/old/FuRunSystemV4/app/db/account_repository.py) 已能按 `user_id + env_mode` 读取启用账户和代理
- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py) 中 `dispatcher` 已能按“候选用户 -> 路由 -> 策略 -> 控制 -> 建任务”推进
- 机会 payload 已全链路保留 `buy_exchange` 与 `sell_exchange`

但当前仍有明显缺口：

1. `DispatchUserRepository` 只判断“用户至少有一条启用账户”，不判断账户是否覆盖当前机会交易所
2. `dispatcher` 当前不会读取账户明细，因此无法区分“只覆盖一边交易所”和“双边都覆盖”
3. 用户即使只有 `buy_exchange` 或只有 `sell_exchange` 一边账户，当前仍可能进入后续策略匹配与建任务链
4. 这会造成一部分实际不可执行的任务在 `dispatcher` 阶段没有被提前过滤

这意味着数据库账户真值虽然已经进入用户发现层，但还没有进入机会级分发过滤层。

## 4. 问题定义

如果继续保持现状，会有以下问题：

1. `dispatcher` 会为账户覆盖不完整的用户创建本不该进入后续链路的候选机会
2. 策略匹配成功不等于真实可执行，任务创建前仍缺一层账户覆盖校验
3. 后续若继续推进数据库账户真值，会在运行链里持续保留一块明显的“假阳性过滤缺口”
4. 观测层无法区分“用户总体有资格”与“这次机会具体缺交易所账户覆盖”

因此，本次必须把“当前机会的买卖交易所是否被用户账户双边覆盖”接入 `dispatcher` 运行层。

## 5. 设计目标

本次设计满足以下目标：

1. 候选用户在每条机会上必须通过账户双边覆盖校验
2. 首版规则采用“`buy_exchange` 和 `sell_exchange` 双边都要有”
3. `DispatchUserRepository` 继续只负责用户级资格发现
4. `AccountRepository` 负责提供运行层账户明细
5. 现有策略匹配、控制规则、任务状态机和节点流保持兼容

## 6. 方案比较

### 6.1 方案 A：把账户覆盖提前到发现仓储

做法：

- 把 `DispatchUserRepository` 改成按 `env_mode + buy_exchange + sell_exchange` 发现用户
- 每条机会调用机会感知的发现仓储

优点：

- 过滤较早
- `dispatcher` 主循环更短

缺点：

- 发现层变成机会感知，职责变重
- 会推翻刚建立的“发现层只做用户资格”的边界

### 6.2 方案 B：运行层直接用账户仓储做双边覆盖

做法：

- `DispatchUserRepository` 保持不变
- `dispatcher` 取得候选用户后，再按机会读取用户账户并做双边覆盖校验

优点：

- 改动集中在运行层
- 职责边界清晰

缺点：

- 每条机会都要做账户明细校验
- 若不注意结构，`live_workers.py` 容易继续变大

### 6.3 方案 C：混合方案

做法：

- `DispatchUserRepository` 继续负责用户级资格发现
- `dispatcher` 装配 `AccountRepository`
- 对每个候选用户，在处理当前机会时用账户仓储做双边覆盖校验

优点：

- 既不破坏发现层边界，也能把账户覆盖放在真正依赖机会字段的位置
- 与现有 `DB 自动发现 -> 策略匹配` 演进路径最一致

缺点：

- 需要在 `dispatcher` 中增加一层账户覆盖 helper

## 7. 推荐方案

推荐采用 `方案 C：混合方案`。

原因：

- 账户覆盖本质上依赖当前机会的 `buy_exchange / sell_exchange`
- 这属于运行层判断，不应把发现层改成机会感知
- 该方案能最小范围打通“用户发现层”和“账户覆盖层”，又不扩散到 executor

## 8. 核心设计

### 8.1 总体边界

本次把 `dispatcher` 的过滤边界拆成三层：

1. 用户发现层：`DispatchUserRepository`
2. 账户覆盖层：`AccountRepository`
3. 策略匹配层：`StrategyConfigRepository` + 策略匹配函数

职责分工：

- 用户发现层回答“这个用户总体上可不可以被 dispatcher 关注”
- 账户覆盖层回答“这个用户是否覆盖本次机会的买卖交易所”
- 策略匹配层回答“这个用户的哪些策略命中本次机会”

原则：

- 不让发现层承担机会感知
- 不让策略层承担账户覆盖判断

### 8.2 账户覆盖规则

首版账户覆盖规则采用：

- `buy_exchange` 和 `sell_exchange` 双边都要有

具体定义：

- 用户在当前 `env_mode` 下必须至少存在一条启用账户，其 `exchange == buy_exchange`
- 同时必须至少存在一条启用账户，其 `exchange == sell_exchange`
- 两条账户可以是不同账户记录
- 只覆盖一边交易所，不算通过

说明：

- 本次不要求账户必须共享同一代理或同一区域
- 本次不要求账户的 `market_type_scope` 覆盖当前执行场景
- 本次不要求检查 `is_auto_trade_enabled`

### 8.3 运行层账户读取边界

本次不新增新的账户查询仓储，继续使用现有 `AccountRepository`。

建议接口继续为：

```python
class AccountRepository:
    def list_enabled_accounts(
        self,
        *,
        user_id: int,
        env_mode: str,
    ) -> list[ExchangeAccount]:
        ...
```

`dispatcher` 的使用方式：

- 对每个候选用户，先取该用户当前 `env_mode` 下的启用账户列表
- 再从账户列表中提取 `exchange` 集合
- 仅当集合同时包含 `buy_exchange` 与 `sell_exchange` 时，才继续进入策略匹配

### 8.4 dispatcher 数据流

`dispatcher` 的目标流程调整为：

1. 从公共机会流读取一条机会
2. 用 `DispatchUserRepository` 得到候选用户集合
3. 对每个候选用户：
   - 查 Redis 路由
   - 查当前 `env_mode` 下的启用账户
   - 校验账户是否双边覆盖 `buy_exchange` 和 `sell_exchange`
   - 若覆盖通过，再进入现有策略匹配链
4. 对命中策略继续走：
   - 控制判断
   - 创建 `arbitrage_tasks`
   - 写节点任务流
   - 标记 `DISPATCHED`

说明：

- 本次不改变任务粒度，仍是“用户 + 策略 + 机会”
- 本次只在策略匹配前新增账户双边覆盖过滤

### 8.5 跳过语义

首版明确以下账户覆盖跳过场景：

- 缺少 `buy_exchange` 账户：跳过
- 缺少 `sell_exchange` 账户：跳过
- 仅覆盖一边交易所：跳过

建议统一为以下原因值：

- `account_exchange_coverage_missing`

并在 payload 中额外携带：

- `user_id`
- `buy_exchange`
- `sell_exchange`
- `available_exchanges`

说明：

- 缺买边或缺卖边都可以落到同一个 reason
- 由 `available_exchanges` 帮助排查是哪一边缺失

### 8.6 运行事件

本次沿用现有 `dispatcher.user.skipped` 事件，不新增新的事件名。

只增加新的 `reason` 取值：

- `account_exchange_coverage_missing`

这样可以保持：

- 事件消费方无需新增事件类别
- 日志观测可以按 `reason` 细分

### 8.7 与现有控制规则的关系

本次不改变控制规则输入：

- `ControlGuard` 仍在账户覆盖和策略匹配之后执行
- `strategy_id` 透传逻辑保持不变
- `control.rule.blocked / resized` 事件保持不变

原因：

- 账户覆盖属于任务创建前置过滤
- 控制规则属于任务创建前的风险放行
- 两者语义不同，不应混合

### 8.8 与账户真值后续演进的关系

本次只把 `exchange_accounts.exchange` 接入运行过滤。

后续可继续演进为：

- `is_auto_trade_enabled` 进入账户覆盖放行条件
- `market_type_scope` 进入账户适配判断
- `account_region` 与节点区域或策略区域联动
- executor 凭证/代理改为数据库账户真值来源

但这些都不属于本次范围。

## 9. 测试与验证

本次应至少覆盖以下验证：

1. 用户同时拥有 `buy_exchange` 和 `sell_exchange` 账户时可继续进入策略匹配
2. 用户只拥有买边账户时被跳过
3. 用户只拥有卖边账户时被跳过
4. 用户有路由但账户双边覆盖不足时，不创建任务也不写节点流
5. `dispatcher.user.skipped` 在账户覆盖不足时会记录 `account_exchange_coverage_missing`
6. 白名单覆盖语义保持不变
7. 现有策略匹配、控制规则和任务状态机不回归

## 10. 迁移策略

本次采用增量迁移：

1. 先补账户双边覆盖相关测试
2. 再在 `dispatcher` 中装配 `AccountRepository`
3. 再新增账户覆盖 helper 与跳过事件 reason
4. 最后做相关回归和远端联调

原则：

- 不推翻刚完成的数据库用户自动发现
- 不改变 executor 当前凭证来源
- 不把账户覆盖与账户凭证数据库化绑成同一个阶段

## 11. 成功标准

完成后，系统应达到以下结果：

- `dispatcher` 不仅按数据库发现候选用户，还会按当前机会做账户双边交易所覆盖过滤
- 只覆盖一边交易所的用户，不再进入策略匹配与任务创建链
- `dispatcher.user.skipped` 能明确标识账户覆盖不足
- 现有 `strategy_configs -> arbitrage_tasks -> node stream -> executor` 主链保持稳定
