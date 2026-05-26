# 套利自动恢复策略升级设计

## 1. 文档目标

本文档定义 `B1-5D/E` 的目标：在已具备自动恢复底座与恢复事件链的前提下，把套利自动恢复从“统一规则”升级为“按失败类型分流、按等级递增冷却、按终局降噪告警”的更完整生产闭环。

本次覆盖：

- 失败分类细化
- 分类到恢复动作的映射
- 递增 / 分层 cooldown
- 恢复事件最小降噪与去重

本轮不做：

- 不引入人工审批流
- 不新增全局 orchestrator
- 不做交易所级硬熔断
- 不重写 spot 链执行逻辑
- 不引入 metrics / tracing / 外部工单系统

## 2. 范围

本次只做以下能力：

- 在 `ArbitrageExecutionTaskConsumer` 中把 executor 失败与 repair 失败先归类，再交给恢复策略决策
- 将恢复决策从单一粗粒度 `failure_reason` 升级为“失败分类 -> 恢复动作”的映射
- 将 cooldown 从固定窗口升级为“按失败类型的基础窗口 + 按重试次数递增”的组合规则
- 为 `arb.recovery.exhausted` 补最小去重 / 降噪，避免自动恢复终局重复刷飞书

本次不做以下能力：

- 不改 `TaskRepository` 的主职责边界，repository 仍只负责状态真相
- 不新增新的任务表、恢复表、工单表
- 不修改 `B1-5C` 已有的 `arb.recovery.retry_scheduled` / `cooldown_started` / `exhausted` 事件名
- 不扩展为全局交易所级或用户级熔断系统

## 3. 背景与现状

截至当前，套利链已经具备三层能力：

- `B1-5A`
  - 套利链已有独立 `arb.*` 运行事件与分层告警
- `B1-5B`
  - 套利失败后支持任务级自动恢复：
    - `RETRY_PENDING`
    - `COOLDOWN`
    - `EXHAUSTED`
- `B1-5C`
  - 自动恢复动作已有独立 `arb.recovery.*` 事件

这意味着系统已经能做到：

- executor / repair 失败后自动进入恢复链
- 恢复动作可见
- 冷却中的任务不会被 claim

但现状仍有四个明显短板：

- 失败分类过粗，恢复动作过于“一刀切”
- cooldown 固定窗口过于简单
- 终局告警容易重复，噪音控制不足
- 后续想继续升级自动恢复时，缺少清晰的“分类 -> 动作 -> 冷却 -> 告警”分层

所以 `B1-5D/E` 的目标不是再补一个新链路，而是把已有自动恢复链升级成更接近生产的智能策略链。

## 4. 问题定义

### 4.1 当前失败原因过粗

现在典型失败原因仍然集中在：

- `execution_failed_non_repairable`
- `repair_failed_manual_required`

这不足以表达：

- 网络抖动
- 路由暂不可用
- 交易所拒单
- repair 执行失败
- 未知硬失败

结果是：

- 不同失败会走近似相同的恢复路径
- 恢复策略无法有针对性地升级

### 4.2 当前恢复动作不够分层

当前自动恢复虽然支持：

- retry
- cooldown
- exhausted

但更多依赖于重试次数，而不是失败性质。

结果是：

- 短时网络问题和交易所拒单可能得到相近处理
- 恢复链不够“聪明”

### 4.3 当前 cooldown 固定窗口过于粗糙

现在的 cooldown 是固定秒数。

这会导致：

- 临时网络抖动等待过长
- 交易所拒单等待过短
- 随失败次数递增的恢复节奏无法表达

### 4.4 当前恢复终局告警存在重复风险

`arb.recovery.exhausted` 是需要人工关注的高优先级终局事件。

但如果同一任务 / 同一交易对短时间内重复进入 exhausted：

- 飞书噪音会明显上升
- 会稀释真正重要的终局事件

因此本次必须同时解决：

- 失败分类
- 恢复动作映射
- cooldown 升级
- exhausted 降噪

