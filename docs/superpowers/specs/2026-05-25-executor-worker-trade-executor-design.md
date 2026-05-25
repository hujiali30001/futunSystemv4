# Executor Worker 切换到正式 Trade Executor 设计

## 1. 文档目标

本文档定义如何把 executor worker 的主执行链路，从当前的
`SpotArbitrageProbeService` 切换为一个正式的 trade execution service，让
`RedisExecutionTaskConsumer` 不再把 probe 当作默认执行器，同时保持现有任务摘要
写回与 `executor.execution_result` 事件兼容。

本次目标是在不重做 scanner / consumer / dispatcher 主链、不删除现有 probe 服务、
也不扩展数据库 schema 的前提下，完成以下收敛：

- `build_executor_worker()` 不再注入 `SpotArbitrageProbeService`
- executor 主链改为注入一个正式 trade execution service
- 新 service 内部调用 `TradeExecutor`
- 输出结果兼容现有 executor 下游依赖的最小字段：
  - `ok`
  - `execution_status`
  - `filled_exchanges`
  - `failed_exchanges`

## 2. 范围

本次只做以下能力：

- 新增 runtime 侧正式执行服务适配层
- 将 executor worker 从 probe 切换到正式执行服务
- 保持 `RedisOpportunityDispatcher` 的现有调用壳不回归
- 保持 execution summary 写回和 `executor.execution_result` 事件兼容
- 定义本次最小测试与验收标准

本次不做以下能力：

- 不改 scanner worker
- 不改 consumer worker
- 不删除 `SpotArbitrageProbeService`
- 不统一 scanner / consumer / executor 三侧的 service 模型
- 不做 close / repair 正式执行
- 不要求正式执行服务首版立刻补齐 probe 的所有 richer result 字段
- 不新增任务表明细、腿表、订单表、成交表

## 3. 背景与现状

当前接线关系如下：

