# Executor 执行结果任务摘要回写设计

## 1. 文档目标

本文档定义如何把 executor 的真实执行结果结构化回写到 `arbitrage_tasks`，让任务真值不仅能表达“任务有没有流转完成”，还能表达“执行结果本身是什么”。

本次目标是在不新增 execution attempt 明细表、不引入订单级持久化、也不重做完整执行编排框架的前提下，为任务表补齐执行摘要字段，并让 executor 在拿到真实 `ExecutionResult` 后写回：

- 执行结果状态
- 成功交易所列表
- 失败交易所列表
- repair 建议 action
- repair 建议 reason

这样任务表就可以直接回答“这条任务最终是否双腿成功、是否部分失败、是否生成了 repair 建议”，而不再只能依赖 `status / status_reason` 做粗粒度判断。

## 2. 范围

本次只做以下能力：

- 定义任务级执行摘要字段
- 定义 executor 成功执行后的摘要写回规则
- 定义 `OPEN_HEDGED / OPEN_PARTIAL` 如何映射到任务真值
- 定义 repair 建议如何作为摘要信息写回
- 定义 repository 与 executor 消费链路的最小改造
- 定义对应测试与验收标准

本次不做以下能力：

- 不新建 execution attempt / execution result 明细表
- 不持久化订单级明细、成交回报或订单 ID
- 不自动执行 repair
- 不改写现有下单并发模型
- 不实现余额、持仓、费率、滑点等更强交易风控
- 不统一所有历史失败路径的结构化回写

## 3. 背景与现状

当前系统已经具备以下基础：

