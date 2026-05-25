# account_region 接入 dispatcher 设计

## 1. 文档目标

本文档定义如何把 `exchange_accounts.account_region` 正式接入 `dispatcher`，让机会分发不再只判断“账户是否存在、是否覆盖当前机会交易所、是否允许自动交易、是否覆盖允许的市场类型”，而是进一步判断“这些账户是否与当前 dispatcher 所在区域兼容”。

本次目标是在不改变 Redis `user-node` 路由真值、不改变用户发现层职责、也不改变 executor 凭证来源的前提下，把 `dispatcher` 的账户过滤精度从：

- 账户双边交易所覆盖
- 双边自动交易开关都开启
- 双边账户的 `market_type_scope` 满足当前允许集合

推进到：

- 账户双边交易所覆盖
- 双边自动交易开关都开启
- 双边账户的 `market_type_scope` 满足当前允许集合
- 双边账户的 `account_region` 与当前 `dispatcher.region` 兼容

这样系统就从“账户存在且允许自动交易且市场类型可接受即可分发”推进到“账户存在、允许自动交易、市场类型可接受且区域兼容才可分发”。

## 2. 范围

本次只做以下能力：

- 定义 `dispatcher` 运行层如何把 `account_region` 纳入账户放行条件
- 定义 `dispatcher.region` 与账户 `account_region` 的首版兼容语义
- 定义 `account_region` 不满足时的跳过语义与运行事件
- 定义它与现有账户覆盖过滤、自动交易过滤、市场类型过滤、策略匹配、控制规则之间的职责关系

本次不做以下能力：

- 不修改 Redis `route:user_node:{user_id}` 的真值模型
- 不让 `account_region` 参与节点选择或自动改派
- 不修改 `DispatchUserRepository` 的用户级发现语义
- 不修改 `AccountRepository` 的默认查询条件为只返回特定区域账户
- 不修改 `route_admin` HTTP 语义
- 不把 `account_region` 接入 executor 执行期的凭证选择
- 不实现账户 HTTP CRUD 或管理后台

## 3. 背景与现状

当前系统已经具备以下基础：

