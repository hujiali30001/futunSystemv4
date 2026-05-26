# 套利自动恢复事件补齐设计

## 1. 文档目标

本文档定义 `B1-5C` 的目标：为 `B1-5B` 已落地的自动恢复链补齐专用 `arb.recovery.*` 事件，让系统能够明确表达“已经安排重试”“已经进入冷却”“自动恢复已耗尽”这三类恢复动作。

本次覆盖：

- `arb.recovery.retry_scheduled`
- `arb.recovery.cooldown_started`
- `arb.recovery.exhausted`
- `alerting.py` 中的中文标题与飞书分层
- 自动恢复链的 focused tests

本轮不做：

- 不改 `B1-5B` 既有自动恢复策略
- 不新增失败分类规则
- 不修改 cooldown 算法
- 不引入 metrics / trace / audit

## 2. 范围

本次只做以下能力：

- 为自动恢复动作补独立事件命名空间
- 为 `retry / cooldown / exhausted` 定义最小 payload
- 在统一恢复出口补事件派发
- 为恢复事件补中文标题与飞书分层
- 增补 focused tests，确保旧 `arb.*` 事件不回归

本次不做以下能力：

- 不改变 executor / repair 的失败分类逻辑
- 不新增额外数据库字段
- 不新增复杂告警聚合策略
- 不修改 spot 链事件命名或飞书行为

## 3. 背景与现状

当前 `B1-5B` 已经让套利失败后进入系统自动恢复，而不是人工介入。

系统已经支持：

- `RETRY_PENDING`
- `COOLDOWN`
- `EXHAUSTED`

并且：

- executor 非可 repair 失败会进入统一自动恢复决策
- repair 失败也会进入同一自动恢复决策

但目前自动恢复链仍然存在一个明显缺口：

- 系统会自动安排恢复动作
- 但不会显式告诉运行侧“它到底安排了什么动作”

当前我们只能通过：

- 数据库字段变化
- 代码逻辑
- 部分失败事件

去反推：

- 这次失败是准备重试
- 还是已经进入冷却
- 还是自动恢复已经耗尽

这会直接带来排障成本：

- 很难快速判断恢复链是否按预期运行
- 很难区分“任务失败”和“任务失败后系统做了什么”

所以 `B1-5C` 的价值，不是让自动恢复更智能，而是让自动恢复本身更可见。

## 4. 问题定义

如果不补这一步，系统会继续存在四个问题：

### 4.1 恢复动作不可见

当前能看到：

- `arb.executor.task_failed`
- `arb.repair.finished(ERROR)`

但看不到：

- 系统是否安排了自动重试
- 是否进入冷却
- 是否已经耗尽自动恢复策略

### 4.2 当前失败结果与后续恢复动作混在一起

失败结果事件表达的是：

- 这一轮尝试失败了

而恢复动作表达的是：

- 失败后系统下一步怎么做

这两层语义现在没有明确分离。

### 4.3 自动恢复链缺少完整的运行轨迹

若未来继续做：

- 更细粒度失败分类
- 更复杂 cooldown
- 更复杂恢复编排

没有专用恢复事件，会让后续观察和验收都更困难。

### 4.4 所有恢复事件都发飞书会制造噪音

如果：

- `retry_scheduled`
- `cooldown_started`
- `exhausted`

都直接外发飞书，会带来大量噪音。

因此本轮必须同时解决：

- 恢复事件补齐
- 日志 / 飞书分层

## 5. 设计目标

本次设计满足以下目标：

1. 自动恢复链拥有独立的 `arb.recovery.*` 事件命名空间
2. 明确区分“本次尝试失败”和“系统安排的恢复动作”
3. 恢复事件在统一出口产生，而不是分散在多个失败点
4. `retry / cooldown` 默认只记日志，不制造飞书噪音
5. `exhausted` 进入飞书，明确标记自动恢复终局
6. `B1-5A` 和 `B1-5B` 既有行为不回归

## 6. 方案比较

### 6.1 方案 A：补齐专用恢复事件，推荐

做法：

- 新增 `arb.recovery.*`
- 在统一自动恢复出口中派发恢复事件
- 对恢复事件做独立的中文标题和飞书分层

