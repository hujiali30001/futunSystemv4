# Executor Repair Planned 结构化事件设计

## 1. 文档目标

本文档定义如何把当前 executor 对 `OPEN_PARTIAL` 的 repair 摘要语义，从仅写入
`arbitrage_tasks.repair_action / repair_reason`，提升为一个可被后续 risk / repair
消费链直接使用的结构化运行时事件。

本次目标是在不引入真实 repair worker、不新增 repair 数据表、也不直接执行自动补单或
反向平仓的前提下，为 executor 增加一个专用的 `executor.repair_planned` 事件，用来
表达：

- 这次执行已经落到 `OPEN_PARTIAL`
- 风控层计划采取什么补救动作
- 补救动作针对哪些交易所或腿
- 后续 repair worker 应该从哪里接手

## 2. 范围

本次只做以下能力：

- 为 executor 增加 `executor.repair_planned` 事件
- 定义该事件的触发时机与 payload 字段
- 定义它与 `executor.execution_result`、任务摘要写回的职责关系
- 定义测试与验收标准

本次不做以下能力：

- 不新增 repair worker
- 不真正执行补单、反向平仓、自动减仓或自动全平
- 不新增 `risk_events` / `repair_events` 数据表
- 不改变当前 `arbitrage_tasks` execution summary 字段结构
- 不修改 `RiskManager.build_repair_plan(...)` 的返回模型
- 不新增 Feishu / 邮件外发规则

## 3. 背景与现状

当前 executor 主链已经具备以下基础：

- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py)
  中 `RedisExecutionTaskConsumer.run()` 已在拿到真实执行结果后：
  - 读取 `execution_status`
  - 读取 `filled_exchanges / failed_exchanges`
  - 调用 `RiskManager.build_repair_plan(...)`
  - 写入 `repair_action / repair_reason`
