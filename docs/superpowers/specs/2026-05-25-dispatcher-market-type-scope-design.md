# market_type_scope 接入 dispatcher 设计

## 1. 文档目标

本文档定义如何把 `exchange_accounts.market_type_scope` 正式接入 `dispatcher`，让机会分发不再只判断“账户是否存在、是否覆盖当前机会交易所、是否允许自动交易”，而是进一步判断“这些账户是否覆盖当前允许的市场类型范围”。

本次目标是在不改变用户发现层职责、不改变账户仓储默认查询语义、也不改变 executor 凭证来源的前提下，把 `dispatcher` 的账户过滤精度从：

- 账户双边交易所覆盖
- 双边自动交易开关都开启

推进到：

- 账户双边交易所覆盖
- 双边自动交易开关都开启
- 双边账户的 `market_type_scope` 均满足当前机会允许的市场类型范围

这样系统就从“账户存在且允许自动交易即可分发”推进到“账户存在、允许自动交易且市场类型范围可接受才可分发”。

## 2. 范围

本次只做以下能力：

- 定义 `dispatcher` 运行层如何把 `market_type_scope` 纳入账户放行条件
- 定义当前 `spot_futures` 机会下的市场类型放行语义
- 定义 `market_type_scope` 不满足时的跳过语义与运行事件
- 定义它与现有账户覆盖过滤、自动交易过滤、策略匹配、控制规则之间的职责关系

本次不做以下能力：

- 不修改 `DispatchUserRepository` 的用户级发现语义
- 不修改 `AccountRepository` 的默认查询条件为只返回特定 `market_type_scope` 的账户
- 不修改机会 payload 结构去显式区分买边和卖边的市场类型
- 不把 `market_type_scope` 接入 executor 执行期的凭证选择
- 不实现账户 HTTP CRUD 或管理后台
- 不把本次语义扩展为“买边必须 spot、卖边必须 swap”的 side-specific 强约束

## 3. 背景与现状

当前系统已经具备以下基础：

