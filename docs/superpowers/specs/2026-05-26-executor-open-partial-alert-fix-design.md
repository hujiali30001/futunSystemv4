# Executor OPEN_PARTIAL 误报警修复设计

## 1. 文档目标

本文档定义如何修复主服务器上新的飞书误报：

- `executor.task.failed`
- `error = OPEN_PARTIAL`

本次目标不是改变 `repair worker` 链路，也不是修改飞书通知系统，而是修正
`executor` 在 `OPEN_PARTIAL` 可修复中间态下的事件派发语义，避免把本应交给
`repair` 收口的结果误发成失败告警。

## 2. 范围

本次只做以下能力：

- 修改 `executor` 在 `OPEN_PARTIAL` 且可进入 repair 计划时的失败事件派发条件
- 为该行为补 focused regression tests
- 确认 `executor.execution_result` 与 `executor.repair_planned` 仍然照常发出

本次不做以下能力：

- 不改变数据库中的 `lifecycle_status` 语义
- 不改变 `repair worker` 的消费或结果回写逻辑
- 不修改飞书通知路由或过滤规则
- 不扩大到 Redis stream 协议或 worker 架构改造

## 3. 背景与现状

当前 `executor` 在消费执行任务后，会从 `RuntimeExecutionResult` 里取出：

- `execution_status`
- `filled_exchanges`
- `failed_exchanges`

随后构造 `repair_plan` 并做三类事情：

1. 如有需要，发布 repair task
2. 如有需要，发送 `executor.repair_planned`
3. 发送 `executor.execution_result`

但当前代码还有一段额外逻辑：

- 当 `lifecycle_status == "FAILED"` 时，直接派发 `executor.task.failed`

而 `lifecycle_status` 当前是这样算的：

