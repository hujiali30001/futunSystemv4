# Executor 真实执行结果结构化事件设计

## 1. 文档目标

本文档定义如何把 `spot_arbitrage_probe` 已经具备的 richer result，通过 executor
结构化事件稳定暴露出来，让远端联调、运行日志和后续 repair 消费链不再只能看到
“任务处理成功/失败”，还能看到“真实执行到底发生了什么”。

本次目标是在不扩展 `arbitrage_tasks` 字段、不改写现有 execution summary 持久化、
也不引入新的 repair 自动执行编排的前提下，为 executor 增加一个专用
`executor.execution_result` 事件，用来承载：

- `execution_status`
- 成功交易所列表
- 失败交易所列表
- 买腿 / 卖腿阶段状态
- 稳定错误码与失败阶段

这样运行时链路就可以直接回答：

- 这次真实执行是 `OPEN_HEDGED` 还是 `OPEN_PARTIAL`
- 哪一腿失败
- 失败发生在 `create`、`cancel` 还是 `fetch_final`
- 现有 `executor.task.processed / failed` 到底对应的是 worker 处理结论，还是交易执行细节

## 2. 范围

本次只做以下能力：

- 为 executor 增加独立的真实执行结果事件
- 定义事件的触发时机与 payload 字段
- 定义 richer result 与旧执行结果对象的兼容读取方式
- 定义该事件与现有 `executor.task.processed / failed` 的职责分层
- 定义测试与验收标准

本次不做以下能力：

- 不扩展 `arbitrage_tasks` 持久化字段
- 不把所有 richer result 字段回写数据库
- 不改变当前 execution summary 写回逻辑
- 不修改 `TradeExecutor.ExecutionResult` 模型
- 不新增 Feishu / 邮件外发规则
- 不直接接入 repair 自动执行链

## 3. 背景与现状

当前系统已经具备以下基础：

- [spot_arbitrage_probe.py](file:///d:/old/FuRunSystemV4/app/runtime/spot_arbitrage_probe.py)
  中 `SpotArbitrageTaskResult` 已包含：
  - `execution_status`
  - `filled_exchanges`
  - `failed_exchanges`
  - `buy_leg_status`
  - `sell_leg_status`
  - `buy_leg_error_code`
  - `sell_leg_error_code`
  - `buy_leg_error_detail`
  - `sell_leg_error_detail`
  - `failed_stage`
- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py)
  中 `RedisExecutionTaskConsumer` 已能：
  - 消费真实执行结果
  - 按 `execution_status / filled_exchanges / failed_exchanges` 写 execution summary
  - 发出 `executor.task.processed` 与 `executor.task.failed`