## 5. 设计目标

本次设计满足以下目标：

1. executor 失败与 repair 失败先被分类，再进入恢复决策
2. 不同失败分类可以映射到不同恢复动作
3. cooldown 不再是固定窗口，而是按失败类型和重试次数递增
4. `UNKNOWN_HARD_FAILURE` 不会被无意义重试，直接进入终局
5. `arb.recovery.exhausted` 支持最小去重，降低飞书噪音
6. `B1-5A`、`B1-5B`、`B1-5C` 的既有行为不回归

## 6. 方案比较

### 6.1 方案 A：一次补齐分类、映射、cooldown、降噪，推荐

做法：

- 增加失败分类层
- 增加分类到动作的映射层
- 增加递增 cooldown
- 对 exhausted 做最小去重

优点：

- 一次形成完整自动恢复升级闭环
- 与现有 `B1-5A/B/C` 衔接最顺
- 后续再扩展时边界清晰

缺点：

- 本轮范围比过去单小闭环大

### 6.2 方案 B：只做失败分类和动作映射

优点：

- 改动更小

缺点：

- 很快还要再补 cooldown 升级和告警降噪
- 会继续拆成两轮，整体效率不高

### 6.3 方案 C：直接做交易所级熔断 / 全局调度器

优点：

- 长期看能力最强

缺点：

- 范围过大
- 会引入新的全局状态与调度复杂度
- 不适合作为当前连续闭环中的下一步

### 6.4 推荐方案

本次采用方案 A。

原因：

- 这最符合“相邻小闭环合并推进”的节奏
- 既能加快开发速度，又不会把范围扩到不可控
- 能直接把自动恢复链提升到更接近生产的形态

## 7. 核心设计

### 7.1 组件边界

本次仍保持清晰边界：

- `ArbitrageExecutionTaskConsumer`
  - 作为 executor / repair 失败的统一收口点
  - 负责调用失败分类和恢复决策
- `live_workers.py` 内的恢复策略层
  - 增加两个轻量单元：
    - `classify_arbitrage_failure(...)`
    - `decide_arbitrage_recovery(...)`
  - 前者回答“这是什么失败”
  - 后者回答“这个失败该怎么恢复”
- `TaskRepository`
  - 仍只负责最终状态真相
  - 不承担策略判断
- `alerting.py`
  - 继续负责恢复事件外显与降噪
  - 不承担恢复策略本身

### 7.2 失败分类模型

本次推荐先收敛为 5 类：

- `TRANSIENT_NETWORK`
- `TEMPORARY_ROUTE`
- `EXCHANGE_REJECTED`
- `REPAIR_FAILED`
- `UNKNOWN_HARD_FAILURE`

#### `TRANSIENT_NETWORK`

表示：

- 短时网络抖动
- 连接超时
- 临时连接中断

目标处理：

- 倾向快速重试

#### `TEMPORARY_ROUTE`

表示：

- 路由暂不可用
- 依赖资源短时不足
- 账户映射或执行资源暂不可用

目标处理：

- 短冷却后再试

#### `EXCHANGE_REJECTED`

表示：

- 交易所拒单
- 参数不接受
- 约束不满足

目标处理：

- 比普通临时失败更长冷却

#### `REPAIR_FAILED`

表示：

- repair 进入执行后仍未修复

目标处理：

- 第一轮先冷却
- 若再次失败，允许直接耗尽

#### `UNKNOWN_HARD_FAILURE`

表示：

- 无法归类但明显不适合继续自动尝试

目标处理：

- 直接终局

### 7.3 分类输入来源

本轮不引入复杂异常体系重构，而是基于现有可用信息做最小分类。

分类输入优先顺序：

1. repair 路径是否失败
2. 执行结果状态
3. 失败交易所列表
4. 现有 `failure_reason` / `reason` 文本
5. 异常文本中的关键字

推荐最小规则：

- repair 最终失败：
  - 优先分类为 `REPAIR_FAILED`