- [models.py](file:///d:/old/FuRunSystemV4/models.py) 中 `ExchangeAccount` 已建模 `account_region`
- [account_repository.py](file:///d:/old/FuRunSystemV4/app/db/account_repository.py) 已能读取用户当前 `env_mode` 下的启用账户
- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py) 中 `dispatcher` 已能按账户双边交易所覆盖、`is_auto_trade_enabled`、`market_type_scope` 过滤用户
- 当前 Redis 路由真值仍是 `route:user_node:{user_id}`，由 [redis_flow.py](file:///d:/old/FuRunSystemV4/app/runtime/redis_flow.py) 中的路由读取与 [route_admin_service.py](file:///d:/old/FuRunSystemV4/app/runtime/route_admin_service.py) 中的管理接口维护

但当前仍有明显缺口：

1. `account_region` 虽已建模，但还未参与任何运行时放行逻辑
2. 当前只要双边账户存在、自动交易开启、且 `market_type_scope` 可接受，就会进入后续分发链，即使这些账户声明的区域与当前 dispatcher 不兼容
3. 运维侧无法区分“账户存在但区域不兼容”和“账户不存在 / 自动交易关闭 / 市场类型不匹配”

这意味着数据库账户真值虽然已经逐步进入 dispatcher 的前置过滤层，但 `account_region` 仍停留在“数据存在但行为未生效”的状态。

## 4. 问题定义

如果继续保持现状，会有以下问题：

1. `dispatcher` 可能为区域不兼容的账户组合继续创建任务
2. 现有 `user-node` 路由真值并不能表达“该用户当前账户是否适合在此区域执行”
3. 运维侧无法从 `dispatcher.user.skipped` 清晰区分“区域不兼容”的跳过原因
4. 后续推进多区域执行链时，`account_region` 会继续成为一个明显未闭环的账户真值字段

因此，本次需要把 `account_region` 作为账户级放行条件正式接入 `dispatcher`。

## 5. 设计目标

本次设计满足以下目标：

1. 双边账户都必须与当前 `dispatcher.region` 兼容才继续分发
2. 区域判断继续放在运行层，不改发现层边界
3. `AccountRepository` 保持通用查询职责，不被改造成只返回特定区域账户
4. 跳过原因能清晰区分“账户覆盖不足”“自动交易未开启”“市场类型范围不匹配”“区域不兼容”
5. 现有 Redis `user-node` 路由模型、策略匹配、控制规则、任务状态机和节点流保持兼容

## 6. 方案比较

### 6.1 方案 A：dispatcher 运行层新增区域放行

做法：

- `DispatchUserRepository` 不变
- `AccountRepository` 不变
- Redis `user-node` 路由不变
- `dispatcher` 在现有账户放行链基础上继续检查 `account_region`

优点：

- 最符合当前“账户级机会放行条件集中在 runtime 层”的设计
- 不改变现有路由真值与运维控制面
- 可以最小代价让 `account_region` 从“仅存储字段”变成“真实运行约束”

缺点：

- `dispatcher` 运行层 helper 会再多一层聚合逻辑

### 6.2 方案 B：修改 AccountRepository，按区域预过滤账户

做法：

- 在 `AccountRepository` 中新增或替换为按 `account_region` 预过滤的查询接口
- `dispatcher` 继续复用现有放行逻辑

优点：

- 运行层表面更简洁

缺点：

- 仓储会开始承担 dispatcher 区域语义
- 其他场景若需要拿到完整启用账户，仍然需要保留通用查询接口
- 无法解决“当前 region 不兼容但 route 仍存在”的观测问题

### 6.3 方案 C：让 account_region 直接参与路由决策

做法：

- 把 `account_region` 接到 `user-node` 路由决策中，按账户区域约束节点区域

优点：

- 更接近未来区域感知执行链

缺点：

- 范围明显扩大，涉及路由真值、控制面与运维流程
- 不是一个小步闭环
- 不适合当前这条连续增量演进路径

### 6.4 推荐方案

本次采用方案 A。

原因：

- 当前 `dispatcher` 已经把“账户级机会放行条件”集中在运行层处理，`account_region` 最适合沿着这条链路继续扩展
- 当前 Redis `user-node` 路由已经是独立真值，本轮不宜同时改“路由真值”和“账户放行语义”
- 先做 dispatcher 区域放行，既能让字段真实生效，也为后续区域感知路由保留空间

## 7. 核心设计决策

### 7.1 首版区域兼容语义

本次只定义 `dispatcher.region` 与账户 `account_region` 的兼容关系，不修改节点路由模型。

首版兼容规则如下：

- 若 `account_region == "default"`，视为全局兼容
- 若 `account_region == dispatcher.region`，视为兼容
- 其他值视为不兼容

说明：

- `default` 在本次被明确解释为“可在任意 dispatcher 区域执行”
- 本次不引入区域别名映射，不做 `hk -> asia` 之类的归并
- 本次不做“发现不兼容后自动改派到别的 region”

### 7.2 account_region 解析规则

`account_region` 当前存储为字符串，本次运行层按以下规则解析：

- 取原始值后去除首尾空白
- 统一转为小写
- 若为空字符串、只含空白、或为 `None`，按 `"default"` 处理

例如：

- `"default"` -> `"default"`
- `" Main "` -> `"main"`
- `""` -> `"default"`
- `None` -> `"default"`

说明：

- 这样可以把历史空值统一收敛到全局兼容语义
- 本次不额外限制合法区域枚举，比较逻辑只依赖归一化后的字符串

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
- 再做现有双边 `market_type_scope` 判断
- 最后从相关账户中解析 `account_region`
- 仅当两边账户都至少存在一条记录，其 `account_region` 与当前 `dispatcher.region` 兼容时，才继续进入策略匹配

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
   - 校验双边账户是否都与当前 `dispatcher.region` 兼容
   - 若通过，再进入现有策略匹配链
4. 对命中策略继续走：
   - 控制判断
   - 创建 `arbitrage_tasks`
   - 写节点任务流
   - 标记 `DISPATCHED`

说明：

- 本次不改变任务粒度，仍是“用户 + 策略 + 机会”
- 本次只在策略匹配前新增账户区域放行过滤

### 7.5 相关账户的判定方式

本次不要求同一用户在同一交易所只能有一条账户记录，因此运行层应按交易所聚合同侧可用账户。

对某一边交易所，判断规则为：

- 先筛出当前 `exchange` 下、当前 `env_mode` 下、且 `is_enabled=true` 的账户
- 再从其中筛出 `is_auto_trade_enabled=true` 且 `market_type_scope` 通过的账户
- 对这些账户的 `account_region` 做归一化
- 只要其中任意一条账户与当前 `dispatcher.region` 兼容，即视为该边通过区域放行

这意味着：

- 不要求同一条账户独占该交易所的所有资格
- 仍以“某边存在至少一条可接受账户”为放行标准
- 本次不要求买边和卖边使用同一 `account_region`

### 7.6 跳过语义

本次保留现有原因：

- `account_exchange_coverage_missing`
- `account_auto_trade_disabled`
- `account_market_type_scope_missing`

并新增区域不兼容原因：

- `account_region_mismatch`

适用规则：

- 若缺少买边或卖边账户：使用 `account_exchange_coverage_missing`
- 若双边账户都存在，但任意一边没有自动交易开启账户：使用 `account_auto_trade_disabled`
- 若双边账户都存在且双边都有自动交易开启账户，但任意一边不存在满足当前允许市场类型集合的账户：使用 `account_market_type_scope_missing`
- 若前述条件都通过，但任意一边不存在与当前 `dispatcher.region` 兼容的账户：使用 `account_region_mismatch`

建议在 `dispatcher.user.skipped` 的 payload 中附带：

- `user_id`
- `buy_exchange`
- `sell_exchange`
- `dispatcher_region`
- `available_exchanges`
- `auto_trade_enabled_exchanges`
- `market_type_scopes_by_exchange`
- `account_regions_by_exchange`

说明：

- `account_regions_by_exchange` 用于排查各交易所下实际声明了哪些区域
- `dispatcher_region` 用于说明当前哪一个 dispatcher 区域在做放行判断

### 7.7 运行事件

本次沿用现有 `dispatcher.user.skipped` 事件名，不新增新的事件类别。

变化只有：

- 新增 `reason = account_region_mismatch`
- 新增 `dispatcher_region`
- 新增 `account_regions_by_exchange`

这样可以保持：

- 事件消费方无需新增事件名适配
- 运维可直接按 `reason` 区分问题类型

### 7.8 与现有控制规则的关系

本次不改变控制规则输入：

- `ControlGuard` 仍在账户覆盖、自动交易、市场类型、区域过滤之后执行
- `strategy_id` 透传逻辑保持不变
- `control.rule.blocked / resized` 事件保持不变

原因：

- `account_region` 属于账户级任务放行条件
- 控制规则属于风险放行条件
- 两者语义不同，仍应分层

### 7.9 与现有路由模型的关系

本次明确保持以下边界：

- Redis `route:user_node:{user_id}` 仍是“用户应投递到哪个节点”的运行时真值
- `account_region` 只用于判断“当前 dispatcher 是否应该为该用户创建任务”
- 若路由存在但账户区域不兼容，dispatcher 仍应直接跳过，而不是尝试改写路由

这样可以保证：

- 当前 route-admin 与运维流程不回归
- `account_region` 先在 dispatcher 侧形成真实行为闭环
- 后续若要做区域感知路由，可以在此基础上继续演进

### 7.10 与后续演进的关系

本次只把 `account_region` 以“dispatcher 区域放行”方式接入运行链。

后续可继续演进为：

- `route_admin` 或路由真值支持区域感知
- dispatcher 在区域不兼容时给出更明确的重路由建议
- executor 直接读取数据库账户真值并校验区域
- `StrategyConfig` 引入显式区域维度，与账户区域联动

但这些都不属于本次范围。

## 8. 测试与验证

本次应至少覆盖以下验证：

1. 双边账户存在、双边自动交易开启、双边 `market_type_scope` 都通过、双边 `account_region` 都为 `default` 时，可继续进入策略匹配
2. 双边账户都存在，但买边账户 `account_region` 与当前 `dispatcher.region` 不兼容时，被 `account_region_mismatch` 跳过
3. 双边账户都存在，但卖边账户 `account_region` 与当前 `dispatcher.region` 不兼容时，被 `account_region_mismatch` 跳过
4. 某交易所存在多条账户记录时，只要其中任意一条区域兼容，该边即视为通过
5. `account_region` 失败时不创建任务也不写节点流
6. `dispatcher.user.skipped` payload 包含 `dispatcher_region` 与 `account_regions_by_exchange`
7. `account_region` 失败时不应产生新的 `control.rule.blocked / resized`
8. 现有 `account_exchange_coverage_missing`、`account_auto_trade_disabled`、`account_market_type_scope_missing` 语义不回归

## 9. 风险与兼容性

本次方案的主要风险在于：

1. `default` 被解释为全局兼容，若历史数据依赖别的语义，需要后续迁移时再收紧
2. 当前只做 dispatcher 侧放行，不会自动纠正错误路由，因此“route 存在但 region 不兼容”的情况会表现为跳过
3. 历史账户若填了不规范区域名，首版只会按字符串不相等处理，从而被视为不兼容

对应缓解方式：

- 明确在 spec 中把 `default` 的语义固定为全局兼容
- 通过 `dispatcher.user.skipped` 的新字段为远端排障提供直接证据
- 用本地测试和远端 canary 验证覆盖 `default`、不兼容区域、多账户记录与恢复路径

## 10. 结论

本次设计把 `exchange_accounts.account_region` 以最小但真实生效的方式接入 `dispatcher`：

- 不改变发现层职责
- 不改变账户仓储通用语义
- 不改变 Redis `user-node` 路由真值
- 只在 dispatcher 运行层新增一层区域放行判断

在当前系统仍以 Redis 用户路由作为节点真值的前提下，这是让 `account_region` 尽快进入真实运行链、同时保持后续区域感知路由演进空间的最稳妥方案。
