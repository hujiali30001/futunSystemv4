# 套利运行时可观测性设计

## 1. 文档目标

本文档定义 `B1-5A` 的目标：让套利链拥有和当前 `spot` 链同等级的运行事件与告警可见性。

本次覆盖：

- `arb_dispatcher`
- `arb_executor`
- 套利最小 repair 路径
- `alerting.py` 中的中文标题与飞书文本模板

本轮不做：

- 不改业务状态机
- 不扩 metrics / audit / trace
- 不重做 repair 生命周期

## 2. 范围

本次只做以下能力：

- 为套利调度、执行、repair 补齐独立事件命名空间
- 为套利链关键事件补齐最小 payload 结构
- 为套利链事件定义日志/飞书分层
- 在 `alerting.py` 中补齐中文标题与飞书文本模板
- 增补 focused tests，确保 spot 链既有事件不回归

本次不做以下能力：

- 不引入新的监控指标系统
- 不新增审计日志存储
- 不新增 tracing/span 体系
- 不改变套利任务调度与执行行为
- 不改变现有飞书去重总策略

## 3. 背景与现状

当前代码库已经具备一套较完整的 spot 运行时事件骨架：

- `RuntimeEvent`
- `AlertRouter`
- `StructuredEventLogger`
- `worker.started / worker.start_failed / worker.stopped`
- `executor.execution_result`
- `executor.repair_planned`
- `repair.task.finished`

这意味着当前系统已经能对以下链路做结构化日志与告警：

- worker 生命周期
- spot 执行结果
- spot repair 计划
- spot repair 完成

但套利链当前仍明显不对称：

- `RedisArbitrageTaskDispatcher` 没有同等级的调度事件
- `ArbitrageExecutionTaskConsumer` 没有独立执行事件
- 套利最小 repair 路径只改库，不发显式事件
- `alerting.py` 对套利事件没有中文标题与专用文本模板

因此 `B1-5A` 的价值，不是补新功能，而是把套利链从“能跑但不透明”，补成“能跑且可排障”。

## 4. 问题定义

如果不补这一步，系统会继续存在四个问题：

### 4.1 套利调度链不可见

当前套利任务在 `arb_dispatcher` 阶段会发生：

- 用户命中
- 跳过
- 创建任务

但这些关键决策没有结构化事件输出。

结果是：

- 只能看数据库最终状态
- 很难回答“为什么这个机会没变成任务”

### 4.2 套利执行链不可见

当前套利执行链虽然已经具备：

- `OPEN`
- `CLOSE`
- 最小 repair 兼容

但执行结果和 repair 计划没有独立事件。

结果是：

- 远端运行时很难判断任务是成功、部分成功还是失败
- spot 链有事件，套利链没有同级别可见性

### 4.3 套利 repair 只改库不发事件

当前最小套利 repair 成功或失败，只会体现在任务状态变化里。

这意味着：

- 人和系统都很难在运行时直接观察 repair 是否发生
- `manual_required` 也缺少更直观的事件出口

### 4.4 若所有事件都直发飞书，会造成噪音

如果一上来把：

- user discovered
- task created
- task skipped
- execution result
- repair planned

全部推送飞书，实际结果只会是告警噪音爆炸。

因此本轮必须同时解决：

- 事件补齐
- 告警分层

## 5. 设计目标

本次设计满足以下目标：

1. 给套利链建立独立、明确的事件命名空间
2. 补齐 dispatcher / executor / repair 的关键结构化事件
3. 只把真正需要人工关注的失败事件送飞书
4. 保持 spot 链事件与告警行为不回归

## 6. 方案比较

### 6.1 方案 A：补全套利链事件壳与告警模板，推荐

做法：

- 新增 `arb.*` 事件命名空间
- 调度、执行、repair 各自补齐关键事件
- `alerting.py` 只对真正的失败事件走飞书

优点：

- 全链可见
- 语义清晰
- 不改业务闭环

缺点：

- 需要补一批新测试和模板映射

### 6.2 方案 B：复用现有 `executor.* / repair.*` 事件名

做法：

- 套利链直接沿用 spot 链事件名

优点：

- 改动更少

缺点：

- spot 与 arbitrage 语义混淆
- 后续排障与过滤不够清晰