- [risk_manager.py](file:///d:/old/FuRunSystemV4/app/trading/risk_manager.py)
  已能把 `OPEN_PARTIAL` 映射到：
  - `action = AUTO_HEDGE_REPAIRING`
  - `reason = one_leg_failed`
- [runtime_events.py](file:///d:/old/FuRunSystemV4/app/runtime/runtime_events.py)
  已定义统一 `RuntimeEvent`
- [executor.execution_result` 事件设计](file:///d:/old/FuRunSystemV4/docs/superpowers/specs/2026-05-25-executor-execution-result-event-design.md)
  已建立“真实执行结果结构化暴露”的模式

但当前仍有一个关键缺口：

1. repair 目前只是任务表摘要字段，不是可消费的编排对象
2. 后续要接 repair worker 时，没有稳定的 runtime event 契约可复用
3. 当前远端与日志层看不到“这次计划修哪一边、为什么修”的结构化信息

因此，本次需要先把 repair 从“摘要字段”提升为“编排事件”。

## 4. 问题定义

如果继续保持现状，会有以下问题：

1. `OPEN_PARTIAL` 只能在数据库任务行里看到 repair 摘要，运行时链路无法直接消费
2. 后续 repair worker 若要接入，只能重复推断 `repair_action / repair_reason`
3. 远端联调与故障排查时，无法直接从事件面回答“这次计划怎么补救”
4. repair 仍停留在描述性字段，尚未成为真正的编排层

因此，本次需要先为 repair 计划增加一个结构化事件契约。

## 5. 设计目标

本次设计满足以下目标：

1. `OPEN_PARTIAL` 的 repair 语义可被结构化事件稳定暴露
2. 任务摘要写回继续保留，但不再是 repair 语义的唯一出口
3. 后续 repair worker 可以直接消费本次事件，不需要重复设计输入契约
4. 不改变现有 execution summary 与 `executor.execution_result` 的行为

## 6. 方案比较

### 6.1 方案 A：新增独立 `executor.repair_planned` 事件

做法：

- 当 `execution_status == OPEN_PARTIAL`
- 且 `RiskManager.build_repair_plan(...)` 返回需要补救的动作时
- 发出单独的 `executor.repair_planned`

优点：

- repair 编排与执行结果语义清晰分层
- 最适合作为后续 repair worker 的消费面
- 与现有 `executor.execution_result` 模式一致

缺点：

- 会新增一类事件

### 6.2 方案 B：把 repair 信息塞进 `executor.execution_result`

做法：

- 不新增事件
- 直接把 repair_action / repair_reason / target_exchanges 塞入现有 execution_result

优点：

- 改动最少

缺点：

- 会把“执行事实”和“补救编排”两层语义混在一起
- 后续 repair worker 消费边界不清晰

### 6.3 方案 C：只继续写任务表摘要，不加事件

做法：

- 保持现状

优点：

- 零新增结构

缺点：

- 无法推进 repair 编排层
- 后续 still 需要返工

### 6.4 推荐方案

本次采用方案 A。

原因：

- 它最符合“小步把 repair 从摘要提升为可消费对象”的目标
- 它不会立刻扩大到新表或真实自动补救执行
- 它为后续接 repair worker 保留了最自然的升级路径

## 7. 核心设计

### 7.1 新事件类型

本次新增一个固定事件类型：

- `executor.repair_planned`

该事件只在以下条件同时满足时发出：

1. executor 拿到真实执行结果
2. `execution_status == "OPEN_PARTIAL"`
3. `RiskManager.build_repair_plan(...)` 返回的 action 不为 `NONE`

这意味着：

- `OPEN_HEDGED` 不发
- preflight 失败不发
- account truth 失败不发
- dispatch 前异常不发
- summary-only 成功对象不发

### 7.2 与现有事件的职责关系

本次明确分层如下：

- `executor.execution_result`
  - 表达真实执行事实
- `executor.repair_planned`
  - 表达基于执行事实推导出的 repair 编排结果
- `executor.task.processed / failed`
  - 表达 worker 生命周期处理结论

这样：

- 执行事实与补救计划分层
- repair worker 后续只需订阅 `executor.repair_planned`

### 7.3 事件基础字段

`RuntimeEvent` 基础字段建议如下：

- `event_type = "executor.repair_planned"`
- `level = "INFO"`
- `service = "executor"`
- `region = self.region`
- `symbol = payload.get("symbol")`
- `exchange = payload.get("buy_exchange")`
- `exchanges = [payload["buy_exchange"], payload["sell_exchange"]]`
- `message = "executor repair planned"`

说明：

- `exchange` 仍沿用当前事件主展示习惯，使用买腿交易所
- `exchanges` 保留买卖两边，便于后续 repair worker 完整理解上下文

### 7.4 事件 payload 字段

首版 payload 固定包含以下字段：

- `task_uuid`
- `user_id`
- `symbol`
- `buy_exchange`
- `sell_exchange`
- `execution_status`
- `filled_exchanges`
- `failed_exchanges`
- `repair_action`
- `repair_reason`
- `target_exchanges`

字段约束如下：

- `execution_status`
  - 固定为 `OPEN_PARTIAL`
- `filled_exchanges`
  - 若缺失则降级为空数组
- `failed_exchanges`
  - 若缺失则降级为空数组
- `repair_action`
  - 直接来自 `RiskManager.build_repair_plan(...)`
- `repair_reason`
  - 直接来自 `RiskManager.build_repair_plan(...)`
- `target_exchanges`
  - 首版固定等于 `failed_exchanges`

### 7.5 `target_exchanges` 规则

首版不做更复杂的修复目标推导。

明确规则：

- `target_exchanges = failed_exchanges`

原因：

- 当前最小 repair 语义就是“哪边没成功，就先把哪边作为补救目标”
- 先不在这轮引入：
  - 反向平仓目标推导
  - 裸露腿方向推导
  - 多阶段恢复优先级链

后续如果引入真实 repair worker，再扩展更复杂的目标选择逻辑。

### 7.6 触发时机

在 [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py)
中，建议触发顺序如下：

1. `dispatcher.dispatch(...)` 返回真实执行结果
2. executor 读取 `execution_status`
3. 若 `execution_status` 存在：
   - 计算 `filled_exchanges / failed_exchanges`
   - 调用 `RiskManager.build_repair_plan(...)`
4. 若 `execution_status == OPEN_PARTIAL` 且 `repair_action != NONE`
   - 发 `executor.repair_planned`
5. 再继续现有：
   - 任务摘要写回
   - `executor.execution_result`
   - `executor.task.processed / failed`

说明：

- `executor.repair_planned` 与 `executor.execution_result` 一样，只依赖真实执行结果和 repair 计划
- 它不依赖数据库写回成功
- 这样即使后续任务摘要写回失败，repair 编排事件仍能进入结构化日志

### 7.7 内部实现边界

本次建议在 `RedisExecutionTaskConsumer` 内增加一个专用 builder，例如：

- `_build_executor_repair_planned_event(...)`

推荐职责：

- 从 `payload`、`execution_status`、`filled_exchanges`、`failed_exchanges`、`repair_plan` 中构造事件
- 统一处理数组空值与基础字段提取
- 返回 `RuntimeEvent`

不建议把 repair 事件 payload 组装逻辑散落在主循环中，原因是：

- 与 `executor.execution_result` 一样，字段一旦稳定就会成为后续消费契约
- 单独 builder 更容易做 focused test

### 7.8 与任务摘要写回的关系

本次不改变现有任务摘要写回逻辑：

- `repair_action`
- `repair_reason`

仍继续写入 `arbitrage_tasks`

这意味着首版会同时保留两层输出：

1. 数据库任务摘要
2. runtime repair 编排事件

这样可以：

- 保持当前下游兼容
- 给后续 repair worker 提供结构化消费面

### 7.9 与 AlertRouter 的关系

本次不修改 [alerting.py](file:///d:/old/FuRunSystemV4/app/runtime/alerting.py) 的外发规则。

原因：

- 新事件是 `INFO`
- 它应该默认只进入结构化日志
- 首版不增加飞书或邮件噪音

因此：

- 可以先把 repair 编排提升到事件层
- 不会立即增加现网外发告警数量

## 8. 数据流

目标数据流如下：

1. executor 消费节点任务
2. preflight / account truth / control guard 正常通过
3. `dispatcher.dispatch(...)` 返回真实执行结果
4. 若 `execution_status == OPEN_PARTIAL`
   - executor 生成 `repair_plan`
   - 发 `executor.repair_planned`
5. executor 继续：
   - 写 execution summary
   - 发 `executor.execution_result`
   - 发 `executor.task.failed`

最终效果是：

- `OPEN_PARTIAL` 不再只是任务表摘要
- repair 编排首次变成独立 runtime 对象

## 9. 错误处理

错误处理规则如下：

- preflight / account truth / dispatch 前失败：
  - 不发 `executor.repair_planned`
- `execution_status != OPEN_PARTIAL`：
  - 不发 `executor.repair_planned`
- `failed_exchanges` 为空：
  - 首版不发 `executor.repair_planned`
- `repair_plan.action == NONE`：
  - 不发 `executor.repair_planned`
- 事件发出后若任务摘要写回失败：
  - 首版不新增补偿
  - 仍沿用当前异常上抛模型

## 10. 测试策略

本次至少补以下测试：

### 10.1 `tests/test_live_workers.py`

- 当结果为 `OPEN_PARTIAL` 时：
  - 发出 `executor.repair_planned`
  - 断言 payload 中：
    - `execution_status = OPEN_PARTIAL`
    - `repair_action = AUTO_HEDGE_REPAIRING`
    - `repair_reason = one_leg_failed`
    - `target_exchanges == failed_exchanges`
- 当结果为 `OPEN_HEDGED` 时：
  - 不发 `executor.repair_planned`
- preflight 失败时：
  - 不发 `executor.repair_planned`
- `executor.execution_result` 现有断言不回归
- execution summary 写回现有断言不回归

### 10.2 聚合回归

- `executor.task.processed / failed` 现有断言不回归
- `RiskManager` 现有测试不需要改动行为
- AlertRouter 不需要新增外发测试，只需保证新 `INFO` 事件不影响现有路径

## 11. 验收标准

满足以下条件即可视为完成：

1. executor 新增 `executor.repair_planned` 事件
2. 事件只在 `OPEN_PARTIAL` 且存在 repair 动作时发出
3. event payload 能稳定承载：
   - `execution_status`
   - `filled_exchanges`
   - `failed_exchanges`
   - `repair_action`
   - `repair_reason`
   - `target_exchanges`
4. `OPEN_HEDGED` / preflight / 非真实执行失败路径不误发该事件
5. execution summary 写回与 `executor.execution_result` 不回归
6. 相关测试通过

## 12. 后续演进

本次完成后，后续可以继续沿以下方向推进，但不属于本次范围：

- 新增 repair worker 消费 `executor.repair_planned`
- 把 repair 编排同步落到 `risk_events` 或专用 repair 表
- 扩展 `target_exchanges` 为更复杂的补救目标推导
- 接入真实自动补单、反向平仓、自动减仓或自动全平链路
