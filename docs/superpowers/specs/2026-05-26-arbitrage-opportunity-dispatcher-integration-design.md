# 套利机会调度接入设计

## 1. 文档目标

本文档定义 `B1-2` 的目标：让新引入的

- `stream:opportunities`

上的套利机会，正式接入当前系统的

- consumer
- dispatcher
- 用户策略筛选
- 路由
- 任务创建

主链。

本次接入同时覆盖：

- `OPEN`
- `CLOSE`

两类套利机会。

但本次目标仍然是“把套利机会接入调度层”，而不是“把套利机会直接打进现有执行层”。

也就是说，本轮要完成的是：

- 套利机会可以被消费
- 可以被用户策略判断
- 可以被账户覆盖与路由逻辑判断
- 可以生成套利调度记录

本轮不要求：

- 现有 `spot executor` 立刻执行这类新任务
- `repair` 立刻理解这类新任务
- 持仓、衍生品执行与补偿闭环在本轮一次打完

## 2. 范围

本次只做以下能力：

- 为 `stream:opportunities` 新增独立 consumer 入口
- 为套利机会新增独立 dispatcher 边界
- 支持 `OPEN` 机会的用户级筛选与调度记录创建
- 支持 `CLOSE` 机会在“存在可关闭上下文”时创建平仓调度记录
- 保持现有 `stream:spot_opps` 主链不变
- 增补 focused tests，覆盖新旧主链并存

本次不做以下能力：

- 不让新套利任务直接进入现有 `RuntimeTradeExecutionService`
- 不让 `repair worker` 处理新套利任务
- 不实现完整的现货腿 + 衍生品腿执行器
- 不实现完整持仓账本或持仓恢复系统
- 不在本次引入完整 `arbitrage_tasks / arbitrage_legs` 数据库落地

## 3. 背景与现状

`B1-1` 已经完成了机会语义层的桥接：

- 新增 `ArbitrageOpportunity`
- 新增 `arb:zset:open`
- 新增 `arb:zset:close`
- 新增 `stream:opportunities`
- 新增 `ArbitrageOpportunityPublisher`
- 新增最小 `LiveArbitrageFlowService`

但当前系统的调度与运行时主链仍然全部围绕旧的现货机会展开。

当前仍然绑定在旧模型上的关键点包括：

- `RedisSpotConsumer`
- `RedisOpportunityDispatcher`
- `dispatch_source_stream = stream:spot_opps`
- `worker_service` 里的默认 dispatch wiring
- 现有 `spot_futures` 策略发现与用户调度主链

也就是说，当前系统已经能“发出新套利机会”，但还不能“消费并调度新套利机会”。

这会造成一个中间断层：

1. 新机会语义已经形成
2. 新 Redis 键已经存在
3. 但用户级策略与任务创建层还看不见这些机会

因此 `B1-2` 的价值就是把这层断点补上。

## 4. 问题定义

如果不做这一步，系统会继续存在四个问题：

### 4.1 新机会流无法进入用户主链

当前 `stream:opportunities` 只是新事件流，还没有 consumer 和 dispatcher 真正消费它。

结果是：

- `OPEN` 机会只能发布，不能转化为用户级调度
- `CLOSE` 机会只能发布，不能驱动平仓候选判断

### 4.2 旧 dispatcher 无法正确理解新 payload

现有 `RedisOpportunityDispatcher` 假设 payload 是：

- `buy_exchange`
- `sell_exchange`
- `target_quote_amount`

它面向的是现货买低卖高语义。

而新套利机会 payload 是：

- `spot_exchange`
- `derivative_exchange`
- `opportunity_type`
- `open_spread_bps`
- `close_spread_bps`
- `funding_rate`
- `annualized_bps`

两者语义不兼容，不能硬复用。

### 4.3 CLOSE 机会天然依赖“可关闭上下文”

`OPEN` 机会只需要判断“是否值得开”。

但 `CLOSE` 机会必须回答一个额外问题：

- 当前到底有没有可关闭的套利上下文

如果没有这个边界，系统就会产生“看见 close 机会就盲目平仓”的假任务。

### 4.4 若直接接入 executor，范围会失控

如果本轮把新机会直接打进现有 executor，就会同时改动：

- dispatcher 语义
- 任务模型
- executor 输入
- repair 语义
- 告警与收口

这会让 `B1-2` 从“调度接入”膨胀成一次跨层重构。

## 5. 设计目标

本次设计满足以下目标：

1. 让 `OPEN` 和 `CLOSE` 两类套利机会都能进入调度主链
2. 保持现有 `spot` 调度与执行主链不回归
3. 新增一条独立的套利机会 consumer / dispatcher 边界
4. 让 `CLOSE` 只在存在可关闭上下文时创建调度任务
5. 让本轮新增的任务先停在“调度记录”层，而不是直接执行

## 6. 方案比较

### 6.1 方案 A：OPEN+CLOSE 接入调度层，但不直接进入 executor，推荐