- [models.py](file:///d:/old/FuRunSystemV4/models.py) 中 `ExchangeAccount` 已建模 `market_type_scope`
- [account_repository.py](file:///d:/old/FuRunSystemV4/app/db/account_repository.py) 已能读取用户当前 `env_mode` 下的启用账户
- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py) 中 `dispatcher` 已能按账户双边交易所覆盖与 `is_auto_trade_enabled` 过滤用户
- 最近两轮设计已明确把 `market_type_scope` 留作后续账户适配能力，而尚未进入实际运行判断

但当前仍有明显缺口：

1. `market_type_scope` 虽已建模，但还未参与任何运行时放行逻辑
2. 当前只要双边账户存在且开启自动交易，就会进入后续分发链，即使账户声明的市场类型范围并不符合预期
3. 运维侧无法区分“账户存在但市场类型范围不符合”和“账户不存在”或“自动交易关闭”

这意味着数据库账户真值虽然已经进入 dispatcher 的前置过滤层，但 `market_type_scope` 仍停留在“数据存在但行为未生效”的状态。

## 4. 问题定义

如果继续保持现状，会有以下问题：

1. `dispatcher` 可能为市场类型范围不合适的账户组合继续创建任务
2. 账户双边覆盖与自动交易开启并不等于账户真正适配当前机会
3. 运维侧无法从 `dispatcher.user.skipped` 清晰区分“账户市场类型范围不符合”的跳过原因
4. 后续推进账户真值驱动执行链时，`market_type_scope` 仍会是一个明显未闭环的缺口

因此，本次需要把 `market_type_scope` 作为账户级放行条件正式接入 `dispatcher`。

## 5. 设计目标

本次设计满足以下目标：

1. 双边账户都必须满足当前机会允许的 `market_type_scope` 才继续分发
2. 市场类型判断继续放在运行层，不改发现层边界
3. `AccountRepository` 保持通用查询职责，不被改造成只返回特定市场类型账户
4. 跳过原因能清晰区分“账户覆盖不足”“自动交易未开启”“市场类型范围不匹配”
5. 现有策略匹配、控制规则、任务状态机和节点流保持兼容

## 6. 方案比较

### 6.1 方案 A：运行层扩展账户覆盖 helper

做法：

- `DispatchUserRepository` 不变
- `AccountRepository` 不变
- `dispatcher` 在现有账户覆盖 helper 基础上，同时检查 `exchange`、`is_auto_trade_enabled` 与 `market_type_scope`

优点：

- 最符合当前分层
- 不改变仓储通用语义
- 最容易沿着已完成的账户覆盖逻辑继续演进

缺点：

- 运行层 helper 会再多一点解析逻辑

### 6.2 方案 B：修改 AccountRepository，按市场类型预过滤账户

做法：

- 在 `AccountRepository` 中新增或替换为按 `market_type_scope` 预过滤的查询接口
- `dispatcher` 继续复用现有双边覆盖逻辑

优点：

- 运行层表面上更简洁

缺点：

- 仓储会开始承担机会级语义
- 当前机会并没有显式 side-specific 市场类型，仓储很难表达清楚过滤意图
- 其他场景若需要拿到完整启用账户，仍然要保留通用查询接口

### 6.3 方案 C：只做观测，不做实际阻断

做法：

- `dispatcher` 只记录 `market_type_scope` 相关事件和字段
- 先不把它纳入实际放行条件

优点：

- 风险最小

缺点：

- 仍然无法把数据库字段变成真实运行约束
- 不能满足本轮“让 `market_type_scope` 真实生效”的目标

### 6.4 推荐方案

本次采用方案 A。

原因：

- 当前 `dispatcher` 已经把“机会级账户放行条件”集中在运行层处理，`market_type_scope` 最适合沿着这条链路继续扩展
- 当前机会 payload 只暴露 `buy_exchange / sell_exchange`，没有显式 side-specific 市场类型，因此本轮不宜把语义过早固化到仓储层
- 先在运行层做粗粒度市场类型放行，既能让字段真实生效，也为后续更精确的 side-specific 演进保留空间

## 7. 核心设计决策

### 7.1 当前机会的市场类型语义

本次只覆盖当前 `spot_futures` 机会。

由于当前 dispatcher 接收到的机会 payload 中没有独立的“买边市场类型”和“卖边市场类型”字段，本次不引入 side-specific 映射，不强制解释为：

- 买边必须是 `spot`
- 卖边必须是 `swap`

本次采用的首版语义是：

- 当前 `spot_futures` 机会允许的市场类型集合定义为 `{spot, swap}`
- 对用户当前机会涉及的两边账户，只要账户声明的 `market_type_scope` 与该允许集合有交集，即视为该账户在本轮放行规则下可接受
- 不区分买边与卖边分别命中哪一种市场类型

说明：

- 这是一个“粗粒度允许集合”规则，而不是 side-specific 规则
- 该语义与当前机会 payload 的信息粒度一致
- 后续若机会结构显式携带 side-specific 市场类型，再单独演进到更精确的约束

### 7.2 market_type_scope 解析规则

`market_type_scope` 当前存储为字符串，本次运行层按以下规则解析：

- 对原始值按逗号分隔
- 去除首尾空白并统一转为小写
- 空字符串、只含空白、或解析后为空集合，视为未声明任何市场类型

例如：

- `spot,swap` -> `{spot, swap}`
- ` spot , swap ` -> `{spot, swap}`
- `spot` -> `{spot}`
- `swap` -> `{swap}`
- `""` 或 `None` -> `{}`

说明：

- 本次只识别 `spot` 与 `swap`
- 其他值若出现，不参与允许集合命中

### 7.3 账户放行判断边界

本次不修改 `AccountRepository` 接口，继续使用：

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

- 先取当前用户 `env_mode` 下的启用账户列表
- 先做现有交易所双边覆盖判断
- 再做现有双边自动交易开启判断
- 最后从相关账户中解析 `market_type_scope`
- 仅当两边账户都至少存在一条记录，其 `market_type_scope` 与当前机会允许集合有交集时，才继续进入策略匹配

### 7.4 dispatcher 数据流

`dispatcher` 的目标流程调整为：

1. 从公共机会流读取一条机会
2. 用 `DispatchUserRepository` 得到候选用户集合
3. 对每个候选用户：
   - 查 Redis 路由
   - 查当前 `env_mode` 下的启用账户
   - 校验账户是否双边覆盖 `buy_exchange` 与 `sell_exchange`
   - 校验双边账户是否都开启 `is_auto_trade_enabled`
   - 校验双边账户是否都满足当前允许的 `market_type_scope`
   - 若通过，再进入现有策略匹配链
4. 对命中策略继续走：
   - 控制判断
   - 创建 `arbitrage_tasks`
   - 写节点任务流
   - 标记 `DISPATCHED`

说明：

- 本次不改变任务粒度，仍是“用户 + 策略 + 机会”
- 本次只在策略匹配前新增账户市场类型放行过滤

### 7.5 相关账户的判定方式

本次不要求同一用户在同一交易所只能有一条账户记录，因此运行层应按交易所聚合同侧可用账户。

对某一边交易所，判断规则为：

- 先筛出当前 `exchange` 下、当前 `env_mode` 下、且 `is_enabled=true` 的账户
- 再从其中筛出 `is_auto_trade_enabled=true` 的账户
- 对这些账户的 `market_type_scope` 做解析
- 只要其中任意一条账户与当前允许集合有交集，即视为该边通过市场类型放行

这意味着：

- 不要求同一条账户同时承担所有放行语义之外的更多责任
- 仍以“某边存在至少一条可接受账户”为放行标准

### 7.6 跳过语义

本次保留现有账户覆盖不足原因：

- `account_exchange_coverage_missing`

本次保留现有自动交易关闭原因：

- `account_auto_trade_disabled`

并新增市场类型范围不匹配原因：

- `account_market_type_scope_missing`

适用规则：

- 若缺少买边或卖边账户：使用 `account_exchange_coverage_missing`
- 若双边账户都存在，但任意一边没有自动交易开启账户：使用 `account_auto_trade_disabled`
- 若双边账户都存在且双边都有自动交易开启账户，但任意一边不存在满足当前允许市场类型集合的账户：使用 `account_market_type_scope_missing`

建议在 `dispatcher.user.skipped` 的 payload 中附带：

- `user_id`
- `buy_exchange`
- `sell_exchange`
- `available_exchanges`
- `auto_trade_enabled_exchanges`
- `market_type_scopes_by_exchange`
- `allowed_market_types`

说明：

- `market_type_scopes_by_exchange` 用于排查该用户在每个交易所下实际声明了哪些 scope
- `allowed_market_types` 用于说明当前机会在本轮规则下接受哪些市场类型

### 7.7 运行事件

本次沿用现有 `dispatcher.user.skipped` 事件名，不新增新的事件类别。

变化只有：

- 新增 `reason = account_market_type_scope_missing`
- 新增 `market_type_scopes_by_exchange`
- 新增 `allowed_market_types`

这样可以保持：

- 事件消费方无需新增事件名适配
- 运维可直接按 `reason` 区分问题类型

### 7.8 与现有控制规则的关系

本次不改变控制规则输入：

- `ControlGuard` 仍在账户覆盖、自动交易、市场类型过滤之后执行
- `strategy_id` 透传逻辑保持不变
- `control.rule.blocked / resized` 事件保持不变

原因：

- `market_type_scope` 属于账户级任务放行条件
- 控制规则属于风险放行条件
- 两者语义不同，仍应分层

### 7.9 与后续演进的关系

本次只把 `market_type_scope` 以“粗粒度允许集合”方式接入 `dispatcher` 放行判断。

后续可继续演进为：

- 机会 payload 显式区分买边和卖边市场类型
- 买边强制要求 `spot`、卖边强制要求 `swap`
- executor 直接读取数据库账户真值作为凭证来源
- `account_region` 与节点区域、策略区域联动

但这些都不属于本次范围。

## 8. 测试与验证

本次应至少覆盖以下验证：

1. 双边账户存在、双边自动交易开启、双边 `market_type_scope` 都与 `{spot, swap}` 有交集时，可继续进入策略匹配
2. 买边账户存在且自动交易开启，但其 `market_type_scope` 为空或不命中允许集合时，被 `account_market_type_scope_missing` 跳过
3. 卖边账户存在且自动交易开启，但其 `market_type_scope` 为空或不命中允许集合时，被 `account_market_type_scope_missing` 跳过
4. 某交易所存在多条账户记录时，只要其中任意一条满足允许集合，该边即视为通过
5. `market_type_scope` 失败时不创建任务也不写节点流
6. `dispatcher.user.skipped` payload 包含 `market_type_scopes_by_exchange` 与 `allowed_market_types`
7. `market_type_scope` 失败时不应产生新的 `control.rule.blocked / resized`
8. 现有 `account_exchange_coverage_missing` 与 `account_auto_trade_disabled` 语义不回归

## 9. 风险与兼容性

本次方案的主要风险在于：

1. 当前机会 payload 不区分 side-specific 市场类型，因此首版规则只能是粗粒度放行，而不是精确语义
2. 若数据库中已有历史账户把 `market_type_scope` 留空，接入后这些账户会被更早拦截
3. 若历史数据中存在非标准值，首版解析会把它们视为无效 scope

对应缓解方式：

- 明确首版语义只解决“字段真实生效”，不冒进做 side-specific 推断
- 通过 `dispatcher.user.skipped` 的新字段为远端排障提供直接证据
- 用本地测试和远端 canary 验证覆盖空值、脏值、多账户记录和恢复路径

## 10. 结论

本次设计把 `exchange_accounts.market_type_scope` 以最小但真实生效的方式接入 `dispatcher`：

- 不改变发现层职责
- 不改变账户仓储通用语义
- 不提前下沉到 executor
- 只在 dispatcher 运行层新增一层市场类型放行判断

在当前机会结构还不支持 side-specific 市场类型表达的前提下，这是让 `market_type_scope` 尽快进入真实运行链、同时保持后续演进空间的最稳妥方案。