### 6.3 方案 C：顺手把 metrics / audit / trace 一次补齐

做法：

- 把可观测性体系一次性补完整

优点：

- 长期形态更完整

缺点：

- 明显超出本轮短闭环范围

### 6.4 推荐方案

本次采用方案 A。

原因：

- 它满足你要求的“全链都补”
- 但仍收敛在事件与告警层，不去顺手重写业务链
- 最符合当前短闭环推进节奏

## 7. 核心设计

### 7.1 事件命名空间

本次新增独立套利事件命名空间，而不是复用现有 spot 事件名。

推荐事件名如下：

- `arb.dispatcher.user_discovered`
- `arb.dispatcher.task_created`
- `arb.dispatcher.task_skipped`
- `arb.executor.execution_result`
- `arb.executor.repair_planned`
- `arb.executor.task_failed`
- `arb.repair.finished`

这样做的好处是：

- spot 与 arbitrage 一眼可分
- 日志筛选更清楚
- 飞书过滤更容易

### 7.2 Dispatcher 事件

本次为套利调度链补三类事件：

- `arb.dispatcher.user_discovered`
- `arb.dispatcher.task_created`
- `arb.dispatcher.task_skipped`

#### `arb.dispatcher.user_discovered`

最小字段：

- `user_id`
- `symbol`
- `opportunity_type`
- `spot_exchange`
- `derivative_exchange`
- `source_message_id`

语义：

- 某条套利机会进入某个用户的候选调度判断范围

默认级别：

- `INFO`

默认只进结构化日志，不进飞书。

#### `arb.dispatcher.task_created`

最小字段：

- `task_uuid`
- `user_id`
- `strategy_config_id`
- `symbol`
- `opportunity_type`
- `spot_exchange`
- `derivative_exchange`
- `worker_node_id`

语义：

- 某条套利机会已形成真实调度任务

默认级别：

- `INFO`

默认只进结构化日志，不进飞书。

#### `arb.dispatcher.task_skipped`

最小字段：

- `user_id`
- `symbol`
- `opportunity_type`
- `skip_reason`
- `source_message_id`

`skip_reason` 至少支持：

- `threshold_not_matched`
- `account_coverage_missing`
- `close_context_missing`
- `route_unavailable`

默认级别：

- `INFO`

默认只进结构化日志，不进飞书。

### 7.3 Executor 事件

本次为套利执行链补三类事件：

- `arb.executor.execution_result`
- `arb.executor.repair_planned`
- `arb.executor.task_failed`

#### `arb.executor.execution_result`

最小字段：

- `task_uuid`
- `user_id`
- `symbol`
- `task_type`
- `spot_exchange`
- `derivative_exchange`
- `execution_status`
- `filled_exchanges`
- `failed_exchanges`

语义：

- 套利任务已经完成一次执行尝试

默认级别：

- `INFO`

默认只进结构化日志，不进飞书。

#### `arb.executor.repair_planned`

最小字段：

- `task_uuid`
- `symbol`
- `task_type`
- `execution_status`
- `repair_action`
- `repair_reason`
- `target_exchanges`

语义：

- 套利任务执行后已命中最小 repair 兼容路径

默认级别：

- `INFO`

默认只进结构化日志，不进飞书。

#### `arb.executor.task_failed`

最小字段：

- `task_uuid`
- `user_id`
- `symbol`
- `task_type`
- `error`
- `failed_exchanges`

语义：

- 套利任务执行失败，且不进入 repair
- 或执行阶段发生明确异常

默认级别：

- `ERROR`

这是本轮建议进入飞书的套利执行失败事件之一。

### 7.4 Repair 事件

本次为套利最小 repair 路径新增：

- `arb.repair.finished`

最小字段：

- `task_uuid`
- `symbol`
- `task_type`
- `status`
- `repaired_exchanges`
- `remaining_failed_exchanges`
- `reason`

默认级别：

- repair 成功：`INFO`
- repair 失败：`ERROR`

默认告警策略：

- 成功只进结构化日志
- 失败进入飞书

### 7.5 日志 / 飞书分层

本次不把所有套利事件都推飞书，而是做分层：

#### 只进结构化日志

- `arb.dispatcher.user_discovered`
- `arb.dispatcher.task_created`
- `arb.dispatcher.task_skipped`
- `arb.executor.execution_result`
- `arb.executor.repair_planned`
- `arb.repair.finished` 成功事件