- `OPEN_HEDGED -> SUCCEEDED`
- 其他所有状态 -> FAILED`

这导致：

- `OPEN_PARTIAL` 虽然已经被识别为“部分成交、可进入 repair”
- 仍会被额外包装成 `executor.task.failed`
- 最终飞书收到一条新的误报：`error = OPEN_PARTIAL`

## 4. 问题定义

`OPEN_PARTIAL` 在当前系统里并不总是“终态失败”。

对可修复的 `OPEN_PARTIAL` 而言，系统已经有更精确的语义表达：

- `executor.execution_result`
- `executor.repair_planned`

如果此时再额外发：

- `executor.task.failed`

会产生两个问题：

1. 飞书把可修复中间态误认成失败
2. 运营侧看到重复且冲突的信号：一边说已进入 repair，一边又报 task failed

因此，本次要修复的不是 `OPEN_PARTIAL` 本身，而是它在可修复场景下被错误映射成
`executor.task.failed` 的派发条件。

## 5. 设计目标

本次设计满足以下目标：

1. 可进入 repair 的 `OPEN_PARTIAL` 不再触发 `executor.task.failed`
2. 同一场景下仍保留：
   - `executor.execution_result`
   - `executor.repair_planned`
3. 真正异常和不可修复失败仍继续发 `executor.task.failed`
4. 修改范围保持最小，不扩散到数据库或飞书系统

## 6. 方案比较

### 6.1 方案 A：所有 `OPEN_PARTIAL` 都不发 `executor.task.failed`

做法：

- 只要 `execution_status == OPEN_PARTIAL` 就全部静音

优点：

- 最简单

缺点：

- 某些没有进入 repair 计划的 `OPEN_PARTIAL` 也会被静音
- 容易掩盖真正需要人工关注的部分失败

### 6.2 方案 B：只静音“可 repair 的 `OPEN_PARTIAL`”

做法：

- 仅当以下条件同时满足时，不派发 `executor.task.failed`：
  - `execution_status == OPEN_PARTIAL`
  - `failed_exchanges` 非空
  - `repair_plan.action != "NONE"`

优点：

- 只去掉误报
- 不影响真正失败
- 与现有 `repair_planned` 语义完全一致

缺点：

- 需要补 focused regression，确保边界行为稳定

### 6.3 方案 C：保留代码逻辑，改飞书过滤

做法：

- 继续发 `executor.task.failed`
- 由通知层过滤 `OPEN_PARTIAL`

优点：

- 应用代码改动最少

缺点：

- 事件语义仍然脏
- 飞书以外的其他事件消费者仍会收到误导性失败事件

### 6.4 推荐方案

本次采用方案 B。

原因：

- 它只修正真正错误的事件语义
- 不隐藏真实失败
- 与当前已存在的 `repair_planned` 设计完全一致
- 修改范围最小

## 7. 核心设计

### 7.1 可 repair 的 `OPEN_PARTIAL` 判定

本次沿用现有 repair 触发口径，不新增新的判定规则。

若同时满足以下条件，则视为“可进入 repair 的 `OPEN_PARTIAL`”：

- `execution_status == "OPEN_PARTIAL"`
- `failed_exchanges` 非空
- `repair_plan.action != "NONE"`

这与当前发布 repair task 和发送 `executor.repair_planned` 的口径保持一致。

### 7.2 失败事件派发条件修正

当前失败事件派发逻辑是：

- 只要 `lifecycle_status == "FAILED"`，就发 `executor.task.failed`

本次改为：

- 先计算 `should_emit_failed_event`
- 默认沿用现有失败语义
- 但如果当前结果属于“可进入 repair 的 `OPEN_PARTIAL`”，则：
  - `should_emit_failed_event = False`

最终行为为：

- `should_emit_failed_event == False`
  - 不派发 `executor.task.failed`
  - 直接结束当前消息处理
- `should_emit_failed_event == True`
  - 保持当前失败事件派发逻辑不变

### 7.3 保留现有结果与 repair 事件

本次不修改以下两类事件：

- `executor.execution_result`
- `executor.repair_planned`

也就是说，对可 repair 的 `OPEN_PARTIAL`：

- 仍然会保留结构化执行结果
- 仍然会保留 repair 计划事件
- 只是去掉额外的 `executor.task.failed`

### 7.4 不改变数据库状态语义

虽然 `OPEN_PARTIAL` 当前会映射成数据库侧的 `lifecycle_status = FAILED`，
但本次不修改该行为。

原因：

- 这会牵涉任务状态摘要的更大语义调整
- 当前用户最紧急的问题是飞书误报，而不是数据库状态模型
- 本次只修事件派发层，保持范围最小

### 7.5 真实失败保留报警

以下场景本次仍保留 `executor.task.failed`：

1. preflight / 凭证 / account truth 等真实异常
2. dispatch 过程中抛出的真实异常
3. 没有进入 repair 计划的失败结果
4. 其他非 `OPEN_PARTIAL` 的失败结果

这样可以确保飞书仍能看到真正需要人工关注的失败。

## 8. 错误处理

本次不新增异常类型。

只调整事件派发条件：

- 对“可 repair 的 `OPEN_PARTIAL`”不再人为构造 `RuntimeError(execution_status)`
- 对其他真实失败，继续沿用现有异常和失败事件逻辑

如果修复后飞书仍继续出现 `error = OPEN_PARTIAL`，则说明：

- 还有其他路径在派发同名失败事件
- 或者是旧日志/历史消息重放

那时再继续排查其他事件来源，不在本次范围内顺手扩写。

## 9. 测试策略

本次至少补充以下 focused tests：

1. `OPEN_PARTIAL` 且进入 repair 计划时：
   - 不发 `executor.task.failed`
2. 同一场景下仍会发：
   - `executor.execution_result`
   - `executor.repair_planned`
3. 非 repair 场景的真实失败：
   - 继续发 `executor.task.failed`

同时保留现有 `OPEN_PARTIAL`、`repair_planned`、`execution_result` 相关测试通过。

## 10. 验收标准

满足以下条件即可视为完成：

1. 新增 regression test 先红后绿
2. 可 repair 的 `OPEN_PARTIAL` 不再派发 `executor.task.failed`
3. 同一场景仍派发：
   - `executor.execution_result`
   - `executor.repair_planned`
4. 真实失败路径仍会派发 `executor.task.failed`
5. 修复后主服务器重启 `executor`，飞书不再持续收到：
   - `executor.task.failed`
   - `error = OPEN_PARTIAL`

## 11. 后续演进

本次完成后，后续可以继续推进，但不属于本次范围：

- 重新审视 `OPEN_PARTIAL` 在数据库侧是否应继续映射为 `FAILED`
- 为事件路由增加更清晰的“recoverable warning / terminal failure”分层
- 为飞书通知链增加更细粒度的事件类型过滤
