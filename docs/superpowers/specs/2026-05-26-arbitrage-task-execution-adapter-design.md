# 套利任务执行适配设计

## 1. 文档目标

本文档定义 `B1-3` 的目标：让 `B1-2` 生成的套利调度记录，正式进入真实执行链。

本次同时覆盖：

- `OPEN`
- `CLOSE`

两类套利任务。

但本次接入方式不是重写底层执行器，而是在当前稳定的现货执行链之上，新增一层：

- `ArbitrageExecutionAdapter`

由它负责把套利任务语义翻译成现有执行器可以理解的最小输入。

本轮完成标准是：

- 套利调度记录可以被选中执行
- `OPEN` 可以触发真实开仓执行
- `CLOSE` 可以触发真实平仓执行
- 执行结果可以正确回写到 `ArbitrageTask`

本轮不要求：

- 把 `RuntimeTradeExecutionService` 改造成原生套利执行器
- 把 `RuntimeRepairExecutionService` 扩展成完整套利 repair 系统
- 补齐完整持仓账本、合约腿订单管理或复杂对账

## 2. 范围

本次只做以下能力：

- 为套利任务新增独立执行适配层
- 支持 `OPEN` 与 `CLOSE` 两类任务进入真实执行
- 将套利任务映射为现有执行器可理解的输入
- 将执行结果回写到 `ArbitrageTask`
- 对最小可兼容的失败结果沿用当前 repair 入口
- 保持现有 spot 执行主链不回归

本次不做以下能力：

- 不重写现有 `RuntimeTradeExecutionService` 的内部执行模型
- 不要求底层执行器显式理解 `spot + derivative` 新语义
- 不新增套利专用 repair planner
- 不引入完整持仓恢复、仓位核验与对账体系
- 不把 executor / repair 的告警系统整轮重构

## 3. 背景与现状

当前系统已经形成三层能力：

### 3.1 `B1-1` 已形成机会层

当前已存在：

- `ArbitrageOpportunity`
- `arb:zset:open`
- `arb:zset:close`
- `stream:opportunities`

### 3.2 `B1-2` 已形成调度层

当前已存在：

- `RedisArbitrageConsumer`
- `RedisArbitrageTaskDispatcher`
- `arb_dispatcher` worker role
- `OPEN` / `CLOSE` 型 `ArbitrageTask` 调度记录

也就是说，当前系统已经可以：

- 消费套利机会
- 筛选用户与策略
- 路由
- 创建套利任务记录

### 3.3 当前缺口在执行层

当前真实执行仍然依赖：

- `RuntimeTradeExecutionService`
- `RuntimeRepairExecutionService`

它们的输入语义仍是：

- `buy_exchange`
- `sell_exchange`
- `symbol`
- `target_quote_amount`

以及现货双腿方向。

而 `ArbitrageTask` 当前表达的是：

- `task_type = open/close`
- `spot_exchange`
- `derivative_exchange`
- `target_notional`

这意味着：

- 当前系统可以生成套利任务
- 但还不能把套利任务安全、明确地接入真实执行

## 4. 问题定义

如果不做这一步，系统会继续存在四个问题：

### 4.1 调度记录停留在数据库，无法形成真实闭环

`ArbitrageTask` 当前只停留在：

- `CREATED`
- `DISPATCHED`

等调度语义层。

若不能进入真实执行，则 `B1` 仍然只完成“机会被看见”，没有完成“任务被执行”。

### 4.2 直接把套利任务塞给现有 executor 会产生语义错位

现有 executor 假设：

- `buy_exchange` 是买入腿
- `sell_exchange` 是卖出腿

而套利任务的方向由：

- `task_type`
- `spot_exchange`
- `derivative_exchange`

共同决定。

若没有适配层，`OPEN` 和 `CLOSE` 的腿方向容易被错误翻译。

### 4.3 `OPEN` 与 `CLOSE` 的执行方向不同

`OPEN` 的最小执行语义是：

- `spot_exchange -> buy`
- `derivative_exchange -> sell`

`CLOSE` 的最小执行语义则相反：

- `spot_exchange -> sell`
- `derivative_exchange -> buy`

如果不显式建立方向映射，系统会把平仓任务错误当作开仓任务执行。

### 4.4 若本轮同时重做 repair，范围会失控

如果在 `B1-3` 同时重做：

- 套利执行
- 套利 repair
- 持仓核验
- 告警与收口

本轮会从“执行接入”膨胀成“完整套利生命周期重构”。

## 5. 设计目标

本次设计满足以下目标：

