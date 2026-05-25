# 策略配置接入 dispatcher 设计

## 1. 文档目标

本文档定义如何把数据库中的 `strategy_configs` 正式接入 `dispatcher`，让机会分发不再只是“按用户广播”，而是能够围绕用户启用中的策略配置生成任务。

本次目标是把已经存在但尚未落地的策略真值边界真正接入运行主链，做到：

- `dispatcher` 能按用户读取启用中的 `strategy_configs`
- 同一用户的多条启用策略可对同一个机会并行命中
- 每条命中的策略独立创建一条 `arbitrage_tasks`
- `arbitrage_tasks.strategy_config_id` 不再固定为 `None`
- 控制规则在 `dispatcher` 层真正拿到 `strategy_id`

这样系统就从“策略模型已存在但未参与分发”推进到“策略配置驱动任务创建”。

## 2. 范围

本次只做以下能力：

- 定义 `dispatcher` 读取策略配置的仓储边界
- 定义机会与策略配置的匹配规则
- 定义同一用户多策略并行时的任务创建语义
- 定义 `strategy_config_id` 如何进入任务、控制规则和节点流
- 定义相关幂等与失败处理约束

本次不做以下能力：

- 不实现 `strategy_configs` 的 HTTP CRUD 或管理后台
- 不实现复杂策略 DSL 或表达式引擎
- 不实现策略优先级、互斥组、组合编排
- 不实现 `executor` 二次查库读取完整策略配置
- 不改动现有数据库主状态机

## 3. 背景与现状

当前系统已经具备以下基础：