做法：

- 新增套利机会 consumer
- 新增套利 dispatcher
- `OPEN` 与 `CLOSE` 都参与用户筛选和任务创建
- 但只创建套利调度记录，不直接喂给现有 executor

优点：

- 满足 `OPEN+CLOSE` 全接入
- 范围仍然可控
- 不会误伤现有执行链

缺点：

- 本轮完成后仍然需要下一轮把调度记录接到真实执行层

### 6.2 方案 B：OPEN 先接完整调度，CLOSE 只观察

做法：

- `OPEN` 真正创建调度记录
- `CLOSE` 暂时只做消费与观察

优点：

- 风险更低

缺点：

- 不满足本轮明确要求的 `OPEN+CLOSE` 全接入

### 6.3 方案 C：OPEN+CLOSE 直接接入现有 executor

做法：

- 新机会直接翻译成现有执行任务

优点：

- 向真实闭环推进更快

缺点：

- 当前 executor 仍是现货双腿语义
- 会在一轮内同时触发执行、repair、告警、收口变动
- 风险最高

### 6.4 推荐方案

本次采用方案 A。

原因：

- 它兼顾了你明确要求的 `OPEN+CLOSE 全接入`
- 它又把范围限定在调度层，而不是过早把执行层一起拖进来
- 这最符合当前“双轨并行、单轨短闭环”的推进节奏

## 7. 核心设计

### 7.1 新增独立套利机会 consumer

本次新增一条并行于 `RedisSpotConsumer` 的 consumer 入口，用于消费：

- `stream:opportunities`

职责如下：

1. 从 Redis stream 读取套利机会消息
2. 解析 `ArbitrageOpportunity` payload
3. 校验 `opportunity_type`
4. 调用新的套利 dispatcher

本次不允许通过修改 `RedisSpotConsumer` 去“兼容”新 payload。

原因是：

- `spot` 与 `arbitrage` 的 payload 边界已经不同
- 继续混在一个 consumer 中会让逻辑迅速失真

### 7.2 新增独立套利 dispatcher

本次新增一条独立于 `RedisOpportunityDispatcher` 的 dispatcher 边界。

职责如下：

1. 根据 `ArbitrageOpportunity` 做用户发现
2. 按策略配置筛选用户
3. 校验账户覆盖与交易所约束
4. 做节点路由
5. 创建套利调度记录

本次不要求它直接调用：

- `RuntimeTradeExecutionService`
- `RuntimeRepairExecutionService`

### 7.3 新增“套利调度记录”边界

本次新增的不是可直接执行的 executor payload，而是一条更窄的调度记录边界。

最小字段建议如下：

- `task_uuid`
- `user_id`
- `symbol`
- `spot_exchange`
- `derivative_exchange`
- `opportunity_type`
- `open_spread_bps`
- `close_spread_bps`
- `funding_rate`
- `annualized_bps`
- `source_message_id`
- `strategy_config_id`
- `worker_node_id`
- `status`
- `created_at`

这条记录的语义是：

- 用户级套利调度已命中
- 已完成筛选和路由
- 可供后续执行链继续接入

本次不要求它已经具备：

- 现货腿下单字段
- 合约腿下单字段
- repair 输入字段

### 7.4 OPEN 分支

`OPEN` 机会的最小调度判断如下：

1. 用户允许自动交易
2. 策略类型命中套利主线
3. `symbol`、交易所范围命中
4. `open_spread_bps` 或 `annualized_bps` 达到阈值
5. 用户在：
   - `spot_exchange`
   - `derivative_exchange`
   上具备账户覆盖

判断通过后：

- 创建一条 `OPEN` 型套利调度记录

状态建议使用现有轻量风格，例如：

- `CREATED`
- 或 `DISPATCHED`

本次不要求 `OPEN` 记录立刻转化为真实执行任务。

### 7.5 CLOSE 分支

`CLOSE` 机会的调度判断必须比 `OPEN` 多一层边界。

除了基础用户/策略/账户检查外，还必须先回答：

- 当前是否存在可关闭套利上下文

如果不存在，则：

- 不创建 `CLOSE` 调度记录
- 不误造平仓任务

如果存在，则：

- 创建一条 `CLOSE` 型套利调度记录

### 7.6 可关闭套利上下文

本次不要求完整持仓系统，但必须引入最小查询边界，用于支持 `CLOSE` 调度判断。

最小匹配维度如下：

- `user_id`
- `symbol`
- `spot_exchange`
- `derivative_exchange`

这条上下文可以来自：

- 已存在的套利调度记录
- 或未来更完整的套利任务事实表

本次的关键不是“上下文最终存哪里”，而是先建立一个稳定接口：

- 可以判断“这个用户在这对交易所上，是否有可关闭的套利上下文”

### 7.7 与现有 spot dispatcher 的关系

本次明确保持旧链不动：

