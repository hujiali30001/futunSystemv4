# Repair Worker 最小自动补单设计

## 1. 文档目标

本文档定义如何在现有 `executor.repair_planned` 事件基础上，新增一条最小可交付的 repair 执行闭环，让系统从“能生成 repair 计划”推进到“能执行一次最小自动补单”。

本次目标是在不引入复杂恢复优先级链、不新增 repair 专用表、也不扩展为完整风险账本的前提下，完成以下能力：

- 新增一个最小 `repair worker`
- 消费 `executor.repair_planned`
- 仅处理 `AUTO_HEDGE_REPAIRING`
- 仅对失败腿执行一次最小自动补单尝试
- 产出 repair 结果事件
- 用现有任务摘要字段完成状态收口

## 2. 范围

本次只做以下能力：

- 新增最小 `repair worker`，专门消费 `executor.repair_planned`
- 只覆盖 `repair_action == AUTO_HEDGE_REPAIRING`
- 只覆盖 `OPEN_PARTIAL` 下的失败腿补单
- 只执行一次最小自动补单尝试
- 新增一类 repair 结果事件，用于表达补单成功或失败
- 复用现有任务表摘要字段完成最小状态收口

本次不做以下能力：

- 不做反向平已成交腿
- 不做多阶段恢复优先级链
- 不做自动减仓、自动全平、自动撤单重发
- 不新增 `risk_events` / `repair_events` 表
- 不新增独立 repair 账本
- 不重构现有 `executor` 主链
- 不扩展完整 systemd 生产部署链

## 3. 背景与现状

当前系统已经具备以下基础：

- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py)
  中 `RedisExecutionTaskConsumer` 已在 `OPEN_PARTIAL` 时发出 `executor.repair_planned`
