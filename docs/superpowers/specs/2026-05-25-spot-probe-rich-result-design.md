# Spot Probe 真实执行结果增强设计

## 1. 文档目标

本文档定义如何增强 `spot_arbitrage_probe` 的真实执行返回，让 executor 在消费真实执行结果时，不再只拿到“是否部分成功”和少量订单 ID，而是能拿到更适合排障、复盘与稳定映射的腿级结果摘要。

本次目标是在不统一 `TradeExecutor` 与 `SpotArbitrageProbeService` 两套执行模型、不引入 execution attempt 明细表、也不直接把订单级原始响应整包写入数据库的前提下，为 `SpotArbitrageTaskResult` 增加：

- 买腿 / 卖腿执行状态
- 稳定短码形式的腿级失败码
- 可选的腿级失败 detail
- 统一的失败阶段 `failed_stage`

这样 executor 和运维链路就可以区分：

- 哪一腿失败
- 失败发生在创建单、撤单还是最终状态查询
- `OPEN_PARTIAL` 下具体是“卖腿没下成”还是“后处理阶段失败”

## 2. 范围

本次只做以下能力：

- 扩展 `SpotArbitrageTaskResult` 的真实执行返回结构
- 定义腿级状态字段与失败阶段字段
- 定义 `spot_arbitrage_probe.run_task()` 各阶段如何填充 richer result
- 定义 executor 如何继续消费 richer result 且不回归已有任务摘要写回
- 定义测试与验收标准

本次不做以下能力：

- 不统一 `TradeExecutor.ExecutionResult` 与 `SpotArbitrageTaskResult`
- 不新建共享 execution result schema 层
- 不新建 execution attempt / order detail 表
- 不把交易所完整原始响应落库
- 不自动执行 repair
- 不改变当前任务摘要字段集合

## 3. 背景与现状

当前系统已经具备以下基础：

- [spot_arbitrage_probe.py](file:///d:/old/FuRunSystemV4/app/runtime/spot_arbitrage_probe.py) 中 `SpotArbitrageTaskResult` 已包含：
  - `execution_status`
  - `filled_exchanges`
  - `failed_exchanges`
  - `buy_order_id`
  - `sell_order_id`
  - `buy_final_status`
  - `sell_final_status`
- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py) 中 executor 已能根据：
  - `execution_status`
  - `filled_exchanges`
  - `failed_exchanges`
  生成任务摘要写回