- [models.py](file:///d:/old/FuRunSystemV4/models.py) 中 `ArbitrageTask` 已承载任务真值，包含：
  - `status`
  - `status_reason`
  - `buy_account_id`
  - `sell_account_id`
  - `worker_node_id`
- [task_repository.py](file:///d:/old/FuRunSystemV4/app/db/task_repository.py) 已支持：
  - `mark_dispatched`
  - `mark_executing`
  - `mark_succeeded`
  - `mark_failed`
  - `mark_blocked`
- [executor.py](file:///d:/old/FuRunSystemV4/app/trading/executor.py) 中 `TradeExecutor.execute_open()` 已返回：
  - `status`
  - `filled_exchanges`
  - `failed_exchanges`
- [risk_manager.py](file:///d:/old/FuRunSystemV4/app/trading/risk_manager.py) 已能基于 `ExecutionResult` 生成：
  - `RepairPlan.action`
  - `RepairPlan.reason`
- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py) 中 executor 已有：
  - task-account-binding
  - executor preflight
  - `mark_executing / mark_succeeded / mark_failed / mark_blocked`

但当前仍有明显缺口：

1. executor 即使拿到了 `ExecutionResult`，任务表里也没有结构化执行结果字段
2. `status=SUCCEEDED` 只能表示“任务链路完成”，不能独立表达“交易两腿都成功”
3. `status=FAILED` 既可能代表 preflight/dispatch 失败，也可能代表真实执行的部分失败，语义混在一起
4. repair 建议虽然可以在内存里计算，但不会沉淀到任务真值中

因此，本次需要把“真实执行结果摘要”补进 `arbitrage_tasks`。

## 4. 问题定义

如果继续保持现状，会有以下问题：

1. 运维无法直接从任务表回答“这条任务是 `OPEN_HEDGED` 还是 `OPEN_PARTIAL`”
2. 无法直接知道买卖两边哪些交易所成功、哪些失败
3. 未来做 repair 排障时，看不到任务级 repair 建议
4. `status` 同时承载任务生命周期和执行结果，语义容易混淆

因此，本次需要把“任务生命周期状态”和“执行结果状态”明确分层。

## 5. 设计目标

本次设计满足以下目标：

1. `status` 继续表达任务生命周期
2. 新增 `execution_status` 表达真实执行结果
3. executor 在拿到 `ExecutionResult` 后写回成功/失败交易所摘要
4. `OPEN_PARTIAL` 场景下写回 repair 建议
5. preflight、account truth、dispatch 失败等非真实执行失败路径不伪造 execution 摘要

## 6. 方案比较

### 6.1 方案 A：只在 `arbitrage_tasks` 写任务级执行摘要

做法：

- 给 `arbitrage_tasks` 增加少量摘要字段
- executor 在拿到真实 `ExecutionResult` 后直接写回任务表

优点：

- 范围小
- 运维查询直接
- 与当前“任务真值中心化”路线一致
- 最适合接在已完成的 task-account-binding 与 preflight 之后继续演进

缺点：

- 只能表达任务级摘要，不能承载多次 attempt 或订单级细节

### 6.2 方案 B：新建 execution result / attempt 明细表

做法：

- 为任务新增一张执行结果表或 attempt 表
- 任务表只保留聚合字段

优点：

- 更利于长期演进到多次执行与更细审计

缺点：

- 范围明显扩大
- 需要新的表结构、repository、查询和测试切面
- 不适合本轮“小步补齐任务真值”的目标

### 6.3 方案 C：继续只用 `status_reason` 压缩表达

做法：

- 不加新字段
- 把 `OPEN_PARTIAL`、repair 等信息塞进 `status_reason`

优点：

- 改动最小

缺点：

- 结构化价值很低
- 无法稳定查询成功/失败交易所列表
- 会继续放大 `status_reason` 的职责

### 6.4 推荐方案

本次采用方案 A。

原因：

- 它能以最小代价把执行结果补进任务真值
- 它与当前 `arbitrage_tasks` 作为任务链主落点的设计一致
- 它为后续如需新增 execution attempt 表保留了自然升级路径

## 7. 核心设计

### 7.1 任务真值字段扩展

本次建议在 `ArbitrageTask` 中新增以下字段：

- `execution_status`
- `filled_exchanges_json`
- `failed_exchanges_json`
- `repair_action`
- `repair_reason`

字段语义如下：

- `execution_status`
  - 表达真实执行结果语义
  - 首版只写：
    - `OPEN_HEDGED`
    - `OPEN_PARTIAL`
- `filled_exchanges_json`
  - JSON 数组
  - 保存本次真实执行成功的交易所列表
- `failed_exchanges_json`
  - JSON 数组
  - 保存本次真实执行失败的交易所列表
- `repair_action`
  - 保存 `RiskManager.build_repair_plan()` 生成的 `action`
- `repair_reason`
  - 保存 `RiskManager.build_repair_plan()` 生成的 `reason`

### 7.2 `status` 与 `execution_status` 分层

本次明确分层如下：

- `status`
  - 负责表达任务生命周期
  - 例如：
    - `CREATED`
    - `DISPATCHED`
    - `EXECUTING`
    - `SUCCEEDED`
    - `FAILED`
    - `BLOCKED`
- `execution_status`
  - 负责表达真实执行结果
  - 例如：
    - `OPEN_HEDGED`
    - `OPEN_PARTIAL`

这样：

- `status=SUCCEEDED` 表示任务执行链路完成
- `execution_status=OPEN_HEDGED` 表示真实交易结果两腿都成功

同理：

- `status=FAILED` 可以表示任务最终失败
- `execution_status=OPEN_PARTIAL` 可以明确表示失败原因来自真实执行阶段的部分成交/部分失败

### 7.3 executor 写回时机

只有在 executor 拿到真实 `ExecutionResult` 后，才写 execution 摘要。

建议时机如下：

1. preflight 通过
2. account truth 解析通过
3. 真实执行返回 `ExecutionResult`
4. 若结果为 `OPEN_PARTIAL`，调用 `RiskManager.build_repair_plan(result)`
5. 通过一次 repository 更新同时写回执行摘要与最终生命周期状态

说明：

- preflight 失败、account truth 失败、dispatch 前异常，不应伪造 execution 摘要
- execution 摘要只反映“真实执行结果”，不反映入口失败

### 7.4 executor 写回规则

#### `OPEN_HEDGED`

当 `ExecutionResult.status == "OPEN_HEDGED"` 时：

- `status = "SUCCEEDED"`
- `execution_status = "OPEN_HEDGED"`
- `filled_exchanges_json = result.filled_exchanges`
- `failed_exchanges_json = result.failed_exchanges`
- `repair_action = "NONE"`
- `repair_reason = "fully_hedged"`
- `status_reason = NULL`

#### `OPEN_PARTIAL`

当 `ExecutionResult.status == "OPEN_PARTIAL"` 时：

- `status = "FAILED"`
- `execution_status = "OPEN_PARTIAL"`
- `filled_exchanges_json = result.filled_exchanges`
- `failed_exchanges_json = result.failed_exchanges`
- `repair_action = repair_plan.action`
- `repair_reason = repair_plan.reason`
- `status_reason = NULL`

首版配套 `RiskManager` 结果为：

- `repair_action = "AUTO_HEDGE_REPAIRING"`
- `repair_reason = "one_leg_failed"`

### 7.5 非真实执行失败路径的处理

以下失败路径维持当前行为，不写 execution 摘要：

- preflight 失败
- control rule block
- account truth resolver 抛错
- 真实执行前的 dispatch 级异常

这些路径继续使用：

- `status`
- `status_reason`

但：

- `execution_status` 保持 `NULL`
- `filled_exchanges_json` 保持空或 `NULL`
- `failed_exchanges_json` 保持空或 `NULL`
- `repair_action` 保持 `NULL`
- `repair_reason` 保持 `NULL`

### 7.6 repository 设计

本次建议给 `TaskRepository` 增加一个聚焦执行摘要写回的新方法，例如：

- `mark_execution_result(...)`

推荐职责：

- 查找任务
- 写入：
  - `status`
  - `execution_status`
  - `filled_exchanges_json`
  - `failed_exchanges_json`
  - `repair_action`
  - `repair_reason`
  - `finished_at`
- commit 后 refresh 返回任务对象

不建议把 execution 摘要硬塞进 `mark_succeeded()` 或 `mark_failed()`，原因是：

- 这两个方法已经承担了通用生命周期更新职责
- execution 摘要是更具体的真实执行结果写回
- 单独方法边界更清晰，也更容易做测试

### 7.7 executor 与 `TradeExecutor` / `RiskManager` 的关系

本次不改 `TradeExecutor.execute_open()` 的并发执行行为。

只要求：

- executor 在拿到 `ExecutionResult` 后做任务摘要写回
- `OPEN_PARTIAL` 时调用 `RiskManager.build_repair_plan(result)`

职责分工：

- `TradeExecutor`
  - 负责产生真实执行结果
- `RiskManager`
  - 负责基于执行结果生成 repair 建议
- `TaskRepository`
  - 负责把结果和 repair 建议写回任务真值
- executor worker
  - 负责串联这三者

## 8. 数据流

目标数据流如下：

1. executor 消费节点任务
2. preflight / control rule / account truth 正常通过
3. 真实执行返回 `ExecutionResult`
4. 如为 `OPEN_PARTIAL`，生成 `RepairPlan`
5. repository 写 execution 摘要
6. 任务表最终同时拥有：
  - 生命周期状态
  - 执行结果状态
  - 成功/失败交易所列表
  - repair 建议

## 9. 错误处理

错误处理规则如下：

- preflight / account truth / dispatch 前失败：
  - 维持当前行为
  - 只写 `status / status_reason`
  - 不写 execution 摘要
- 真实执行返回 `OPEN_PARTIAL`：
  - 不视为“无结果异常”
  - 要把它当成真实执行结果写回
  - 同时写 repair 建议
- 真实执行返回 `OPEN_HEDGED`：
  - 写成功结果摘要
- 若写执行摘要时 repository 自身抛错：
  - 首版沿用当前异常上抛模型
  - 不额外设计新的补偿流程

## 10. 测试策略

本次至少补以下测试：

### 10.1 `tests/test_task_repository.py`

- 新增 execution 摘要字段持久化测试
- 新增 `mark_execution_result(...)` 写回测试
- 覆盖：
  - `OPEN_HEDGED`
  - `OPEN_PARTIAL`
  - repair action / reason

### 10.2 `tests/test_trading_engine.py`

- 保留现有 `ExecutionResult` 与 `RiskManager` 基础回归
- 如有必要，补 `OPEN_HEDGED` 对应 `RepairPlan(action="NONE", reason="fully_hedged")` 的显式断言

### 10.3 `tests/test_live_workers.py`

- executor 成功执行后写：
  - `status = SUCCEEDED`
  - `execution_status = OPEN_HEDGED`
- executor 部分失败后写：
  - `status = FAILED`
  - `execution_status = OPEN_PARTIAL`
  - `repair_action`
  - `repair_reason`
- preflight 失败路径继续只写 `status_reason`，不误写 execution 摘要

回归要求：

- 不回归已完成的 task-account-binding
- 不回归 executor preflight
- 不回归现有任务生命周期流转

## 11. 验收标准

满足以下条件即可视为完成：

1. `arbitrage_tasks` 新增执行摘要字段
2. `TaskRepository` 支持结构化写回真实执行结果
3. executor 能在 `OPEN_HEDGED` 时写成功摘要
4. executor 能在 `OPEN_PARTIAL` 时写失败摘要与 repair 建议
5. preflight / dispatch 前失败路径不伪造 execution 摘要
6. 相关测试通过

## 12. 后续演进

本次完成后，后续可以继续沿以下方向推进，但不属于本次范围：

- 新建 execution attempt 明细表
- 持久化订单级返回信息
- 记录多次 repair attempt
- 为 repair 自动执行建立独立任务链
- 统一更多 executor 失败路径的结构化结果模型