#### 进入飞书

- `arb.executor.task_failed`
- `arb.repair.finished` 失败事件

这样做的原因是：

- 保留完整运行痕迹
- 但不制造飞书噪音

### 7.6 中文标题与飞书文本

`alerting.py` 中至少补以下中文标题：

- `套利用户命中`
- `套利任务已创建`
- `套利任务已跳过`
- `套利执行结果`
- `套利修复已计划`
- `套利任务失败`
- `套利修复完成`

飞书文本需重点展示：

- 服务
- 交易对
- 任务类型
- 现货交易所
- 衍生品交易所
- 失败原因 / repair 结果

本轮不要求给所有 `INFO` 事件做复杂富文本模板，只要求：

- 失败事件模板清楚
- 其余事件标题映射完整

### 7.7 与现有 spot 事件的关系

本次明确：

- 不修改 `executor.execution_result`
- 不修改 `executor.repair_planned`
- 不修改 `repair.task.finished`
- 不改变 spot 链现有事件级别与飞书行为

套利链新增的是一套并行事件，而不是替换 spot 链。

## 8. 数据流

本次目标数据流如下：

1. `arb_dispatcher` 发现用户
2. 派发：
   - `arb.dispatcher.user_discovered`
3. 若创建任务：
   - 派发 `arb.dispatcher.task_created`
4. 若跳过：
   - 派发 `arb.dispatcher.task_skipped`
5. `arb_executor` 执行任务
6. 执行后：
   - 派发 `arb.executor.execution_result`
7. 若命中最小 repair：
   - 派发 `arb.executor.repair_planned`
8. repair 完成后：
   - 派发 `arb.repair.finished`
9. 若执行直接失败且不 repair：
   - 派发 `arb.executor.task_failed`
10. `AlertRouter` 根据级别决定：
   - 全量写结构化日志
   - 失败类进入飞书

## 9. 错误处理

### 9.1 事件派发失败不阻断主链

若 `event_router.dispatch(...)` 失败：

- 不应中断套利调度或执行主链
- 主链应继续以业务结果为准

本轮推荐按现有系统风格处理：

- 事件尽力派发
- 不让日志/告警通道反向打断业务

### 9.2 失败事件必须稳定产生

对以下场景，必须稳定发出失败类事件：

- 执行明确失败
- repair 最终失败

否则飞书与日志会遗漏人工关注点。

### 9.3 INFO 事件不应升格为 ERROR

如：

- task created
- task skipped
- execution result
- repair planned

这些默认保持 `INFO`，不因“更完整可见”而统一升格。

## 10. 测试策略

本次至少补以下 focused tests：

### 10.1 Dispatcher 事件

- `arb.dispatcher.user_discovered` 被正确派发
- `arb.dispatcher.task_created` 被正确派发
- `arb.dispatcher.task_skipped` 在关键跳过路径被正确派发

### 10.2 Executor 事件

- `arb.executor.execution_result` 在成功和失败路径都可派发
- `arb.executor.repair_planned` 在 `OPEN_PARTIAL` repairable 路径派发
- `arb.executor.task_failed` 在不可 repair 失败路径派发

### 10.3 Repair 事件

- `arb.repair.finished` 在 repair 成功时派发 `INFO`
- `arb.repair.finished` 在 repair 失败时派发 `ERROR`

### 10.4 Alerting 模板

- 中文标题映射覆盖套利链事件
- 失败事件飞书文本含关键字段
- `INFO` 类套利事件默认不触发飞书

### 10.5 并存回归

- 旧 spot 链事件测试不回归
- `worker.started / worker.stopped / worker.start_failed` 不回归

## 11. 验收标准

满足以下条件即可视为本次完成：

1. 套利链拥有独立的 `arb.*` 事件命名空间
2. dispatcher / executor / repair 关键事件都能写入结构化日志
3. 套利失败事件能进入飞书
4. spot 链既有事件与告警行为不回归

## 12. 后续演进

本次完成后，后续可以继续推进，但不属于本次范围：

- 接入 metrics
- 接入 audit 日志
- 接入 tracing
- 更细粒度的套利告警聚合与抑制
- 更完整的套利 repair 生命周期可观测性