- 文本中含：
  - `timeout`
  - `connection`
  - `network`
  - `reset`
  - `temporarily unavailable`
  - 归为 `TRANSIENT_NETWORK`
- 文本中含：
  - `route`
  - `missing execution account`
  - `dispatcher region`
  - 归为 `TEMPORARY_ROUTE`
- 文本中含：
  - `reject`
  - `invalid`
  - `insufficient`
  - `reduce-only`
  - `order not accepted`
  - 归为 `EXCHANGE_REJECTED`
- 无法命中上述规则：
  - 归为 `UNKNOWN_HARD_FAILURE`

### 7.4 恢复动作映射

分类与恢复动作的最小映射如下：

- `TRANSIENT_NETWORK`
  - `RETRY_PENDING`
- `TEMPORARY_ROUTE`
  - `COOLDOWN`
- `EXCHANGE_REJECTED`
  - `COOLDOWN`
- `REPAIR_FAILED`
  - 首次 `COOLDOWN`
  - cooldown 后再次失败时 `EXHAUSTED`
- `UNKNOWN_HARD_FAILURE`
  - `EXHAUSTED`

这意味着恢复决策不再只看“重试次数”，而是同时看：

- 失败分类
- 当前重试次数
- 当前恢复状态

### 7.5 cooldown 分层规则

本轮不做复杂指数退避，但做可解释的“基础窗口 + 递增倍率”。

基础窗口建议：

- `TRANSIENT_NETWORK`
  - `0s` 或非常短窗口
- `TEMPORARY_ROUTE`
  - `60s`
- `EXCHANGE_REJECTED`
  - `300s`
- `REPAIR_FAILED`
  - `180s`
- `UNKNOWN_HARD_FAILURE`
  - 不进入 cooldown

倍率规则：

- 第 1 次失败：
  - 基础窗口 x `1`
- 第 2 次失败：
  - 基础窗口 x `2`
- 第 3 次及以上失败：
  - 基础窗口 x `3`

这允许系统做到：

- 轻微失败尽快恢复
- 重复失败逐步放慢节奏
- 不必一次引入真正复杂的指数退避

### 7.6 executor / repair 接入方式

`ArbitrageExecutionTaskConsumer` 的统一流程升级为：

1. executor 非可 repair 失败
   - 先分类
   - 再决策恢复动作
2. repair 失败
   - 同样先分类
   - 再决策恢复动作

这样 executor 与 repair 都共享：

- 失败分类规则
- 恢复动作映射
- cooldown 计算逻辑

避免出现两套恢复策略并存。

### 7.7 repository 接口边界

本次不要求 repository 学习新的策略知识。

repository 继续只接收最终动作：

- `mark_auto_recovery_retry(...)`
- `mark_auto_recovery_cooldown(...)`
- `mark_auto_recovery_exhausted(...)`

差别只在于：

- 上层传入的 `failure_reason`
- `cooldown_until`

会变得更有策略语义。

### 7.8 恢复事件与降噪

恢复事件仍沿用：

- `arb.recovery.retry_scheduled`
- `arb.recovery.cooldown_started`
- `arb.recovery.exhausted`

其中：

- `retry_scheduled`
  - 继续只写日志
- `cooldown_started`
  - 继续只写日志
- `exhausted`
  - 继续走飞书

本次新增最小降噪规则：

- 同一 `task_uuid + event_type` 在 dedupe 窗口内只发一次飞书

建议：

- `arb.recovery.exhausted` 的去重键优先用：
  - `event_type + task_uuid`

而不是沿用更粗的：

- `event_type + symbol + exchange`

原因：

- exhausted 是任务级终局
- 任务级去重更准确

### 7.9 与现有设计的关系

#### 与 `B1-5A`

- 保留现有 `arb.executor.*` / `arb.repair.*` 事件
- 不改 spot 链 observability

#### 与 `B1-5B`

- 保留自动恢复主状态：
  - `RETRY_PENDING`
  - `COOLDOWN`
  - `EXHAUSTED`