- [risk_manager.py](file:///d:/old/FuRunSystemV4/app/trading/risk_manager.py)
  已将 `OPEN_PARTIAL` 映射为：
  - `AUTO_HEDGE_REPAIRING`
  - `one_leg_failed`
- [task_repository.py](file:///d:/old/FuRunSystemV4/app/db/task_repository.py)
  已能写入：
  - `execution_status`
  - `filled_exchanges`
  - `failed_exchanges`
  - `repair_action`
  - `repair_reason`
- [worker_service.py](file:///d:/old/FuRunSystemV4/app/runtime/worker_service.py)
  已有成熟 worker 装配模式
- [trade_execution_service.py](file:///d:/old/FuRunSystemV4/app/runtime/trade_execution_service.py)
  已有 executor 主链接入 `TradeExecutor` 的 runtime service 模式

但当前仍有关键缺口：

1. `executor.repair_planned` 仍只是编排事件，没有执行方消费
2. 系统还不能把“失败腿需要补单”推进成真实自动补单动作
3. repair 仍停留在“会计划、会记录”，还没有变成“会执行”

因此，本次需要补一条最小 repair 执行闭环。

## 4. 问题定义

如果继续保持现状，会有以下问题：

1. `OPEN_PARTIAL` 只能产出 repair 建议，不能自动补救
2. 后续所有补偿仍需要人工介入，系统无法向总体设计中的自动补偿目标推进
3. `executor.repair_planned` 事件缺少真实下游消费面，价值仍不完整

因此，本次需要先新增一个最小 repair worker，把 repair 从“计划对象”推进到“执行对象”。

## 5. 设计目标

本次设计满足以下目标：

1. `executor.repair_planned` 首次拥有真实消费方
2. `AUTO_HEDGE_REPAIRING` 可以触发一次真实自动补单尝试
3. 成功和失败两条路径都能形成结构化结果事件
4. 不重做前面已稳定的 executor 主链
5. 保持范围最小，优先追求最快闭环

## 6. 方案比较

### 6.1 方案 A：新增最小 repair worker，只做一次失败腿补单

做法：

- 新增独立 repair worker
- 输入只消费 `executor.repair_planned`
- 只支持 `AUTO_HEDGE_REPAIRING`
- 只对 `target_exchanges` 对应失败腿尝试一次最小补单
- 成功或失败后发 repair 结果事件，并回写任务摘要

优点：

- 最短路径形成第一条真实 repair 执行闭环
- 复用现有 `repair_planned` 契约，不需要返工 executor
- 风险和改动面最可控

缺点：

- 能力较窄，只覆盖最小补单场景

### 6.2 方案 B：直接做完整 repair 策略链

做法：

- 一次性补上补单、反向平仓、减仓、全平、优先级链和降级策略

优点：

- 更接近最终生产形态

缺点：

- 范围过大
- 风险高，开发周期长
- 不符合当前“加快开发进度”的目标

### 6.3 方案 C：继续只补测试和状态字段，不上 worker

做法：

- 继续增强 `risk_manager`、repository、事件测试
- 不增加 repair 执行方

优点：

- 风险最低

缺点：

- 总体完成度提升有限
- 不能把 repair 推进到真实执行闭环

### 6.4 推荐方案

本次采用方案 A。

原因：

- 它能以最小范围把系统推进到“会执行 repair”
- 它直接命中当前最大缺口
- 它最符合“高价值、最短路径、可快速形成闭环”的推进目标

## 7. 核心设计

### 7.1 新增最小 repair worker

本次新增一个独立的 repair worker，例如：

- `RedisRepairTaskConsumer`

其职责是：

- 消费 repair 输入事件
- 校验是否属于当前支持的 repair 动作
- 解析失败腿与目标交易所
- 调用最小 repair 执行服务完成一次自动补单
- 发出 repair 结果事件
- 回写任务摘要的最小状态收口

repair worker 不负责：

- 重新推导 repair 计划
- 决定复杂补偿优先级链
- 执行反向平仓或自动全平

### 7.2 输入契约

本次不新增新的 repair 请求协议，直接复用现有：

- `executor.repair_planned`

worker 只读取以下最小字段：

- `task_uuid`
- `symbol`
- `buy_exchange`
- `sell_exchange`
- `execution_status`
- `failed_exchanges`
- `repair_action`
- `repair_reason`
- `target_exchanges`

约束如下：

- `repair_action` 必须等于 `AUTO_HEDGE_REPAIRING`
- `execution_status` 必须等于 `OPEN_PARTIAL`
- `target_exchanges` 首版必须非空
- 若任一条件不满足，则 worker 不执行补单，直接走忽略或失败收口

### 7.3 最小 repair 执行动作

首版只做一种动作：

- 对失败腿执行一次自动补单尝试

明确边界：

- 只重试一次
- 只针对 `target_exchanges` 指向的失败腿
- 不对已成交腿做反向平仓
- 不引入第二阶段恢复策略

如果事件中：

- `target_exchanges == ["gate"]`

则 worker 只尝试补 `gate` 对应失败腿。

### 7.4 最小 repair 执行服务

建议新增一个 focused runtime service，例如：

- `RuntimeRepairExecutionService`

其职责是：

- 接收 repair 事件上下文
- 根据目标交易所和腿方向构造最小执行输入
- 调用当前已有交易执行基础设施，对失败腿发起补单
- 返回结构化 repair 结果

建议最小返回模型如下：

```python
@dataclass(slots=True)
class RuntimeRepairResult:
    ok: bool
    status: str
    task_uuid: str
    target_exchanges: list[str]
    repaired_exchanges: list[str]
    remaining_failed_exchanges: list[str]
    reason: str | None = None
```

其中：

- 成功时：
  - `ok = True`
  - `status = "REPAIRED"`
- 失败时：
  - `ok = False`
  - `status = "MANUAL_REQUIRED"`

### 7.5 repair 结果事件

本次建议新增一类最小结果事件：

- `repair.task.finished`

用途：

- 作为 repair worker 的结构化输出
- 明确表达“这次 repair 是否成功”
- 为后续监控、日志、下游消费保留稳定契约

基础字段建议：

- `event_type = "repair.task.finished"`
- `level = "INFO"` 或失败时 `ERROR`
- `service = "repair"`
- `region = self.region`
- `symbol = payload.get("symbol")`
- `exchange = payload.get("buy_exchange")`
- `exchanges = [buy_exchange, sell_exchange]`
- `message = "repair task finished"`

payload 首版至少包含：

- `task_uuid`
- `repair_action`
- `repair_reason`
- `target_exchanges`
- `repaired_exchanges`
- `remaining_failed_exchanges`
- `status`
- `reason`

### 7.6 任务状态收口

本次不新增新表，优先复用现有任务摘要字段完成最小收口。

首版约束如下：

- repair 成功：
  - 任务要推进到更接近“已修复/已对冲”的口径
  - 允许通过现有 `mark_execution_result(...)` 语义扩展，或新增 focused repository 方法完成
- repair 失败：
  - 任务要推进到“人工处理”口径
  - 继续保留 `repair_action / repair_reason`

为避免一次性扩大模型，本次不要求完整引入总体设计中的全状态机，只要求：

1. repair 成功和失败能在任务摘要层区分开
2. 后续可以基于该状态继续扩展更复杂生命周期

### 7.7 与现有 executor 链的关系

本次不改动现有 executor 主链分工：

- `executor.execution_result`
  - 表达执行事实
- `executor.repair_planned`
  - 表达 repair 计划
- 新 `repair.task.finished`
  - 表达 repair 执行结果

这样三层职责清晰分离：

1. 执行事实
2. repair 编排
3. repair 执行结果

### 7.8 worker 装配

建议沿用现有 `worker_service.py` 的装配模式，新增 repair worker builder。

职责：

- 构造 repair consumer
- 注入最小 repair execution service
- 复用现有 `TaskRepository`、事件路由和运行区参数

首版不要求把 repair worker 接入复杂多节点路由，只要求先具备本地与测试可装配能力。

## 8. 数据流

目标数据流如下：

1. executor 产生 `executor.repair_planned`
2. repair worker 消费该事件
3. 校验 `repair_action == AUTO_HEDGE_REPAIRING`
4. 根据 `target_exchanges` 定位失败腿
5. repair execution service 对失败腿执行一次最小补单
6. 得到 `RuntimeRepairResult`
7. 发出 `repair.task.finished`
8. 更新任务摘要到成功或人工处理口径

## 9. 错误处理

错误处理规则如下：

- 如果输入事件缺少关键字段：
  - 不执行 repair
  - 发失败结果事件
- 如果 `repair_action != AUTO_HEDGE_REPAIRING`：
  - 首版不支持
  - 直接忽略或标记失败，但不做执行
- 如果补单执行抛异常：
  - 视为 repair 失败
  - 结果状态为 `MANUAL_REQUIRED`
- 如果任务摘要回写失败：
  - 首版仍沿用当前异常上抛模型
  - 不新增补偿事务

## 10. 测试策略

本次至少补以下测试：

### 10.1 repair worker 主链测试

- 当收到 `AUTO_HEDGE_REPAIRING + target_exchanges=["gate"]`：
  - 触发一次最小补单执行
  - 发出 `repair.task.finished`
  - `status = REPAIRED` 或 `MANUAL_REQUIRED`
- 当输入不是 `AUTO_HEDGE_REPAIRING`：
  - 不执行补单
- 当 `target_exchanges` 为空：
  - 不执行补单

### 10.2 repair 执行服务测试

- 成功补单返回：
  - `ok = True`
  - `status = REPAIRED`
- 补单失败返回：
  - `ok = False`
  - `status = MANUAL_REQUIRED`

### 10.3 装配与回归测试

- repair worker 装配测试
- 现有 executor 主链测试不回归
- `executor.repair_planned` 现有契约不回归

## 11. 验收标准

满足以下条件即可视为完成：

1. 新增最小 repair worker
2. worker 能消费 `executor.repair_planned`
3. `AUTO_HEDGE_REPAIRING` 能触发一次真实自动补单尝试
4. 成功和失败都能发出 `repair.task.finished`
5. 任务摘要能区分 repair 成功与人工处理口径
6. 本地测试通过

## 12. 后续演进

本次完成后，后续可以继续沿以下方向推进，但不属于本次范围：

- 支持反向平已成交腿
- 支持自动减仓、自动全平
- 引入完整 repair 生命周期状态机
- 增加 `risk_events` / `repair_events` 持久化
- 增加主服务器远端验证与 systemd 部署收口
