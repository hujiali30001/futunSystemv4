# 真实账户接入 dispatcher 设计

## 1. 文档目标

本文档定义如何把数据库中的真实用户与账户真值正式接入 `dispatcher`，让机会分发不再主要依赖静态 `dispatch_user_ids`，而是由数据库自动发现“当前可分发的用户集合”。

本次目标是把 `dispatcher` 的“候选用户发现”从配置白名单推进到数据库真值，做到：

- `dispatcher` 可从数据库自动发现候选用户
- 候选用户必须同时具备启用中的真实账户与启用中的策略
- Redis 路由继续只负责回答“该发到哪个 node”
- `dispatch_user_ids` 从默认真值入口退为可选覆盖开关
- 不打断现有 `strategy_configs -> arbitrage_tasks -> executor` 主链

这样系统就从“按静态用户白名单驱动分发”推进到“按数据库真实账户驱动分发”。

## 2. 范围

本次只做以下能力：

- 定义 `dispatcher` 的数据库用户发现边界
- 定义“什么样的用户可进入候选分发集合”
- 定义数据库发现与 Redis 路由的职责分工
- 定义 `dispatch_user_ids` 的兼容语义
- 定义用户无账户、无策略、无路由时的跳过语义与运行事件

本次不做以下能力：

- 不把交易所 API 凭证正式迁移到数据库读取链
- 不把执行链的代理注入切换到数据库账户级代理
- 不改变 `executor` 的凭证读取模式
- 不实现账户管理后台或账户 HTTP CRUD
- 不实现复杂的用户调度优先级或负载均衡策略

## 3. 背景与现状

当前系统已经具备以下基础：