- 只升级策略决策层

#### 与 `B1-5C`

- 保留 `arb.recovery.*` 事件名
- 只升级它们的触发条件与终局降噪

## 8. 数据流

本次目标数据流如下：

1. executor 或 repair 失败
2. `classify_arbitrage_failure(...)` 给出失败分类
3. `decide_arbitrage_recovery(...)` 基于：
   - 失败分类
   - `retry_count`
   - `auto_recovery_status`
   决定动作
4. 若动作为 `RETRY_PENDING`
   - repository 重入队
   - 发 `arb.recovery.retry_scheduled`
5. 若动作为 `COOLDOWN`
   - 计算递增 `cooldown_until`
   - repository 写入冷却
   - 发 `arb.recovery.cooldown_started`
6. 若动作为 `EXHAUSTED`
   - repository 写终局失败
   - 发 `arb.recovery.exhausted`
   - `AlertRouter` 按任务级去重后发飞书

## 9. 错误处理

### 9.1 分类失败不能阻断主链

若失败分类本身报错：

- 不应让任务停留在不一致中间态

推荐处理：

- 回落为 `UNKNOWN_HARD_FAILURE`
- 直接进入 `EXHAUSTED`

### 9.2 cooldown 计算必须稳定

cooldown 计算必须只依赖：

- 失败分类
- `retry_count`
- 固定倍率规则

避免：

- 隐式依赖 worker 局部状态

### 9.3 exhausted 去重不能吞掉首个终局事件

去重只应用于重复 exhausted 飞书。

必须保证：

- 同一任务第一次进入 exhausted 时一定能发出去

### 9.4 分类与动作必须可解释

分类和动作映射必须能在日志 / payload 中看出来。

至少要保留：

- `failure_reason`
- 失败分类
- 最终恢复动作

方便后续排障和策略升级。

## 10. 测试策略

本次至少补以下 focused tests：

### 10.1 失败分类

- 网络类失败 -> `TRANSIENT_NETWORK`
- 路由类失败 -> `TEMPORARY_ROUTE`
- 交易所拒单 -> `EXCHANGE_REJECTED`
- repair 失败 -> `REPAIR_FAILED`
- 未命中规则 -> `UNKNOWN_HARD_FAILURE`

### 10.2 恢复动作映射

- `TRANSIENT_NETWORK` -> `RETRY_PENDING`
- `TEMPORARY_ROUTE` -> `COOLDOWN`
- `EXCHANGE_REJECTED` -> `COOLDOWN`
- `REPAIR_FAILED` 首次 -> `COOLDOWN`
- `REPAIR_FAILED` 再次 -> `EXHAUSTED`
- `UNKNOWN_HARD_FAILURE` -> `EXHAUSTED`

### 10.3 cooldown 计算

- 不同分类得到不同基础窗口
- 重试次数递增时窗口变大
- cooldown 到期后任务仍能再次 claim

### 10.4 exhausted 降噪

- 同一任务的 exhausted 飞书在 dedupe 窗口内不会重复发送
- 不同任务的 exhausted 不会被错误吞掉

### 10.5 并存回归

- `B1-5A` 旧套利事件不回归
- `B1-5B` auto recovery 主状态不回归
- `B1-5C` recovery 事件不回归
- 旧 spot 链执行与告警行为不回归

## 11. 验收标准

满足以下条件即可视为本次完成：

1. executor / repair 失败都能先被分类
2. 不同失败分类映射到不同恢复动作
3. cooldown 能按分类与重试次数递增
4. `UNKNOWN_HARD_FAILURE` 直接进入 exhausted
5. `arb.recovery.exhausted` 能按任务级去重
6. 既有 `B1-5A/B/C` 行为不回归

## 12. 后续演进

本次完成后，后续可以继续推进，但不属于本次范围：

- 交易所级熔断
- 用户级恢复抑制
- 更复杂的指数退避
- 恢复策略配置中心
- metrics / traces / 外部工单联动