- `RedisSpotConsumer` 继续消费 `stream:spot_opps`
- `RedisOpportunityDispatcher` 继续处理旧 spot payload
- `dispatch_source_stream` 默认行为不回归

新链与旧链并存：

- 旧链处理现货机会
- 新链处理套利机会

本次不做单 consumer / 单 dispatcher 混用。

### 7.8 策略边界

当前仓库里用户策略发现与筛选逻辑已经存在。

本次不要求彻底重做策略系统，但要求至少新增一层明确分支：

- 现有 spot 主链策略
- 套利机会主链策略

本次推荐继续沿用现有策略发现方式，但在套利 dispatcher 中显式识别：

- 哪些策略能消费 `ArbitrageOpportunity`

这一步不要求把策略系统抽成通用 DSL。

### 7.9 路由边界

本次继续沿用现有 `UserNodeRouter` / `NodeExecutionTaskPublisher` 一类思路中的“路由先行”原则。

但由于本轮还不直接接 executor，本次只要求：

- 为套利调度记录落下 `worker_node_id`

而不是立刻把它写入现有：

- `stream:spot_exec_tasks:{node_id}`

### 7.10 记录与执行解耦

本设计最重要的边界是：

- 调度记录先成立
- 执行接入下一轮再做

这样做的价值是：

1. `OPEN+CLOSE` 真正进入了调度主链
2. 旧 executor / repair 不会被当前轮次误伤
3. 下一轮可以围绕已形成的调度记录继续补执行链

## 8. 数据流

本次目标数据流如下：

1. `stream:opportunities` 收到套利机会事件
2. 新套利 consumer 读取消息
3. 新套利 dispatcher 解析 payload
4. 发现可调度用户
5. 按策略阈值与账户覆盖筛选
6. 若 `OPEN`：
   - 创建开仓型套利调度记录
7. 若 `CLOSE`：
   - 先查找可关闭上下文
   - 若存在，再创建平仓型套利调度记录
8. 写入调度记录并附带 `worker_node_id`
9. 本轮结束于调度层，不继续下沉到 executor

## 9. 错误处理

### 9.1 Consumer 输入错误

若 `stream:opportunities` payload 缺少以下关键字段：

- `symbol`
- `spot_exchange`
- `derivative_exchange`
- `opportunity_type`
- `source_message_id` 或等价消息标识

则：

- 稳定拒绝消费
- 不进入调度主链

### 9.2 非法 opportunity_type

若 `opportunity_type` 不是：

- `OPEN`
- `CLOSE`

则：

- 稳定拒绝
- 不回退到旧 spot 调度器

### 9.3 OPEN 过滤失败

若用户策略、账户覆盖、交易所范围或阈值不命中，则：

- 不创建调度记录
- 视为正常过滤，而不是系统错误

### 9.4 CLOSE 上下文不存在

若 `CLOSE` 机会没有匹配中的可关闭上下文，则：

- 不创建平仓调度记录
- 视为正常跳过，而不是失败告警

### 9.5 调度记录写入失败

若调度记录写入失败，则：

- 作为真实错误处理
- 但不得污染旧 `spot` 主链

## 10. 测试策略

本次至少补以下 focused tests：

### 10.1 Consumer / Dispatcher

- 新 consumer 能消费 `stream:opportunities`
- 新 dispatcher 能识别 `OPEN` 与 `CLOSE`
- 非法 `opportunity_type` 稳定拒绝

### 10.2 OPEN 调度

- `OPEN` 命中策略时创建调度记录
- `OPEN` 不命中阈值时不创建记录
- `OPEN` 在账户覆盖不足时不创建记录

### 10.3 CLOSE 调度

- `CLOSE` 在无可关闭上下文时不创建记录
- `CLOSE` 在有匹配上下文时创建平仓型调度记录

### 10.4 并存回归

- `stream:spot_opps` 旧链不回归
- `RedisSpotConsumer` 旧行为不回归
- 新旧两条流可并存

## 11. 验收标准

满足以下条件即可视为本次完成：

1. `stream:opportunities` 存在独立 consumer / dispatcher 主链
2. `OPEN` 机会可创建开仓型套利调度记录
3. `CLOSE` 机会仅在存在可关闭上下文时创建平仓型调度记录
4. 旧 `spot` 主链不回归
5. 调度记录与真实执行链仍保持解耦

## 12. 与上一轮设计的关系

本设计建立在 `B1-1` 之上。

其中：

- `B1-1` 解决“机会模型与发布语义”
- `B1-2` 解决“机会如何进入用户调度主链”

两者关系是：

- `B1-1` 把机会发出来
- `B1-2` 把机会接进来

## 13. 后续演进

本次完成后，后续可以继续推进，但不属于本次范围：

- 把套利调度记录接入真实执行链
- 为现货腿与衍生品腿建立真正的执行 payload
- 为 `CLOSE` 引入更真实的持仓与任务事实查询
- 让 repair 与告警体系理解新套利任务