- [models.py](file:///d:/old/FuRunSystemV4/models.py) 已定义 `StrategyConfig` 和 `ArbitrageTask.strategy_config_id`
- [task_repository.py](file:///d:/old/FuRunSystemV4/app/db/task_repository.py) 的 `ArbitrageTaskCreate` 已预留 `strategy_config_id`
- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py) 中 `dispatcher` 已能为每个用户创建数据库任务
- 控制面规则已经支持 `strategy_id` 维度匹配

但当前仍有明显缺口：

1. `dispatcher` 建任务时仍固定写 `strategy_config_id=None`
2. `dispatcher` 的控制判断未传入真实 `strategy_id`
3. 当前任务生成仍是“按用户 + 机会”一条，不是“按用户 + 策略 + 机会”生成
4. 同一机会下多策略并行没有正式语义与幂等边界

这意味着数据库中虽然已有策略模型，但策略真值还没有真正进入任务主链。

## 4. 问题定义

如果继续保持现状，会有以下问题：

1. `arbitrage_tasks` 无法表达任务到底由哪条策略触发
2. 控制面中的“按策略额度”规则在 `dispatcher` 层不会真正生效
3. 同一用户即使配置了多条策略，也无法对同一机会并行产出多条任务
4. 后续做任务查询、策略回溯和效果分析时，缺少策略到任务的稳定关联

因此，本次必须先把“策略匹配 -> 任务创建 -> 控制判断 -> 节点流投递”这条链路接起来。

## 5. 设计目标

本次设计满足以下目标：

1. `strategy_configs` 成为 `dispatcher` 生成任务的正式输入之一
2. 同一用户允许多条启用策略对同一机会并行生成任务
3. 每条策略任务都带有真实 `strategy_config_id`
4. 幂等键能够区分同一机会下的不同策略任务
5. 控制规则在 `dispatcher` 侧可基于真实 `strategy_id` 判定

## 6. 方案比较

### 6.1 方案 A：单条最优策略命中

做法：

- 同一用户的多条启用策略中，每个机会只选最匹配的一条
- 每个用户对每个机会最多生成一条任务

优点：

- 实现简单
- 幂等和控制语义容易保持兼容

缺点：

- 不符合本次“多策略并行”的明确需求
- 需要额外定义“最优策略”的排序规则

### 6.2 方案 B：多策略并行命中

做法：

- `dispatcher` 按用户加载全部启用策略
- 每条策略独立判断是否命中当前机会
- 每条命中策略独立创建一条数据库任务并投递节点流

优点：

- 直接符合“同一用户多策略并行”的需求
- `strategy_config_id` 与任务一一对应，事实边界清晰
- 控制规则、日志和后续效果分析都更容易按策略维度追踪

缺点：

- 幂等键、测试和任务计数会比单策略更复杂
- 需要明确多条策略互不影响的阻断语义

### 6.3 方案 C：只做任务挂接，不做策略匹配

做法：

- 给任务结构补上 `strategy_config_id`
- 但 `dispatcher` 仍不从数据库按策略做真正匹配

优点：

- 改动最小

缺点：

- 不算真正把 `strategy_configs` 接入 `dispatcher`
- 控制规则的策略维度仍然不会有效

## 7. 推荐方案

推荐采用 `方案 B：多策略并行命中`。

原因：

- 用户已明确同一用户的多条启用策略需要支持并行
- 现有 `ArbitrageTask.strategy_config_id` 正适合表达“一条任务属于一条策略”
- 该方案能最小范围打通策略真值、控制规则和任务真值三者关系

## 8. 核心设计

### 8.1 总体流程

`dispatcher` 的目标流程调整为：

1. 从公共机会流取到一条机会
2. 遍历目标用户
3. 为该用户加载启用中的策略配置
4. 对每条策略判断是否命中当前机会
5. 每条命中策略各自创建一条 `arbitrage_tasks`
6. 对该策略任务执行控制判断
7. 未被阻断则写入节点任务流并标记 `DISPATCHED`
8. 被阻断则把该策略任务标记为 `BLOCKED`

原则：

- “用户”不再是任务创建的唯一维度
- “用户 + 策略 + 机会”才是首版策略任务的最小事实单元

### 8.2 策略配置读取边界

本次新增 `StrategyConfigRepository`。

职责：

- 按 `user_id` 读取已启用的策略配置
- 只返回 `is_enabled=true` 的策略
- 按稳定顺序返回结果，便于测试和日志对齐

建议接口：

```python
class StrategyConfigRepository:
    def list_enabled_for_user(
        self,
        *,
        user_id: int,
        strategy_type: str = "spot_futures",
    ) -> list[StrategyConfig]:
        ...
```

说明：

- 首版只要求 `dispatcher` 读取策略，不要求提供增删改查
- `strategy_type` 先保留过滤位，默认只接当前主链使用的类型

### 8.3 策略命中规则

首版策略命中以“静态范围 + 阈值 + 启停”为主。

一条策略要进入候选，至少满足：

- `is_enabled=true`
- `strategy_type` 与当前运行链匹配
- `symbol_scope_json` 为空，或包含当前机会 `symbol`
- `exchange_scope_json` 为空，或同时覆盖当前机会的买卖交易所
- 当前机会 `spread_bps` 大于等于 `open_spread_bps_threshold`

首版约束：

- 空的 `symbol_scope_json` 视为“全符号”
- 空的 `exchange_scope_json` 视为“全交易所组合”
- `close_spread_bps_threshold` 本次只保留在模型中，不进入 `dispatcher` 开仓任务判断

### 8.4 任务创建语义

每条命中策略都创建一条独立任务：

- `user_id` = 当前目标用户
- `strategy_config_id` = 命中策略的主键
- `opportunity_id` = 当前流消息 ID
- `task_type` = `open`
- `target_notional` 默认取策略的 `target_quote_amount`

说明：

- 若机会 payload 已带 `target_quote_amount`，本次不再把它当成最终真值
- 当命中策略时，以 `strategy_config.target_quote_amount` 作为该任务的请求名义金额
- 若后续控制规则缩量，写入节点流的金额可以小于策略目标金额

### 8.5 控制规则接入

`dispatcher` 为每条策略任务做控制判断时，必须传入：

- `user_id`
- `symbol`
- `exchange`
- `requested_notional`
- `strategy_id = strategy_config.id`

语义：

- 控制规则对不同策略独立判定
- 某条策略被阻断，只阻断该条任务
- 同用户其他命中策略仍可继续创建和投递

这样才能让控制面中已存在的“按策略额度”规则真正生效。

### 8.6 节点任务流载荷

本次节点任务流在已强制携带 `task_uuid` 的基础上，建议再加入：

- `strategy_config_id`
- `env_mode`

原则：

- 不把完整策略配置下发到流里
- 只下发最小关联字段，保证任务与策略可追踪

### 8.7 幂等键

现有幂等键不能继续只用：

- `user_id`
- `opportunity_id`
- `task_type`

首版改为至少包含：

- `user_id`
- `opportunity_id`
- `task_type`
- `strategy_config_id`

建议格式：

```text
{user_id}:{opportunity_id}:{task_type}:{strategy_config_id}
```

这样可以避免：

- 同一用户同一机会下，多条策略并行时互相冲突

### 8.8 失败与阻断处理

对每条命中策略任务独立处理：

- 数据库创建成功、控制阻断：标记 `BLOCKED`
- 数据库创建成功、写节点流失败：标记 `FAILED`
- 节点流写入成功：标记 `DISPATCHED`

说明：

- 失败或阻断都只影响当前 `strategy_config_id` 对应任务
- 不能因为一条策略失败就放弃同用户其他命中策略

## 9. 边界与约束

### 9.1 本次不做的策略能力

首版不支持以下复杂行为：

- 多策略优先级排序
- 多策略互斥
- 策略间资金占用协调
- 基于历史仓位的动态策略禁用
- `close` 任务与平仓策略路由

### 9.2 与现有任务状态机的关系

本次不新增任务状态，仍沿用：

- `CREATED`
- `DISPATCHED`
- `EXECUTING`
- `SUCCEEDED`
- `FAILED`
- `BLOCKED`

变化只在于：

- 任务的创建粒度从“用户级”提升为“用户策略级”

### 9.3 与 executor 的关系

本次不要求 `executor` 再按 `strategy_config_id` 回查数据库完整策略配置。

`executor` 首版只需要：

- 继续消费带 `task_uuid` 的任务
- 若 payload 中存在 `strategy_config_id`，允许记录到日志或事件上下文
- 不改变当前执行入口和任务状态回写主路径

## 10. 测试与验证

本次应至少覆盖以下验证：

1. `StrategyConfigRepository` 能按用户返回启用策略
2. `dispatcher` 面对未命中策略时不创建任务
3. `dispatcher` 面对单用户多条命中策略时创建多条任务
4. 多条策略任务的 `strategy_config_id` 正确写入数据库
5. 控制判断能收到真实 `strategy_id`
6. 幂等键包含 `strategy_config_id`
7. 单条策略被阻断时，其它策略不受影响
8. 节点流 payload 包含 `task_uuid` 与 `strategy_config_id`

## 11. 迁移策略

本次采用增量接入：

1. 先新增策略读取仓储与对应测试
2. 再把 `dispatcher` 的任务创建从“按用户一条”调整为“按命中策略逐条”
3. 再补控制规则透传、幂等键和节点流字段
4. 最后做回归测试与远端联调

原则：

- 不推翻现有 `scanner -> dispatcher -> executor` 主链
- 不要求首版同时完成策略后台和远端运维入口

## 12. 成功标准

完成后，系统应达到以下结果：

- `dispatcher` 会从数据库真实读取用户启用策略
- 同一用户多条策略可对同一机会并行建任务
- 数据库中的每条策略任务都能追溯到明确的 `strategy_config_id`
- 控制规则的策略维度在 `dispatcher` 层开始真实生效
- 现有任务状态机与 executor 主链保持稳定