1. 让 `OPEN` 和 `CLOSE` 两类套利任务都能进入真实执行
2. 通过一层适配器完成任务方向翻译
3. 保持当前 spot executor 主链不回归
4. 让执行结果稳定回写到 `ArbitrageTask`
5. 把 repair 控制在最小兼容范围内，而不是一轮重构

## 6. 方案比较

### 6.1 方案 A：新增套利执行适配层，OPEN+CLOSE 都接入真实执行，推荐

做法：

- 新增 `ArbitrageExecutionAdapter`
- `OPEN` / `CLOSE` 都通过适配层翻译为现有执行器输入
- 执行后回写套利任务状态
- repair 只做最小兼容

优点：

- 两类任务都进入真实执行
- 改动范围集中
- 不破坏现有 spot executor 主链

缺点：

- 需要维护一层显式映射逻辑

### 6.2 方案 B：直接扩现有 executor，让它原生理解套利语义

做法：

- 直接修改 `RuntimeTradeExecutionService` 与相关 consumer
- 让 executor 原生理解 `task_type/open/close`

优点：

- 从表面上看更“一步到位”

缺点：

- 会动到当前已打稳的执行语义
- 容易把现有 spot 主链一起带回归
- 风险最高

### 6.3 方案 C：把套利任务直接写进现有 `spot_exec_tasks`，让旧 executor 硬吃

做法：

- 只改任务发布，不改执行边界

优点：

- 实现快

缺点：

- payload 语义错位最严重
- 后续返工概率最高

### 6.4 推荐方案

本次采用方案 A。

原因：

- 它满足你明确要求的 `OPEN+CLOSE 都执行`
- 又能把改动收敛在执行适配层
- 最符合当前“先打通最小真实闭环，再补完整生命周期”的节奏

## 7. 核心设计

### 7.1 新增 `ArbitrageExecutionAdapter`

本次新增独立适配层：

- `ArbitrageExecutionAdapter`

职责如下：

1. 读取 `ArbitrageTask`
2. 根据 `task_type` 判断方向
3. 翻译为现有执行器输入
4. 调用现有执行服务
5. 将结果回写到 `ArbitrageTask`
6. 必要时触发最小 repair 兼容路径

本次不允许直接把套利任务对象塞进 `RuntimeTradeExecutionService`。

### 7.2 与现有执行器的关系

本次明确保持底层执行器职责不变：

- `RuntimeTradeExecutionService`
  - 继续接收 `buy_exchange/sell_exchange/symbol/target_quote_amount`
- `RuntimeRepairExecutionService`
  - 继续接收当前 repair 所理解的最小输入

适配层负责把新任务语义转换成旧输入语义。

### 7.3 OPEN 映射

`OPEN` 任务的最小执行映射如下：

- `spot_exchange -> buy_exchange`
- `derivative_exchange -> sell_exchange`
- `symbol -> symbol`
- `target_notional -> target_quote_amount`

也就是说，`OPEN` 的执行方向是：

- 买现货腿
- 卖衍生品腿

本轮不要求底层执行器显式知道“这是现货腿还是合约腿”，只要求适配后的方向是稳定、一致的。

### 7.4 CLOSE 映射

`CLOSE` 任务的最小执行映射如下：

- `spot_exchange -> sell_exchange`
- `derivative_exchange -> buy_exchange`
- `symbol -> symbol`
- `target_notional -> target_quote_amount`

也就是说，`CLOSE` 的执行方向与 `OPEN` 相反。

本轮的重要边界是：

- `CLOSE` 能进入真实执行
- 但 closeable 判断仍然以前一轮 `B1-2` 的调度结果为准
- executor 阶段不重新做一遍 closeable 决策

### 7.5 执行入口

本次推荐新增独立于现有 spot executor consumer 的执行入口。

做法优先级如下：

1. 新增套利任务 execution worker / consumer，推荐
2. 或在现有 executor consumer 上增加一层明确分支

本次推荐独立入口的原因是：

- 套利任务和 spot 任务的 payload 语义已不同
- 若继续混在一个 consumer，会快速放大复杂度

### 7.6 任务选择边界

本次执行入口只消费：

- 已由 `arb_dispatcher` 创建
- 并被标记为可执行状态的套利任务

本轮不要求同时重构“调度记录如何转为执行待处理状态”的完整生命周期，但要求至少有一层稳定边界，避免：

- 重复执行同一任务
- 未筛选完成的任务被 executor 抢跑

### 7.7 结果回写

执行完成后，必须把以下结果回写到 `ArbitrageTask`：

