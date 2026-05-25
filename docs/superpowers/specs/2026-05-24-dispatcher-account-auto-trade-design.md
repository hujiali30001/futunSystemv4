# is_auto_trade_enabled 接入 dispatcher 设计

## 1. 文档目标

本文档定义如何把 `exchange_accounts.is_auto_trade_enabled` 正式接入 `dispatcher`，让机会分发不再只判断“账户是否存在且覆盖当前机会交易所”，而是进一步判断“这些账户是否允许自动交易”。

本次目标是在不改变发现层职责和执行链凭证来源的前提下，把 `dispatcher` 的账户过滤精度从“账户双边交易所覆盖”推进到“账户双边交易所覆盖 + 双边自动交易开关都开启”，做到：

- `dispatcher` 继续按数据库自动发现候选用户
- 继续要求账户覆盖当前机会的 `buy_exchange` 与 `sell_exchange`
- 同时要求这两边账户都满足 `is_auto_trade_enabled = true`
- `DispatchUserRepository` 与 `AccountRepository` 的职责边界保持不变
- 现有 `strategy_configs -> arbitrage_tasks -> executor` 主链保持稳定

这样系统就从“账户覆盖完整就可分发”推进到“账户覆盖完整且允许自动交易才可分发”。

## 2. 范围

本次只做以下能力：

- 定义 `dispatcher` 运行层如何把 `is_auto_trade_enabled` 纳入账户放行条件
- 定义当前机会 `buy_exchange / sell_exchange` 双边账户自动交易开关的判断规则
- 定义自动交易开关关闭时的跳过语义与运行事件
- 定义它与现有账户覆盖过滤、策略匹配、控制规则之间的职责关系

本次不做以下能力：

- 不修改 `DispatchUserRepository` 的用户级发现语义
- 不修改 `AccountRepository` 的查询条件为默认只返回 `is_auto_trade_enabled=true` 的账户
- 不把执行链凭证来源切到数据库账户
- 不把 `is_auto_trade_enabled` 接入 executor
- 不实现账户 HTTP CRUD 或管理后台

## 3. 背景与现状

当前系统已经具备以下基础：