- [risk_manager.py](file:///d:/old/FuRunSystemV4/app/trading/risk_manager.py) 已能在 `OPEN_PARTIAL` 下生成 repair 建议
- 主服务器远端验证已通过：
  - `OPEN_HEDGED`
  - `OPEN_PARTIAL`
  - preflight 失败不误写 execution summary

但当前真实执行返回仍然过于粗糙：

1. `OPEN_PARTIAL` 只能表达“部分成功”，不能表达失败发生在哪个阶段
2. 只能知道哪边交易所失败，不能知道是 `create_order`、`cancel_order` 还是 `fetch_order` 失败
3. `message` 常常是异常文本，不利于稳定聚合和排障
4. executor 拿到了 richer task summary，但真实执行层仍缺乏更细的稳定结构

因此，本次需要增强 `SpotArbitrageTaskResult`，把真实执行返回从“够用”提升到“可诊断”。

## 4. 问题定义

如果继续保持现状，会有以下问题：

1. `OPEN_PARTIAL` 的运维价值有限，只能知道有一腿失败，无法知道失败发生在哪一步
2. 真实执行阶段的问题只能依赖 `message` 文本排查，难以做稳定 reason 聚合
3. 后续若要把 repair 输入进一步结构化，没有足够稳定的腿级上下文
4. 当前 execution summary 已经能写回任务表，但缺少更细的真实执行来源信息支撑

因此，本次需要在真实执行返回层面补上“腿级状态 + 失败阶段 + 稳定失败码”。

## 5. 设计目标

本次设计满足以下目标：

1. `SpotArbitrageTaskResult` 提供稳定的腿级执行状态
2. 能明确区分失败发生在 `create`、`cancel`、`fetch_final` 等阶段
3. executor 继续兼容现有 execution summary 写回路径
4. 不把整段异常文本当作唯一结构化信息
5. 不扩大到统一所有执行模型

## 6. 方案比较

### 6.1 方案 A：只增强 `spot_arbitrage_probe` 的真实返回

做法：

- 在 `SpotArbitrageTaskResult` 中增加腿级状态与失败阶段字段
- 在 `run_task()` 里按执行阶段持续更新这些字段

优点：

- 最贴近当前真实执行路径
- 改动面集中
- 最适合接在已完成的 execution summary 之后继续演进

缺点：

- 仍然与 `TradeExecutor.ExecutionResult` 并存两套结果模型

### 6.2 方案 B：统一 `TradeExecutor` 与 `spot_arbitrage_probe` 结果模型

做法：

- 设计一套共享 result schema
- 让两边都往同一对象靠拢

优点：

- 长期结构更统一

缺点：

- 范围明显更大
- 需要重做更多测试和消费边界
- 不适合紧接当前真实执行链路做小步增强

### 6.3 方案 C：继续只增强 `message`

做法：

- 不新增结构化字段
- 只让 `message` 更规范

优点：

- 改动最小

缺点：

- 本质仍是文本协议
- 不利于稳定聚合、断言与后续映射

### 6.4 推荐方案

本次采用方案 A。

原因：

- 它最符合当前“小步增强真实执行返回”的目标
- 它不会破坏刚刚完成的 execution summary 写回链
- 它为后续如需统一结果模型保留了演进空间

## 7. 核心设计

### 7.1 `SpotArbitrageTaskResult` 字段扩展

在现有字段基础上，新增以下 richer result 字段：

- `buy_leg_status`
- `sell_leg_status`
- `buy_leg_error_code`
- `sell_leg_error_code`
- `buy_leg_error_detail`
- `sell_leg_error_detail`
- `failed_stage`

字段语义如下：

- `buy_leg_status` / `sell_leg_status`
  - 表达对应腿当前走到的最远阶段
- `buy_leg_error_code` / `sell_leg_error_code`
  - 表达对应腿的稳定失败短码
- `buy_leg_error_detail` / `sell_leg_error_detail`
  - 保留可读 detail，用于日志与人工排障
- `failed_stage`
  - 表达整个任务第一次失败发生在哪个阶段

### 7.2 腿级状态枚举

首版不引入正式 enum 类，直接约定以下字符串状态：

- `not_started`
- `create_submitted`
- `create_failed`
- `created`
- `cancel_submitted`
- `cancel_failed`
- `cancelled`
- `final_fetch_failed`
- `final_fetched`

说明：

- `created` 表示下单成功拿到订单 ID
- `cancelled` 表示撤单动作成功
- `final_fetched` 表示已完成最终状态查询
- `*_failed` 直接表达对应阶段失败

### 7.3 失败阶段枚举

首版统一使用以下 `failed_stage`：

- `create_buy`
- `create_sell`
- `cancel_buy`
- `cancel_sell`
- `fetch_final_buy`
- `fetch_final_sell`

若整条链路成功，则：

- `failed_stage = NULL`

### 7.4 稳定失败码

首版只要求为真实执行链路提供轻量稳定短码，不追求覆盖所有交易所错误类型。

建议形式：

- `buy_create_failed`
- `sell_create_failed`
- `buy_cancel_failed`
- `sell_cancel_failed`
- `buy_final_fetch_failed`
- `sell_final_fetch_failed`

要求：

- `*_error_code` 使用稳定短码
- `*_error_detail` 保留原始异常文本摘要
- 不再让 `message` 承担唯一的结构化错误表达职责

### 7.5 `run_task()` 数据流

`SpotArbitrageProbeService.run_task()` 的状态推进建议如下：

1. 初始时两腿状态都设为 `not_started`
2. 创建买腿前：
   - `buy_leg_status = create_submitted`
3. 买腿创建成功后：
   - `buy_leg_status = created`
4. 创建卖腿前：
   - `sell_leg_status = create_submitted`
5. 卖腿创建成功后：
   - `sell_leg_status = created`
6. 撤单前：
   - 对应腿进入 `cancel_submitted`
7. 撤单成功后：
   - 对应腿进入 `cancelled`
8. 最终状态查询成功后：
   - 对应腿进入 `final_fetched`
9. 任一步失败时：
   - 设置对应腿 `*_error_code`
   - 设置对应腿 `*_error_detail`
   - 设置 `failed_stage`
   - 返回当前已经累积的 richer result

### 7.6 成功与失败映射

#### 全成功

若两腿都走完整条链：

- `execution_status = OPEN_HEDGED`
- `buy_leg_status = final_fetched`
- `sell_leg_status = final_fetched`
- `failed_stage = NULL`
- `buy_leg_error_code = NULL`
- `sell_leg_error_code = NULL`

#### 买腿成功、卖腿创建失败

若买腿成功，但卖腿 `create_order` 失败：

- `execution_status = OPEN_PARTIAL`
- `buy_leg_status = created` 或更后状态
- `sell_leg_status = create_failed`
- `failed_stage = create_sell`
- `sell_leg_error_code = sell_create_failed`

#### 下单成功、后处理失败

若两边单都创建成功，但后续 `cancel/fetch_final` 某一步失败：

- 保留已知成功腿状态
- 失败腿进入对应 `*_failed`
- `failed_stage` 明确指向具体阶段
- `execution_status` 继续反映真实执行结果，不把所有失败都压成同一种无上下文状态

### 7.7 与任务摘要层的关系

本次不要求把所有 richer result 字段都落库到 `arbitrage_tasks`。

关系分层如下：

- `SpotArbitrageTaskResult`
  - 负责承载更细的真实执行上下文
- `ArbitrageTask`
  - 继续承载任务级 execution summary
- executor consumer
  - 继续从 richer result 中提取：
    - `execution_status`
    - `filled_exchanges`
    - `failed_exchanges`
    - repair 信息

因此：

- 本轮重点是增强真实返回
- 不是立刻扩大任务表字段范围

### 7.8 与 `TradeExecutor` 的关系

本次不修改 [executor.py](file:///d:/old/FuRunSystemV4/app/trading/executor.py) 中的
`ExecutionResult`。

原因：

- 当前真实执行链主要走 `SpotArbitrageProbeService`
- 强行统一两套模型会让范围明显扩大
- 先把真实返回做扎实，再决定是否抽共享模型更稳妥

## 8. 错误处理

错误处理规则如下：

- 任一腿 `create_order` 失败：
  - 直接返回 richer result
  - 标出失败腿状态、错误码与 `failed_stage`
- `cancel_order` 失败：
  - 标出对应 `cancel_failed`
  - 不丢失已知的订单 ID 和已成功腿状态
- `fetch_order` 最终查询失败：
  - 标出对应 `final_fetch_failed`
  - 不覆盖先前已知的成功创建信息
- `message`
  - 继续保留
  - 但不再作为唯一结构化错误来源

## 9. 测试策略

本次至少补以下测试：

### 9.1 `tests/test_spot_arbitrage_probe.py`

- 两腿完整成功：
  - 断言 `buy_leg_status = final_fetched`
  - 断言 `sell_leg_status = final_fetched`
  - 断言 `failed_stage is None`
- 买腿成功、卖腿创建失败：
  - 断言 `execution_status = OPEN_PARTIAL`
  - 断言 `sell_leg_status = create_failed`
  - 断言 `failed_stage = create_sell`
  - 断言 `sell_leg_error_code = sell_create_failed`
- 两腿都创建成功，但某一腿撤单失败：
  - 断言对应 `cancel_failed`
  - 断言 `failed_stage`
- 两腿都创建成功，但某一腿最终状态查询失败：
  - 断言对应 `final_fetch_failed`
  - 断言 `failed_stage`

### 9.2 `tests/test_live_workers.py`

- executor 消费 richer result 后，现有 execution summary 逻辑不回归
- `OPEN_HEDGED` 仍正常写成功任务摘要
- `OPEN_PARTIAL` 仍正常写 repair 摘要
- richer result 中新增字段不影响 preflight 失败路径

## 10. 验收标准

满足以下条件即可视为完成：

1. `SpotArbitrageTaskResult` 新增 richer result 字段
2. `run_task()` 能按真实阶段更新腿级状态
3. 至少能稳定区分：
   - `create_sell`
   - `cancel_*`
   - `fetch_final_*`
4. executor 继续兼容现有 execution summary 写回
5. 相关测试通过

## 11. 后续演进

本次完成后，后续可以继续沿以下方向推进，但不属于本次范围：

- 统一 `TradeExecutor` 与 `SpotArbitrageTaskResult` 的共享结果模型
- 把部分 richer result 摘要字段继续下沉到任务表
- 引入 execution attempt 明细表
- 为 repair 自动执行补更丰富的输入上下文