- 数据库中已有 `users / proxies / exchange_accounts / strategy_configs / arbitrage_tasks`
- [account_repository.py](file:///d:/old/FuRunSystemV4/app/db/account_repository.py) 已能按 `user_id + env_mode` 读取启用账户和代理
- [strategy_config_repository.py](file:///d:/old/FuRunSystemV4/app/db/strategy_config_repository.py) 已能按用户读取启用中的 `spot_futures` 策略
- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py) 中 `dispatcher` 已能按“用户 + 策略 + 机会”建任务
- 远端已验证 `strategy_configs -> dispatcher -> arbitrage_tasks -> node stream` 闭环

但当前仍有明显缺口：

1. `dispatcher` 的候选用户集合仍来自静态 `dispatch_user_ids`
2. 即使数据库里已有启用账户和启用策略的用户，若未写进 `dispatch_user_ids`，当前也不会被分发
3. `exchange_accounts` 当前只存在于数据库边界，还没有真正参与 `dispatcher` 的候选用户发现
4. 这会让“数据库账户真值”在运行主链里只生效了一半

这意味着当前系统虽然已经有数据库账户模型和策略模型，但 `dispatcher` 仍未真正围绕真实账户集合运行。

## 4. 问题定义

如果继续保持现状，会有以下问题：

1. 运维必须手工维护 `dispatch_user_ids`，数据库无法成为候选用户真值来源
2. 新增一个有真实账户与策略的用户时，不能自动进入分发链
3. `exchange_accounts` 的启停状态还不能直接影响机会分发入口
4. 后续走向多用户生产化时，运行主链仍被静态白名单绑住

因此，本次必须先把“候选用户发现”这一层从静态配置迁移到数据库真值。

## 5. 设计目标

本次设计满足以下目标：

1. `dispatcher` 默认按数据库自动发现候选用户
2. 只有满足“用户可交易 + 有启用账户 + 有启用策略”的用户才进入候选集合
3. Redis 路由继续负责节点定位，不负责用户发现
4. `dispatch_user_ids` 保留为兼容与运维覆盖入口，但不再是默认真值来源
5. 现有策略分发、控制规则、任务状态机和节点流载荷保持兼容

## 6. 方案比较

### 6.1 方案 A：数据库自动发现候选用户

做法：

- `dispatcher` 先从数据库查询“当前可分发用户”
- 再对这些用户逐个检查 Redis 路由
- 有路由的用户继续进入现有“按策略建任务”链路

优点：

- 最符合“真实账户接入 dispatcher”的目标
- 数据库开始成为候选用户真值
- `exchange_accounts` 与 `strategy_configs` 真正参与主链入口

缺点：

- 会改变当前候选用户来源
- 需要补一层新的数据库发现仓储与跳过事件

### 6.2 方案 B：数据库发现结果与 `dispatch_user_ids` 取交集

做法：

- 数据库先发现候选用户
- 最终只保留与 `dispatch_user_ids` 重叠的用户

优点：

- 灰度更稳
- 回滚简单

缺点：

- 仍然保留静态白名单为主要入口
- 不足以称为“数据库自动发现”

### 6.3 方案 C：保留 `dispatch_user_ids`，数据库只做二次校验

做法：

- 仍按 `dispatch_user_ids` 遍历用户
- 只是在每个用户进入分发前检查数据库账户和策略

优点：

- 改动最小

缺点：

- 真实账户并未真正接入候选用户发现层
- 只是对白名单做了一次 DB 校验补丁

## 7. 推荐方案

推荐采用 `方案 A：数据库自动发现候选用户`。

原因：

- 用户已明确选择 `DB 自动发现`
- 当前系统最需要推进的是“数据库账户真值真正进入 dispatcher 入口”
- 该方案范围仍然可控，因为本次只做用户发现，不触碰执行链凭证来源

同时增加一个兼容约束：

- `dispatch_user_ids` 保留，但退为“可选覆盖开关”
- 默认未配置时，完全按数据库自动发现
- 显式配置时，允许在运维/灰度/回滚场景下覆盖数据库发现结果

## 8. 核心设计

### 8.1 总体边界

本次把 `dispatcher` 的职责拆成两层：

1. 发现“哪些用户值得尝试分发”
2. 对这些用户继续走现有“路由 -> 策略 -> 控制 -> 建任务 -> 发节点流”链路

其中：

- 数据库负责回答“哪些用户当前具备分发资格”
- Redis 路由负责回答“这些用户当前该发到哪个 node”

原则：

- 不让 Redis 负责用户发现
- 不让数据库负责节点路由

### 8.2 新增仓储边界

本次新增 `DispatchUserRepository`。

职责：

- 按 `env_mode` 查询当前可分发用户集合
- 只返回满足首版资格规则的用户 ID
- 按稳定顺序返回，便于测试和日志对齐

建议接口：

```python
class DispatchUserRepository:
    def list_dispatchable_user_ids(
        self,
        *,
        env_mode: str,
    ) -> list[str]:
        ...
```

说明：

- 首版只返回用户 ID，不额外返回账户明细
- `dispatcher` 继续用现有 `StrategyConfigRepository` 处理策略匹配
- `AccountRepository` 继续保留给后续账户级读取扩展

### 8.3 首版可分发用户资格规则

一名用户进入候选分发集合，至少满足：

- `users.is_trading_enabled = true`
- 存在至少一条 `exchange_accounts.is_enabled = true`
- 该账户的 `env_mode` 与当前 worker 的 `env_mode` 一致
- 存在至少一条 `strategy_configs.is_enabled = true`
- `strategy_type = "spot_futures"`

首版不额外要求：

- 不检查 `is_auto_trade_enabled`
- 不检查账户是否覆盖机会中的具体交易所
- 不检查账户凭证是否已经数据库化

原因：

- 本次目标是把数据库账户真值接入“用户发现层”
- 不一次性扩大到“账户级执行准备层”

### 8.4 `dispatch_user_ids` 兼容语义

本次将 `dispatch_user_ids` 改为可选覆盖开关。

语义如下：

- 当 `dispatch_user_ids` 为空：
  - `dispatcher` 完全按数据库自动发现候选用户
- 当 `dispatch_user_ids` 非空：
  - 仅对这些显式配置用户尝试分发
  - 但这些用户仍必须满足数据库资格规则

说明：

- 这样既保留了灰度/回滚手段
- 又不让 `dispatch_user_ids` 继续承担默认真值职责

### 8.5 dispatcher 数据流

`dispatcher` 的目标流程调整为：

1. 从公共机会流读取一条机会
2. 解析当前候选用户集合：
   - 默认走 `DispatchUserRepository.list_dispatchable_user_ids(env_mode=...)`
   - 若配置了 `dispatch_user_ids`，则对该白名单做数据库资格校验
3. 对候选用户逐个读取 Redis 路由
4. 有路由的用户继续走现有策略匹配链
5. 每条命中策略继续走：
   - 控制判断
   - 创建 `arbitrage_tasks`
   - 写入节点任务流
   - 标记 `DISPATCHED`

说明：

- 本次不改“按策略建任务”的粒度
- 本次只改变“这些用户从哪里来”

### 8.6 跳过语义

首版明确以下跳过场景：

- 用户无启用账户：不进入候选集合
- 用户无启用策略：不进入候选集合
- 用户交易总开关关闭：不进入候选集合
- 用户有数据库资格但无 Redis 路由：进入候选，但本次机会分发时跳过
- 用户有资格且有路由，但当前机会无命中策略：本次机会分发时跳过

原则：

- “数据库资格不满足”属于发现层过滤
- “路由缺失/机会不匹配”属于运行层跳过

### 8.7 运行事件

本次建议补两类轻量运行事件，便于观察发现层行为：

- `dispatcher.user.discovery.succeeded`
- `dispatcher.user.skipped`

其中 `dispatcher.user.skipped` 首版建议至少覆盖以下原因：

- `user_not_dispatchable`
- `user_route_missing`
- `no_matching_strategy`

说明：

- 这些事件只要求进结构化日志
- 不新增外部通知
- 不新增持久化查询接口

### 8.8 与现有任务状态机的关系

本次不新增任务状态，仍沿用：

- `CREATED`
- `DISPATCHED`
- `EXECUTING`
- `SUCCEEDED`
- `FAILED`
- `BLOCKED`

变化只在于：

- 进入任务创建前的“用户发现层”不再依赖静态白名单

### 8.9 与账户真值后续演进的关系

本次只把 `exchange_accounts` 接入用户发现层，不接入执行凭证层。

后续可继续演进为：

- `dispatcher` 按账户覆盖具体交易所可用性
- `executor` 从数据库账户真值读取凭证与代理
- 账户级 `is_auto_trade_enabled` 进入任务放行条件

但这些都不属于本次范围。

## 9. 测试与验证

本次应至少覆盖以下验证：

1. `DispatchUserRepository` 只返回满足资格规则的用户
2. `dispatcher` 在 `dispatch_user_ids` 为空时，会按数据库自动发现候选用户
3. `dispatcher` 在 `dispatch_user_ids` 非空时，只处理白名单中且满足数据库资格的用户
4. 无启用账户的用户不会进入候选集合
5. 无启用策略的用户不会进入候选集合
6. `env_mode` 不匹配的账户不会让用户进入候选集合
7. 候选用户无 Redis 路由时，本次机会分发跳过
8. 候选用户有路由且有命中策略时，继续走现有任务创建主链

## 10. 迁移策略

本次采用增量迁移：

1. 先新增 `DispatchUserRepository` 与资格测试
2. 再把 `dispatcher` 的候选用户来源从静态 `self.user_ids` 扩展为“数据库发现 + 可选覆盖”
3. 再补运行事件与 `worker_service` 装配
4. 最后做相关回归与远端联调

原则：

- 不推翻现有 Redis 路由
- 不推翻现有策略分发与任务状态机
- 不把账户凭证 DB 化和用户发现层绑在同一个阶段

## 11. 成功标准

完成后，系统应达到以下结果：

- 默认情况下，`dispatcher` 能按数据库自动发现可分发用户
- 新增一个具备启用账户和启用策略的用户后，无需手工维护 `dispatch_user_ids` 也可进入分发候选集合
- `dispatch_user_ids` 仍可在灰度场景下作为临时覆盖开关使用
- 用户发现层与 Redis 路由层的职责边界清晰
- 现有 `strategy_configs -> arbitrage_tasks -> node stream -> executor` 主链保持稳定