- [models.py](file:///d:/old/FuRunSystemV4/models.py) 中 `ExchangeAccount` 已建模 `is_auto_trade_enabled`
- [account_repository.py](file:///d:/old/FuRunSystemV4/app/db/account_repository.py) 已能读取用户当前 `env_mode` 下的启用账户
- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py) 中 `dispatcher` 已能按账户双边交易所覆盖过滤用户
- 远端已验证“单边账户会被 `account_exchange_coverage_missing` 跳过，补齐后恢复进入任务链”

但当前仍有明显缺口：

1. `is_auto_trade_enabled` 虽已建模，但还未参与任何运行时放行逻辑
2. 当前只要账户覆盖 `buy_exchange` 与 `sell_exchange`，即使其中一边账户关闭自动交易，也仍会进入后续分发链
3. 这会让“账户存在”与“账户允许自动交易”这两个语义在 dispatcher 中仍然没有被区分

这意味着数据库账户真值虽然已经进入账户覆盖过滤层，但自动交易开关仍停留在“数据存在但行为未生效”的状态。

## 4. 问题定义

如果继续保持现状，会有以下问题：

1. `dispatcher` 可能为不允许自动交易的账户组合继续创建任务
2. 账户覆盖完整不等于真实可自动交易，当前仍缺一层账户级放行判断
3. 运维侧无法区分“账户缺失”和“账户存在但自动交易开关关闭”
4. 后续推进账户管理能力时，运行链仍会保留自动交易开关未生效的明显缺口

因此，本次必须把 `is_auto_trade_enabled` 作为账户级放行条件正式接入 `dispatcher`。

## 5. 设计目标

本次设计满足以下目标：

1. 当前机会的买卖两边账户都必须 `is_auto_trade_enabled=true` 才继续分发
2. 自动交易开关判断继续放在运行层，不改发现层边界
3. `AccountRepository` 保持通用查询职责，不被改造成“只返回自动交易账户”
4. 跳过原因能清晰区分“账户覆盖不足”和“自动交易未开启”
5. 现有策略匹配、控制规则、任务状态机和节点流保持兼容

## 6. 方案比较

### 6.1 方案 A：运行层扩展账户覆盖 helper

做法：

- `DispatchUserRepository` 不变
- `AccountRepository` 不变
- `dispatcher` 在现有账户覆盖 helper 基础上，同时检查 `exchange` 与 `is_auto_trade_enabled`

优点：

- 最符合当前分层
- 不改变仓储通用语义
- 最容易沿着已完成的账户覆盖逻辑继续演进

缺点：

- 运行层 helper 会再多一点逻辑

### 6.2 方案 B：修改 AccountRepository 默认过滤自动交易账户

做法：

- 把 `AccountRepository.list_enabled_accounts()` 改成默认只返回 `is_auto_trade_enabled=true` 的账户
- `dispatcher` 复用现有双边覆盖逻辑

优点：

- 表面实现较小

缺点：

- 会把仓储语义改窄
- 后续若其他场景需要“已启用但暂不自动交易”的账户，会被仓储隐藏掉

### 6.3 方案 C：把自动交易开关提前到发现层

做法：

- 在 `DispatchUserRepository` 发现候选用户时就要求存在自动交易开启的账户

优点：

- 过滤较早

缺点：

- 发现层仍回答不了“当前机会的买卖两边是否都开启自动交易”
- 会让发现层承担机会无关但账户放行相关的额外职责

## 7. 推荐方案

推荐采用 `方案 A：运行层扩展账户覆盖 helper`。

原因：

- 当前规则明确依赖机会里的 `buy_exchange / sell_exchange`
- 这天然属于运行层过滤，不应挤进发现层
- 也不应为一个 dispatcher 专用放行条件去收窄 `AccountRepository` 的通用语义

## 8. 核心设计

### 8.1 总体边界

本次把 `dispatcher` 的账户过滤继续拆成两层：

1. 账户覆盖判断：账户是否同时覆盖 `buy_exchange / sell_exchange`
2. 自动交易判断：覆盖到的这两边账户是否都允许自动交易

职责分工：

- `DispatchUserRepository`：继续只做用户级资格发现
- `AccountRepository`：继续返回当前 `env_mode` 下的启用账户明细
- `dispatcher` 运行层：负责把账户存在性、账户覆盖性和自动交易开关合并成一次真实放行判断

### 8.2 自动交易放行规则

首版规则采用：

- `buy_exchange` 和 `sell_exchange` 双边账户都必须存在
- 且这两边账户都必须满足 `is_auto_trade_enabled = true`

具体定义：

- 若用户不存在 `buy_exchange` 账户或 `sell_exchange` 账户，仍归类为账户覆盖不足
- 若两边账户都存在，但任意一边 `is_auto_trade_enabled = false`，归类为自动交易开关未开启
- 只有双边账户存在且双边都开启自动交易，才继续进入策略匹配

说明：

- 两边可以是不同账户记录
- 本次不要求校验代理、区域、`market_type_scope`

### 8.3 运行层实现边界

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
- 再从这些账户中提取 `is_auto_trade_enabled=true` 的交易所集合
- 仅当该集合同时包含 `buy_exchange` 与 `sell_exchange` 时，才继续进入策略匹配

### 8.4 dispatcher 数据流

`dispatcher` 的目标流程调整为：

1. 从公共机会流读取一条机会
2. 用 `DispatchUserRepository` 得到候选用户集合
3. 对每个候选用户：
   - 查 Redis 路由
   - 查当前 `env_mode` 下的启用账户
   - 校验账户是否双边覆盖 `buy_exchange / sell_exchange`
   - 校验双边账户是否都开启 `is_auto_trade_enabled`
   - 若通过，再进入现有策略匹配链
4. 对命中策略继续走：
   - 控制判断
   - 创建 `arbitrage_tasks`
   - 写节点任务流
   - 标记 `DISPATCHED`

说明：

- 本次不改变任务粒度，仍是“用户 + 策略 + 机会”
- 本次只在策略匹配前新增账户自动交易放行过滤

### 8.5 跳过语义

本次保留现有账户覆盖不足原因：

- `account_exchange_coverage_missing`

并新增自动交易关闭原因：

- `account_auto_trade_disabled`

适用规则：

- 若缺少买边或卖边账户：使用 `account_exchange_coverage_missing`
- 若双边账户都存在，但任意一边自动交易关闭：使用 `account_auto_trade_disabled`

建议在 `dispatcher.user.skipped` 的 payload 中附带：

- `user_id`
- `buy_exchange`
- `sell_exchange`
- `available_exchanges`
- `auto_trade_enabled_exchanges`

说明：

- 这样能清楚地区分“账户不存在”和“账户存在但不允许自动交易”

### 8.6 运行事件

本次沿用现有 `dispatcher.user.skipped` 事件名，不新增新的事件类别。

变化只有：

- 新增 `reason = account_auto_trade_disabled`
- 新增 `auto_trade_enabled_exchanges` 作为排查字段

这样可以保持：

- 事件消费方无需新增事件名适配
- 运维可直接按 `reason` 区分问题类型

### 8.7 与现有控制规则的关系

本次不改变控制规则输入：

- `ControlGuard` 仍在账户覆盖与自动交易过滤之后执行
- `strategy_id` 透传逻辑保持不变
- `control.rule.blocked / resized` 事件保持不变

原因：

- 自动交易开关属于账户级任务放行条件
- 控制规则属于风控放行条件
- 两者语义不同，仍应分层

### 8.8 与后续演进的关系

本次只把 `is_auto_trade_enabled` 接入 `dispatcher` 放行判断。

后续可继续演进为：

- `market_type_scope` 进入账户适配判断
- `account_region` 与节点区域/策略区域联动
- executor 直接读取数据库账户真值作为凭证来源
- 账户管理入口直接控制 `is_auto_trade_enabled`

但这些都不属于本次范围。

## 9. 测试与验证

本次应至少覆盖以下验证：

1. 双边账户存在且双边自动交易开启时，可继续进入策略匹配
2. 双边账户存在，但买边自动交易关闭时被跳过
3. 双边账户存在，但卖边自动交易关闭时被跳过
4. 双边账户存在，但任意一边自动交易关闭时不创建任务也不写节点流
5. `dispatcher.user.skipped` 在该场景下记录 `account_auto_trade_disabled`
6. `dispatcher.user.skipped` payload 包含 `auto_trade_enabled_exchanges`
7. 现有 `account_exchange_coverage_missing` 语义不回归
8. 现有策略匹配、控制规则和任务状态机不回归

## 10. 迁移策略

本次采用增量迁移：

1. 先补自动交易开关相关测试
2. 再扩展现有账户覆盖 helper
3. 再补 skip reason 与文档
4. 最后做相关回归和远端联调

原则：

- 不推翻刚完成的数据库用户自动发现
- 不推翻账户双边交易所覆盖过滤
- 不把自动交易开关与执行链凭证数据库化绑成同一个阶段

## 11. 成功标准

完成后，系统应达到以下结果：

- `dispatcher` 不仅要求账户双边覆盖，还要求双边都开启自动交易
- 双边账户存在但任意一边关闭自动交易时，不再进入策略匹配与任务创建链
- 跳过日志能清晰区分“账户覆盖不足”和“自动交易关闭”
- 现有 `strategy_configs -> arbitrage_tasks -> node stream -> executor` 主链保持稳定