- [runtime_events.py](file:///d:/old/FuRunSystemV4/app/runtime/runtime_events.py)
  已定义统一 `RuntimeEvent`
- [alerting.py](file:///d:/old/FuRunSystemV4/app/runtime/alerting.py)
  已对特定 `INFO`、`ERROR`、`CRITICAL` 事件做结构化日志与外发处理

但当前仍有明显缺口：

1. richer result 只停留在 `spot_arbitrage_probe` 返回对象里，没有进入 executor 的结构化事件面
2. 远端日志目前只能看到 `executor.task.processed / failed`，缺乏真实执行上下文
3. `executor.task.failed` 目前更多表达 worker 失败结论，而不是详细执行阶段信息
4. 后续如果要把 richer result 继续接到 repair 或排障工具，没有统一事件落点

因此，本次需要先把 richer result 暴露到 executor 结构化事件层。

## 4. 问题定义

如果继续保持现状，会有以下问题：

1. richer result 虽然存在，但只能靠调试或专门脚本读取返回对象，不能在正常运行日志里观察
2. `executor.task.processed / failed` 会混合“worker 是否完成处理”和“真实执行阶段细节”两种语义
3. 后续若要接 repair、报表或排障工具，没有稳定的事件契约可复用
4. 主服务器远端验证虽然已经能跑 richer result helper，但生产链日志仍无法直接给出腿级上下文

因此，本次需要为真实执行结果增加一个职责独立、字段稳定的事件。

## 5. 设计目标

本次设计满足以下目标：

1. executor 为真实执行结果提供稳定、独立的结构化事件
2. `executor.task.processed / failed` 继续表达 worker 处理结论，不承担所有执行细节
3. richer result 新字段优先进入日志与事件面，而不是立即扩大数据库范围
4. 旧的 summary-only 结果对象仍然能被兼容消费
5. 不改变现有 AlertRouter 的外发行为

## 6. 方案比较

### 6.1 方案 A：新增独立 `executor.execution_result` 事件

做法：

- 保留现有 `executor.task.processed / failed`
- 新增单独的 `executor.execution_result`
- 专门承载 richer result 与 execution summary 相关字段

优点：

- 职责边界清晰
- 不污染已有 processed/failed 语义
- 最适合作为后续 repair 与运维工具的统一消费面

缺点：

- 会多出一类事件
- 需要补一层 builder 和测试

### 6.2 方案 B：直接扩充 `executor.task.processed / failed`

做法：

- 把 richer result 字段直接塞进现有事件 `payload`

优点：

- 改动最小

缺点：

- 处理结论与执行细节语义混杂
- 未来字段继续增加时，现有事件会越来越难理解

### 6.3 方案 C：同时发摘要事件和详情事件

做法：

- 保留现有 processed/failed
- 额外再拆一个 detail event
- 让摘要和详情分别落不同事件

优点：

- 结构最完整

缺点：

- 日志量更大
- 对当前阶段来说设计过重

### 6.4 推荐方案

本次采用方案 A。

原因：

- 它最符合当前“小步把 richer result 接到可观测性层”的目标
- 它不会影响现有 execution summary 写回
- 它为后续接 repair、持久化或报表保留了最自然的升级路径

## 7. 核心设计

### 7.1 新事件类型

本次新增一个固定事件类型：

- `executor.execution_result`

该事件只在 executor 拿到真实执行结果，且结果对象中存在
`execution_status` 时发出。

这意味着：

- `OPEN_HEDGED` 会发
- `OPEN_PARTIAL` 会发
- preflight 失败不会发
- account truth 失败不会发
- dispatch 前异常不会发
- 普通 `{"ok": True}` 但没有 `execution_status` 的兼容旧对象不会发

### 7.2 事件职责分层

本次明确分层如下：

- `executor.task.processed`
  - 表达 executor worker 对这条消息处理完成
- `executor.task.failed`
  - 表达 executor worker 在处理这条消息时失败
- `executor.execution_result`
  - 表达真实执行结果本身

这样：

- `task.processed / failed` 负责 worker 生命周期语义
- `execution_result` 负责交易执行语义

不再要求单个事件同时承载这两层职责。

### 7.3 事件基础字段

`RuntimeEvent` 基础字段建议如下：

- `event_type = "executor.execution_result"`
- `level = "INFO"`
- `service = "executor"`
- `region = self.region`
- `symbol = payload.get("symbol")`
- `exchange = payload.get("buy_exchange")`
- `exchanges = [payload["buy_exchange"], payload["sell_exchange"]]`
- `message = "executor execution result recorded"`

说明：

- `exchange` 继续沿用当前事件习惯，使用买腿交易所作为单值主展示字段
- `exchanges` 同时保留买卖两边，便于完整表达跨交易所执行

### 7.4 事件 payload 字段

首版 payload 固定包含以下字段：

- `task_uuid`
- `user_id`
- `source_message_id`
- `buy_exchange`
- `sell_exchange`
- `execution_status`
- `filled_exchanges`
- `failed_exchanges`
- `buy_leg_status`
- `sell_leg_status`
- `buy_leg_error_code`
- `sell_leg_error_code`
- `buy_leg_error_detail`
- `sell_leg_error_detail`
- `failed_stage`

字段约束如下：

- `execution_status`
  - 必须存在才能发事件
- `filled_exchanges`
  - 若结果对象缺失，则降级为空数组
- `failed_exchanges`
  - 若结果对象缺失，则降级为空数组
- richer result 细节字段
  - 使用 `getattr(result, field, None)` 读取
  - 对旧对象允许为 `NULL`

### 7.5 兼容策略

当前 executor 已兼容两类结果对象：

1. richer result 对象
   - 同时具备 `execution_status`、summary 字段和腿级字段
2. summary-only 对象
   - 只具备：
     - `execution_status`
     - `filled_exchanges`
     - `failed_exchanges`

本次兼容规则如下：

- 若 `execution_status is None`
  - 不发 `executor.execution_result`
- 若存在 `execution_status`
  - 一定发事件
- richer result 额外字段缺失时
  - 统一写 `NULL`
- `filled_exchanges / failed_exchanges` 缺失时
  - 统一写空数组

这样可以保证旧测试替身、老兼容对象和新 richer result 对象都能被同一路径消费。

### 7.6 触发时机

在 [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py) 中，
`RedisExecutionTaskConsumer.run()` 的触发顺序建议如下：

1. 拉取消息并进入 `mark_executing`
2. control guard / account truth / preflight 正常通过
3. 调用 `dispatcher.dispatch(...)`
4. 读取 `result.execution_status`
5. 若存在 `execution_status`
   - 先发 `executor.execution_result`
6. 再继续现有 execution summary 写回与 `processed / failed` 事件逻辑

说明：

- `executor.execution_result` 只依赖真实执行返回，不依赖数据库写回成功与否
- 这样即使后续任务表写回抛错，结构化日志仍能保留已拿到的真实执行结果
- 首版不额外改变现有异常传播模型

### 7.7 内部实现边界

本次建议在 `RedisExecutionTaskConsumer` 内部增加一个专用 builder，例如：

- `_build_execution_result_event(...)`

推荐职责：

- 从 `payload` 与 `result` 提取事件基础字段
- 处理 `filled_exchanges / failed_exchanges` 的空值归一
- 处理 richer result 字段的兼容读取
- 返回 `RuntimeEvent`

不建议把这段逻辑内联散落在 `run()` 主循环里，原因是：

- 事件 payload 字段较多
- 兼容规则需要单点维护
- 单独 builder 更容易做 focused test

### 7.8 与 AlertRouter 的关系

本次不修改 [alerting.py](file:///d:/old/FuRunSystemV4/app/runtime/alerting.py) 的外发规则。

原因：

- `AlertRouter` 当前只会对：
  - `CRITICAL`
  - `ERROR`
  - `opportunity.detected`
  做外发
- 新事件是 `INFO`
- 它应该默认只进入结构化日志，不进入飞书或邮件

因此：

- 可以先把 richer result 提升到可观测性层
- 不会增加现网告警噪音

## 8. 数据流

目标数据流如下：

1. executor 消费节点任务
2. preflight / account truth / control guard 正常通过
3. `dispatcher.dispatch(...)` 返回真实执行结果
4. 若 `execution_status` 存在：
   - 发 `executor.execution_result`
5. executor 继续按现有逻辑：
   - 写 execution summary
   - 发 `executor.task.processed` 或 `executor.task.failed`

最终效果是：

- 日志层先拥有 richer result
- 数据库层仍保持当前 execution summary 范围
- worker 处理结论与执行语义分层表达

## 9. 错误处理

错误处理规则如下：

- preflight / account truth / dispatch 前失败：
  - 不发 `executor.execution_result`
  - 继续沿用 `executor.task.failed`
- `dispatcher.dispatch(...)` 返回对象没有 `execution_status`：
  - 视为兼容旧结果对象
  - 不发 `executor.execution_result`
- `dispatcher.dispatch(...)` 返回 `execution_status`，但缺失 richer result 细节字段：
  - 允许发事件
  - 缺失字段写 `NULL`
- 事件发出后若任务摘要写回失败：
  - 首版不新增补偿机制
  - 仍沿用当前异常上抛模型

## 10. 测试策略

本次至少补以下测试：

### 10.1 `tests/test_live_workers.py`

- 当结果为 `OPEN_HEDGED` 且包含 richer result 字段时：
  - 发出 `executor.execution_result`
  - 断言 payload 中：
    - `execution_status = OPEN_HEDGED`
    - `buy_leg_status = final_fetched`
    - `sell_leg_status = final_fetched`
    - `failed_stage is None`
- 当结果为 `OPEN_PARTIAL` 且包含 richer result 字段时：
  - 发出 `executor.execution_result`
  - 断言 payload 中：
    - `execution_status = OPEN_PARTIAL`
    - `sell_leg_error_code = sell_create_failed` 或其他稳定错误码
    - `failed_stage` 为对应阶段
- 当结果对象只有 summary 字段时：
  - 仍发出 `executor.execution_result`
  - richer result 细节字段为 `None`
- preflight 失败时：
  - 不发 `executor.execution_result`

### 10.2 聚合回归

- `executor.task.processed / failed` 现有断言不回归
- execution summary 持久化现有断言不回归
- AlertRouter 不需要新增外发测试，只需保证新事件不会影响现有 `INFO` 路径

## 11. 验收标准

满足以下条件即可视为完成：

1. executor 新增 `executor.execution_result` 事件
2. 事件只在真实执行结果存在 `execution_status` 时发出
3. event payload 能稳定承载：
   - `execution_status`
   - `filled_exchanges`
   - `failed_exchanges`
   - richer result 腿级字段
4. summary-only 结果对象仍能被兼容消费
5. preflight / 非真实执行失败路径不误发该事件
6. 相关测试通过

## 12. 后续演进

本次完成后，后续可以继续沿以下方向推进，但不属于本次范围：

- 把部分 richer result 字段继续下沉到 `arbitrage_tasks`
- 让 repair / 风控直接消费 `executor.execution_result`
- 为 `executor.execution_result` 增加更稳定的 schema 版本标识
- 统一 `TradeExecutor` 与 `SpotArbitrageTaskResult` 的事件映射模型
