# 套利任务原子领取安全设计

## 1. 文档目标

本文档定义 `B1-4` 的目标：让 `arb_executor` 在多 worker 或重复启动场景下，不会重复领取同一条套利任务。

本次目标不是扩功能，而是补执行安全底座。

本轮完成标准是：

- 可执行套利任务只能被一个 worker 成功 claim
- claim 成功后任务立即进入 `RUNNING`
- 已进入终态或运行态的任务不能再被重复领取
- 旧 `spot` 主链不回归

## 2. 范围

本次只做以下能力：

- 新增原子 claim 接口
- 为套利任务补最小状态机约束
- 将 `arb_executor` 改为只通过 claim 领取任务
- 增补 focused tests，验证重复领取窗口被收紧

本次不做以下能力：

- 不改成 Redis consumer group
- 不引入任务租约超时回收
- 不引入重试计数与重试调度
- 不扩套利告警与观测主线
- 不扩更强的套利 repair 生命周期

## 3. 背景与现状

`B1-3` 已经形成：

- `ArbitrageExecutionAdapter`
- `ArbitrageExecutionTaskConsumer`
- `arb_executor` worker role

也就是说，当前系统已经能：

- 从数据库里找套利任务
- 执行 `OPEN` / `CLOSE`
- 回写结果
- 在最小范围内兼容 repair

但当前领取路径仍是分离式的：

1. `list_executable_tasks(limit=1)`
2. `mark_executing(...)`

这是一个明显的竞争窗口。

如果两个 worker 同时查到同一条任务，就可能：

- 都认为自己拿到了任务
- 都进入真实执行

这会导致重复下单、状态污染、repair 误触发，以及后续收口混乱。

## 4. 问题定义

当前主要问题有三个：

### 4.1 领取和状态切换不是原子动作

当前是：

- 先查
- 再改状态

这不是一个不可分割的领取动作。

### 4.2 任务状态机约束太弱

当前仓储层虽然有：

- `mark_dispatched`
- `mark_executing`
- `mark_succeeded`
- `mark_failed`

但没有明确约束：

- 哪些状态可以再次进入 `RUNNING`
- 哪些状态绝对不能再被领取

### 4.3 `arb_executor` 仍然暴露重复领取窗口

当前 `ArbitrageExecutionTaskConsumer` 自己做：

- 查询任务
- 标记执行中

如果不把这两步收口成一条 claim 语义，worker 数量一多就会变成实际风险。

## 5. 设计目标

本次设计满足以下目标：

1. 提供一个真正的原子 claim 边界
2. 让 `arb_executor` 不再自己拼装“查 + 改状态”流程
3. 对最危险的状态反向流转加最小保护
4. 在不改动 spot 主链的前提下完成收口

## 6. 方案比较

### 6.1 方案 A：仓储层新增原子 `claim_next_executable_task()`，推荐

做法：

- 在 `TaskRepository` 中新增 claim 接口
- 把“选择任务 + 改成 RUNNING + 写 worker_node_id + 写 started_at”收成一个动作
- `ArbitrageExecutionTaskConsumer` 只调用 claim

优点：

- 边界最清楚
- 改动最小
- 最适合当前仓储层架构

缺点：

- 仍然没有租约回收与重试机制

### 6.2 方案 B：继续用 `list_executable_tasks()`，在应用层做二次检查

做法：

- 查完后再验证状态

优点：

- 表面上改动少

缺点：

- 竞争窗口依旧存在
- 只能降低风险，不能真正收口

### 6.3 方案 C：直接切到 Redis consumer group

做法：

- 彻底改变套利执行入口

优点：

- 长期更完整

缺点：

- 明显超出本轮范围
- 会把执行通道架构一起重做

### 6.4 推荐方案

本次采用方案 A。

原因：

- 它能最大限度收紧重复领取风险
- 不需要重构整个执行入口
- 最符合“先补安全底座，再扩运维与功能”的节奏

## 7. 核心设计

### 7.1 新增原子 claim 接口

本次新增：

- `claim_next_executable_task(worker_node_id, env_mode)`

语义如下：

- 从当前可执行套利任务中选一条
- 原子地把它切换为 `RUNNING`
- 写入：
  - `worker_node_id`
  - `started_at`
- 返回已成功 claim 的任务

如果没有可执行任务，则：

- 返回 `None`

### 7.2 claim 命中范围