优点：

- 语义清楚
- 改动最小
- 最有利于下一步做失败分类细化

缺点：

- 只补可见性，不提升恢复策略智能度

### 6.2 方案 B：把恢复动作塞进现有失败事件 payload

做法：

- 继续复用 `arb.executor.task_failed`
- 继续复用 `arb.repair.finished`
- 在它们的 payload 中追加恢复动作字段

优点：

- 代码变动更少

缺点：

- 失败结果和恢复动作语义混在一起
- 后续筛选和告警分层不清晰

### 6.3 方案 C：直接做更细失败分类，不补恢复事件

做法：

- 不补 `arb.recovery.*`
- 直接进入下一步失败分类和恢复规则细化

优点：

- 看起来更“推进业务能力”

缺点：

- 自动恢复链依旧不可见
- 会放大调试与验收难度

### 6.4 推荐方案

本次采用方案 A。

原因：

- 它最符合当前“先让恢复链可见，再让恢复链更聪明”的推进顺序
- 不会扩大当前任务范围
- 能直接为下一轮失败分类细化提供观测基础

## 7. 核心设计

### 7.1 事件命名空间

本次新增：

- `arb.recovery.retry_scheduled`
- `arb.recovery.cooldown_started`
- `arb.recovery.exhausted`

这些事件不替换现有：

- `arb.executor.task_failed`
- `arb.repair.finished`

而是并行补充。

它们表达的是：

- 当前失败结果之后，系统决定执行的恢复动作

### 7.2 事件语义

#### `arb.recovery.retry_scheduled`

语义：

- 当前失败不会直接终止
- 系统已经把任务安排为下一次自动尝试

触发场景：

- executor 非可 repair 失败后，决策为 `RETRY_PENDING`
- repair 失败后，决策为 `RETRY_PENDING`

默认级别：

- `INFO`

默认只进结构化日志，不进飞书。

#### `arb.recovery.cooldown_started`

语义：

- 当前失败不会立即重试
- 任务已经进入冷却窗口

触发场景：

- executor 或 repair 失败后，决策为 `COOLDOWN`

默认级别：

- `INFO`

默认只进结构化日志，不进飞书。

#### `arb.recovery.exhausted`

语义：

- 自动恢复策略已经跑尽
- 当前自动收口链到达终局

触发场景：

- executor 或 repair 失败后，决策为 `EXHAUSTED`

默认级别：

- `ERROR`

进入飞书。

### 7.3 最小 payload 结构

三类事件至少共享以下字段：

- `task_uuid`
- `user_id`
- `symbol`
- `task_type`
- `spot_exchange`
- `derivative_exchange`
- `failure_reason`
- `retry_count`
- `max_retry_count`
- `auto_recovery_status`

额外字段：

#### `arb.recovery.retry_scheduled`

- `next_action = RETRY_PENDING`

#### `arb.recovery.cooldown_started`

- `next_action = COOLDOWN`
- `cooldown_until`

#### `arb.recovery.exhausted`

- `next_action = EXHAUSTED`

### 7.4 事件落点

本次恢复事件统一放在：

- `ArbitrageExecutionTaskConsumer._apply_auto_recovery()`

而不是：

- 分散在 executor non-repairable failure 路径
- 分散在 repair failure 路径

原因：

- executor 失败和 repair 失败都已汇聚到同一恢复出口
- 统一落点可以保证恢复事件只在“状态已经落库完成后”发出
- 避免同一恢复动作被多处重复派发

### 7.5 派发顺序

推荐顺序：

1. 当前尝试失败事件先产生
2. repository 完成自动恢复状态写入
3. `_apply_auto_recovery()` 根据最终决策派发对应 `arb.recovery.*`

这样事件表达的不是：

- “准备做某个恢复动作”

而是：

- “恢复动作已经被系统正式安排”

### 7.6 日志 / 飞书分层

本次分层如下：

#### 只进结构化日志

- `arb.recovery.retry_scheduled`
- `arb.recovery.cooldown_started`

#### 进入飞书

- `arb.recovery.exhausted`

原因：