- `status`
- `execution_status`
- `filled_exchanges_json`
- `failed_exchanges_json`
- `started_at`
- `finished_at`

语义要求如下：

- 成功：
  - `status = SUCCEEDED`
  - 保留真实 `execution_status`
- 失败：
  - `status = FAILED`
  - 保留失败边
- 若进入最小 repair：
  - 可使用 `REPAIRING`
  - 或沿用当前系统已存在的最小 repair 中间态

### 7.8 最小状态机

本次建议 `ArbitrageTask` 至少沿用以下状态边界：

- `CREATED`
- `DISPATCHED`
- `RUNNING`
- `SUCCEEDED`
- `FAILED`
- 必要时 `REPAIRING`

`OPEN` 与 `CLOSE` 共用同一套大状态机。

两者的区别主要体现在：

- `task_type`
- 方向映射
- 执行结果解释

### 7.9 最小 repair 兼容

本轮 repair 只做最小兼容，不做完整套利 repair 系统。

兼容原则如下：

1. 若执行结果能够映射到现有 repair 所能理解的单边失败语义，则允许沿用当前 repair 入口
2. 若执行结果不适合当前 repair 模型，则直接停在：
   - `FAILED`
   - 或 `MANUAL_REQUIRED`

本次不要求：

- 套利专用 repair planner
- 持仓感知 repair
- 合约腿专用补偿策略

### 7.10 与 B1-2 的关系

`B1-2` 负责：

- 从机会到调度记录

`B1-3` 负责：

- 从调度记录到真实执行

两轮之间的分工必须清楚：

- 调度层负责决定“谁该执行”
- 执行层负责决定“怎么执行”

本轮不再把用户筛选、closeable 判断重新塞回 executor。

## 8. 数据流

本次目标数据流如下：

1. `arb_dispatcher` 已创建套利任务记录
2. 套利执行入口读取可执行任务
3. `ArbitrageExecutionAdapter` 读取任务并判断 `task_type`
4. 若 `OPEN`：
   - 映射为开仓方向执行输入
5. 若 `CLOSE`：
   - 映射为平仓方向执行输入
6. 调用 `RuntimeTradeExecutionService`
7. 将执行结果回写到 `ArbitrageTask`
8. 若结果满足最小 repair 兼容条件：
   - 进入最小 repair 路径
9. 否则停在真实终态

## 9. 错误处理

### 9.1 非法任务类型

若 `task_type` 不是：

- `open`
- `close`

则：

- 稳定拒绝执行
- 回写失败原因

### 9.2 方向映射失败

若任务缺少以下任一字段：

- `spot_exchange`
- `derivative_exchange`
- `symbol`
- `target_notional`

则：

- 不进入执行器
- 直接回写任务失败

### 9.3 执行器失败

若底层执行器返回失败结果，则：

- 回写失败边
- 再决定是否进入最小 repair

### 9.4 repair 不兼容

若失败形态不适合当前 repair 模型，则：

- 不强行补救
- 直接停在 `FAILED` 或 `MANUAL_REQUIRED`

### 9.5 新链路不得污染旧链

套利任务执行失败时：

- 不应污染旧的 `spot` 执行事件流
- 不应改变既有 `spot` 任务语义

## 10. 测试策略

本次至少补以下 focused tests：

### 10.1 适配器映射

- `OPEN` 任务被正确映射为：
  - `buy=spot_exchange`
  - `sell=derivative_exchange`
- `CLOSE` 任务被正确映射为相反方向

### 10.2 执行结果回写

- 成功执行后回写：
  - `SUCCEEDED`
  - `execution_status`
  - `filled_exchanges_json`
- 失败执行后回写：
  - `FAILED`
  - `failed_exchanges_json`

### 10.3 repair 最小兼容

- 可兼容的单边失败能进入最小 repair
- 不兼容的失败停在终态，不误触发 repair

### 10.4 并存回归

- 旧 spot executor 相关测试不回归
- 新套利执行链与旧 spot 执行链可以并存

## 11. 验收标准

满足以下条件即可视为本次完成：

1. `OPEN` 套利任务可进入真实执行
2. `CLOSE` 套利任务可进入真实执行
3. 存在明确的 `ArbitrageExecutionAdapter` 边界
4. 执行结果可稳定回写到 `ArbitrageTask`
5. 旧 spot 执行主链不回归

## 12. 后续演进

本次完成后，后续可以继续推进，但不属于本次范围：

- 套利专用 repair planner
- 持仓感知 repair 与平仓验证
- 合约腿更真实的执行建模
- 套利任务的完整生命周期与告警语义升级