claim 只允许命中以下状态：

- `CREATED`
- `DISPATCHED`

claim 不允许命中：

- `RUNNING`
- `SUCCEEDED`
- `FAILED`
- `BLOCKED`

也就是说，claim 本身就是一次状态迁移，而不是“只读查询”。

### 7.3 claim 后状态

claim 成功后，任务立刻变为：

- `RUNNING`

并且同时写入：

- `worker_node_id`
- `started_at`

调用方不需要再额外执行：

- `mark_executing()`

### 7.4 `arb_executor` 领取方式变更

`ArbitrageExecutionTaskConsumer` 本次改为：

- 不再自己调用 `list_executable_tasks()`
- 不再自己调用 `mark_executing()`
- 只调用 `claim_next_executable_task()`

这样做的价值是：

- 领取语义被收进仓储层
- consumer 不再自己管理竞争窗口

### 7.5 最小状态机保护

本次不做完整状态机框架，但至少补以下保护：

1. 终态任务不能再次进入 `RUNNING`
2. 已经 `RUNNING` 的任务不能再次 claim
3. claim 只能由：
   - `CREATED`
   - `DISPATCHED`
   进入 `RUNNING`

本轮不要求为所有 `mark_*` 方法都补前置状态校验，但 claim 路径必须是安全的。

### 7.6 选择顺序

claim 命中的任务顺序，继续保持最小语义：

- 按主键或创建顺序，从最早的可执行任务开始

本次不引入：

- 优先级队列
- 重试优先级
- 用户级公平调度

### 7.7 Spot 主链不变

本次明确不改：

- `RedisExecutionTaskConsumer`
- `RedisNodeTaskDispatcher`
- `stream:spot_exec_tasks:*`
- `spot` 执行主链

本轮只修套利任务的数据库领取安全问题。

## 8. 数据流

本次目标数据流如下：

1. `arb_executor` 进入轮询
2. 调用 `claim_next_executable_task(worker_node_id, env_mode)`
3. 若无任务：
   - 返回 `None`
   - worker 休眠等待
4. 若 claim 成功：
   - 任务已被切换为 `RUNNING`
   - worker 获得这条任务
5. worker 执行：
   - `ArbitrageExecutionAdapter`
   - 执行结果回写
   - 必要时最小 repair

## 9. 错误处理

### 9.1 没有可执行任务

若没有可执行任务：

- claim 返回 `None`
- 这不是错误

### 9.2 claim 竞争失败

若某条任务在读取候选后已被其他 worker 先一步 claim：

- 当前 worker 必须拿不到这条任务
- 并继续尝试下一条或返回 `None`

这条语义必须由 claim 实现保证，而不是交给 consumer 猜测。

### 9.3 非法状态回流

若任务已经是：

- `RUNNING`
- `SUCCEEDED`
- `FAILED`
- `BLOCKED`

则 claim 不得返回这条任务。

### 9.4 claim 不影响旧链

套利 claim 改造不得影响：

- spot dispatcher
- spot executor
- repair worker

## 10. 测试策略

本次至少补以下 focused tests：

### 10.1 claim 基础语义

- `CREATED` 任务可被 claim
- `DISPATCHED` 任务可被 claim
- claim 后状态变为 `RUNNING`
- `worker_node_id` 与 `started_at` 被写入

### 10.2 重复领取保护

- 同一条任务第一次 claim 成功
- 第二次 claim 不能再次拿到同一任务
- 已是 `RUNNING` 的任务不会再被 claim

### 10.3 终态保护

- `SUCCEEDED`
- `FAILED`
- `BLOCKED`

这些任务都不会再被 claim

### 10.4 consumer 改造回归

- `ArbitrageExecutionTaskConsumer` 改走 claim 后行为仍然正确
- 旧 spot executor 相关测试不回归

## 11. 验收标准

满足以下条件即可视为本次完成：

1. 存在 `claim_next_executable_task()` 这类原子领取边界
2. `arb_executor` 只通过 claim 获取任务
3. 同一条套利任务不会被重复 claim
4. 终态与运行态任务不会再次进入执行
5. 旧 `spot` 主链不回归

## 12. 后续演进

本次完成后，后续可以继续推进，但不属于本次范围：

- 租约超时回收
- 执行重试机制
- Redis consumer group 化
- 更强的套利告警与观测
- 更强的套利 repair 生命周期