- retry 和 cooldown 属于系统正常自动恢复过程
- exhausted 才表示自动恢复链真正到达终局，值得人工关注

### 7.7 中文标题与飞书文本

`alerting.py` 中至少补以下中文标题：

- `套利自动重试已安排`
- `套利自动冷却已开始`
- `套利自动恢复已耗尽`

飞书文本重点展示：

- 服务
- 交易对
- 任务类型
- 现货交易所
- 衍生品交易所
- 失败原因
- 当前重试次数 / 最大重试次数
- `cooldown_until`（仅 cooldown）

本轮不要求对 `INFO` 类恢复事件做复杂富文本。

只要求：

- 标题映射完整
- `arb.recovery.exhausted` 的飞书文本清楚可读

### 7.8 与现有事件关系

本次明确保留：

- `arb.executor.execution_result`
- `arb.executor.repair_planned`
- `arb.executor.task_failed`
- `arb.repair.finished`

新增的是恢复动作层事件，不是替换现有执行/repair 事件。

关系上：

- 旧事件回答“这一轮尝试发生了什么”
- 新事件回答“系统接下来安排了什么恢复动作”

## 8. 数据流

本次目标数据流如下：

1. `arb_executor` 执行任务
2. 若当前尝试失败：
   - 发失败结果事件
3. 进入 `_apply_auto_recovery()`
4. repository 写入恢复状态
5. 若决策为 `RETRY_PENDING`：
   - 发 `arb.recovery.retry_scheduled`
6. 若决策为 `COOLDOWN`：
   - 发 `arb.recovery.cooldown_started`
7. 若决策为 `EXHAUSTED`：
   - 发 `arb.recovery.exhausted`
8. `AlertRouter` 对恢复事件按级别分层：
   - retry / cooldown 只记日志
   - exhausted 进飞书

## 9. 错误处理

### 9.1 恢复事件派发失败不阻断主链

若 `arb.recovery.*` 事件派发失败：

- 不应回滚已完成的恢复状态落库
- 不应反向中断 executor / repair 主链

本轮仍按现有运行时事件风格处理：

- 状态写库优先
- 事件尽力派发

### 9.2 事件必须基于已落库状态构建

恢复事件 payload 中的：

- `retry_count`
- `auto_recovery_status`
- `cooldown_until`

必须反映状态写入后的结果，而不是写入前的旧值。

### 9.3 exhausted 事件必须稳定产生

自动恢复一旦进入 `EXHAUSTED`：

- 必须稳定发出 `arb.recovery.exhausted`

否则飞书会漏掉真正需要关注的自动恢复终局。

## 10. 测试策略

本次至少补以下 focused tests：

### 10.1 Runtime worker 恢复事件

- non-repairable failure -> `arb.recovery.retry_scheduled`
- retry 上限 -> `arb.recovery.cooldown_started`
- cooldown 后再次失败 -> `arb.recovery.exhausted`
- repair failure 走同一恢复事件出口

### 10.2 Alerting 模板

- 中文标题映射覆盖 `arb.recovery.*`
- `arb.recovery.exhausted` 飞书文本含关键字段
- `arb.recovery.retry_scheduled` 和 `arb.recovery.cooldown_started` 默认不触发飞书

### 10.3 并存回归

- `B1-5A` 已有 `arb.executor.*` / `arb.repair.*` 事件测试不回归
- `B1-5B` 自动恢复策略测试不回归
- 旧 spot 事件与告警行为不回归

## 11. 验收标准

满足以下条件即可视为本次完成：

1. 自动恢复链拥有独立的 `arb.recovery.*` 事件命名空间
2. retry / cooldown / exhausted 三类恢复动作都能写入结构化日志
3. exhausted 事件能进入飞书
4. 恢复事件统一从自动恢复出口派发
5. `B1-5A` 和 `B1-5B` 的既有行为不回归

## 12. 后续演进

本次完成后，后续可以继续推进，但不属于本次范围：

- 更细粒度失败分类
- 更复杂 cooldown / 退避策略
- `arb.recovery.*` 告警聚合与抑制
- 交易所级恢复事件
- 用户级恢复抑制事件