- [worker_service.py](file:///d:/old/FuRunSystemV4/app/runtime/worker_service.py)
  中 `build_executor_worker()` 仍然执行：
  - `dispatcher = RedisOpportunityDispatcher(self.spot_service)`
- [redis_flow.py](file:///d:/old/FuRunSystemV4/app/runtime/redis_flow.py)
  中 `RedisOpportunityDispatcher.dispatch(...)` 仍统一调用：
  - `self.spot_service.run_task(...)`
- [spot_arbitrage_probe.py](file:///d:/old/FuRunSystemV4/app/runtime/spot_arbitrage_probe.py)
  当前承担 executor 主链的真实执行返回
- [executor.py](file:///d:/old/FuRunSystemV4/app/trading/executor.py)
  已存在 `TradeExecutor`，并能基于 `ExecutionTask.open_legs` 做双腿并发下单

因此，当前存在明显不匹配：

1. runtime 主链里已存在正式 `TradeExecutor` 雏形，但 executor worker 还在用 probe
2. `SpotArbitrageProbeService` 更像 runtime 探测器，而不是设计稿里的正式 trade-worker
3. executor 主链已经有 execution summary 与 execution event 语义，但底层执行器仍偏临时实现

## 4. 问题定义

如果继续保持当前形态，会有以下问题：

1. executor worker 的“正式执行”语义仍与 probe 耦合
2. 后续要补风险补偿、订单事实、正式 open/close 执行时，主链仍要先做一次接线替换
3. 当前 `TradeExecutor` 难以进入真实运行路径，只停留在独立模块
4. 继续给 probe 增功能会把临时服务越做越重，增加未来替换成本

因此，本次需要先完成“executor worker 接线替换”，把正式执行器接入真实主链。

## 5. 设计目标

本次设计满足以下目标：

1. executor worker 不再默认使用 `SpotArbitrageProbeService`
2. 正式执行器先以最小适配形式进入主链，不强行同步重构所有 runtime 服务
3. 现有 execution summary 写回、`executor.execution_result` 事件、preflight 行为都不回归
4. `SpotArbitrageProbeService` 仍保留给现有 scanner / consumer 或专门探测用途

## 6. 方案比较

### 6.1 方案 A：只替换 executor worker 为正式 trade execution service

做法：

- 新增一个 runtime 适配层，例如：
  - `RuntimeTradeExecutionService`
- 只在 `build_executor_worker()` 中把注入对象从 probe 换成这个 service
- `RedisOpportunityDispatcher` 接口暂不重构

优点：

- 改动面最小
- 最适合快速提升主链完成度
- 能尽快让 `TradeExecutor` 真正进入运行路径

缺点：

- `RedisOpportunityDispatcher` 暂时仍保持泛化不足的 `spot_service` 命名
- scanner / consumer / executor 三侧 service 模型还不统一

### 6.2 方案 B：替换 executor 并顺手统一 dispatcher 接口

做法：

- 除了接入正式执行服务，还同步重命名/抽象 dispatcher 的 service 接口

优点：

- 结构更干净

缺点：

- 范围明显变大
- 会拖慢这轮主链替换速度

### 6.3 方案 C：直接重做执行主链

做法：

- 同时改 executor、dispatcher、结果模型、repair 接口

优点：

- 更接近最终架构

缺点：

- 超出本次“小步加速”目标
- 风险与回归面太大

### 6.4 推荐方案

本次采用方案 A。

原因：

- 它能最快把 executor 主链从 probe 切到正式执行器
- 它不会立即扩大到 scanner / consumer 重构
- 它为下一步做 risk / repair 与事实账本打下更稳的执行基线

## 7. 核心设计

### 7.1 新增 `RuntimeTradeExecutionService`

本次建议新增一个 runtime 适配层，例如：

- `app/runtime/trade_execution_service.py`

它的职责是：

1. 接收当前 dispatcher 已传入的 runtime 参数
2. 根据 payload 与账户真值构造 `ExecutionTask`
3. 构建 `TradeExecutor` 所需的 adapter 映射
4. 调用 `TradeExecutor.execute_open(...)`
5. 将返回结果适配成 executor 主链兼容对象

它不负责：

- 任务状态写回
- 运行时事件派发
- repair 自动动作
- scanner / consumer 场景的 probe 逻辑

### 7.2 与 `TradeExecutor` 的边界

[executor.py](file:///d:/old/FuRunSystemV4/app/trading/executor.py)
中的 `TradeExecutor` 继续作为纯执行器存在。

本次不把 payload 解析、账户真值、session 管理直接塞进 `TradeExecutor`，而是保持边界：

- runtime service 负责：
  - payload -> `ExecutionTask`
  - account/session -> adapter factory
- `TradeExecutor` 负责：
  - 根据 open legs 执行
  - 产出最小 `ExecutionResult`

这样可以避免 trading 层和 runtime 层继续耦合。

### 7.3 `ExecutionTask` 构造方式

首版只支持 open 执行。

`RuntimeTradeExecutionService.run_task(...)` 至少要基于以下字段构造 `ExecutionTask`：

- `symbol`
- `buy_exchange`
- `sell_exchange`
- `target_quote_amount`

执行腿至少包含两条：

1. buy leg
   - `exchange = buy_exchange`
   - `side = buy`
2. sell leg
   - `exchange = sell_exchange`
   - `side = sell`

价格与数量首版可继续沿用当前 probe 的保守构造思路：

- 价格仍基于 ticker 派生保护价
- 数量仍基于 `target_quote_amount / reference_price`
- 精度裁剪仍由 adapter 完成

这意味着本次重点是“执行主链切换”，而不是彻底重写下单参数生成策略。

### 7.4 adapter 获取方式

当前 executor 在 preflight / account truth 后已经拿到：

- `execution_accounts_by_exchange`
- `credentials_by_exchange`
- `proxies_by_exchange`

因此本次建议 `RuntimeTradeExecutionService` 自己持有 `session_factory`，并在运行时：

1. 按买卖交易所创建 session
2. 调用 `mark_ready()`
3. 为每个交易所创建 `ExchangeAdapter`
4. 组装成 `TradeExecutor(adapter_factory=...)`

完成后仍由 runtime service 负责关闭 adapter / session。

### 7.5 输出兼容策略

首版正式执行服务输出兼容当前 executor 下游依赖的最小集合：

- `ok`
- `execution_status`
- `filled_exchanges`
- `failed_exchanges`

其中：

- `execution_status = OPEN_HEDGED`
  - 当两腿都成功
- `execution_status = OPEN_PARTIAL`
  - 当至少一腿失败，但已有成功腿
- `execution_status = None`
  - 当两腿都失败或未形成有效执行结果

对于 richer result 字段：

- `buy_leg_status`
- `sell_leg_status`
- `buy_leg_error_code`
- `sell_leg_error_code`
- `buy_leg_error_detail`
- `sell_leg_error_detail`
- `failed_stage`

首版允许全部缺失，由 executor 事件 builder 自动兼容为 `None`。

### 7.6 对 `RedisOpportunityDispatcher` 的影响

[redis_flow.py](file:///d:/old/FuRunSystemV4/app/runtime/redis_flow.py)
中的 `RedisOpportunityDispatcher` 本次只做最小修改或不改语义。

允许保留当前结构：

- `__init__(self, spot_service)`
- `dispatch(...) -> await self.spot_service.run_task(...)`

但在 executor worker 中，传入的对象不再是 probe，而是 `RuntimeTradeExecutionService`。

这意味着：

- scanner / consumer 路径仍可继续接 `SpotArbitrageProbeService`
- executor 路径改接正式执行 service

本次不强行在命名上同步完成整轮接口清洗。

### 7.7 对 `WorkerApplication` 的影响

[worker_service.py](file:///d:/old/FuRunSystemV4/app/runtime/worker_service.py)
中：

- `spot_service` 仍保留给 scanner / consumer
- executor worker 新增单独的正式执行 service 注入

推荐形态：

- `spot_service: SpotArbitrageProbeService`
- `trade_execution_service: RuntimeTradeExecutionService`

这样可以避免：

- scanner / consumer / executor 三种 worker 被迫共享同一个 service 实例语义

### 7.8 错误处理

本次错误处理规则如下：

- runtime service 内部任一腿下单失败：
  - 由 `TradeExecutor` 返回 `OPEN_PARTIAL` 或失败摘要
- runtime service 自己的 session 初始化失败、adapter 创建失败：
  - 直接抛异常，由现有 executor 主链按失败路径处理
- runtime service 返回结果缺少 richer result 字段：
  - 允许
  - execution summary 与 `executor.execution_result` 仍按最小字段工作

首版不在正式执行服务里补：

- 撤单
- 最终查单
- cancel failure / final fetch failure 细粒度阶段语义

这些仍属于后续增强范围。

## 8. 数据流

替换后的目标数据流如下：

1. executor 拉取节点任务
2. preflight / control guard / account truth 正常通过
3. `RedisOpportunityDispatcher.dispatch(...)` 被调用
4. 对 executor worker 来说，dispatcher 内部实际调用的是 `RuntimeTradeExecutionService.run_task(...)`
5. runtime service 构造 `ExecutionTask`
6. runtime service 调用 `TradeExecutor.execute_open(...)`
7. 返回最小兼容结果对象
8. executor 继续按现有逻辑：
   - 发 `executor.execution_result`
   - 写 execution summary
   - 发 `executor.task.processed / failed`

## 9. 测试策略

本次至少补以下测试：

### 9.1 `tests/test_trade_execution_service.py`

新增 focused test：

- 两腿都成功时：
  - 返回 `OPEN_HEDGED`
  - `filled_exchanges` 包含买卖所
- 单腿失败时：
  - 返回 `OPEN_PARTIAL`
  - `failed_exchanges` 包含失败交易所

### 9.2 `tests/test_worker_service.py`

补一个接线测试：

- `build_executor_worker()` 使用的是正式执行 service
- scanner / consumer 仍保留 probe service

### 9.3 `tests/test_live_workers.py`

补/改 executor 主链兼容测试：

- 正式执行 service 返回最小结果对象时，仍会：
  - 发 `executor.execution_result`
  - 写 execution summary
- preflight 失败路径仍不回归

### 9.4 聚合回归

至少覆盖：

- `tests/test_live_workers.py`
- `tests/test_worker_service.py`
- `tests/test_trade_execution_service.py`

并执行：

- `python -m py_compile` 检查新增/修改文件

## 10. 验收标准

满足以下条件即可视为完成：

1. executor worker 不再注入 `SpotArbitrageProbeService`
2. executor worker 改为注入正式 trade execution service
3. `TradeExecutor` 真正进入 runtime 主链
4. execution summary 写回不回归
5. `executor.execution_result` 事件不回归
6. scanner / consumer 仍可继续使用 probe
7. 相关测试通过

## 11. 后续演进

本次完成后，后续可以继续沿以下方向推进，但不属于本次范围：

- 为正式执行服务补 richer result 细粒度阶段字段
- 把 `RedisOpportunityDispatcher` 的 service 接口正式抽象统一
- 正式引入 close 执行、repair 执行
- 扩展 `TradeExecutor` 到更完整的订单事实与恢复语义
