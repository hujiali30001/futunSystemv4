# 跨所现期机会语义升级设计

## 1. 文档目标

本文档定义功能扩展轨 `B1` 的首条短闭环：把当前运行时中的

- `spot buy low / sell high`

机会语义，升级为更贴近总设计的

- `cross-exchange spot + derivative arbitrage`

机会语义。

本次目标不是直接重写 `dispatcher -> executor -> repair` 全链路，也不是立刻上线完整跨所现期执行，而是在现有可运行的 `spot` 主链旁边，补出一层更正确的机会模型与发布语义，为后续用户级策略筛选、任务建模和真实套利执行打基础。

## 2. 范围

本次只做以下能力定义：

- 新增一套独立于 `SpotOpportunity` 的 `ArbitrageOpportunity` 模型
- 明确跨所现期机会的最小字段与语义边界
- 明确新的 Redis ZSET / Stream 发布键
- 定义 `open` 与 `close` 两类机会的发布规则
- 规定与现有 `arb:zset:spot` / `stream:spot_opps` 的并存迁移关系
- 为后续 plan 和实现提供 focused 测试与验收目标

本次不做以下能力：

- 不废弃或重写现有 `SpotOpportunity`
- 不直接改造 `dispatcher` 去消费新的套利机会
- 不在本次引入真实衍生品下单、持仓、保证金或 funding 执行逻辑
- 不在本次引入数据库级 `arbitrage_tasks` 全量建模
- 不把旧 `spot` runtime 主链改造成完整 `opportunity-engine`

## 3. 背景与现状

当前仓库已经具备两套相关但未完全对齐的东西：

### 3.1 总设计已经定义了完整目标语义

在 [2026-05-24-cross-exchange-arbitrage-design.md](file:///d:/old/FuRunSystemV4/docs/superpowers/specs/2026-05-24-cross-exchange-arbitrage-design.md) 中，机会层已经明确采用：

- `arb:zset:open`
- `arb:zset:close`
- `stream:opportunities`

并把机会定义为现货腿与衍生品腿之间的开仓、平仓机会，而不是单纯的多交易所现货价差。

### 3.2 当前运行时代码仍以 `spot` 机会为主

当前实现里：

- [opportunity.py](file:///d:/old/FuRunSystemV4/app/market/opportunity.py) 已存在一个较旧的 `Opportunity` 模型，但没有进入当前主链
- [opportunity.py](file:///d:/old/FuRunSystemV4/app/market/opportunity.py) 中真正被运行时使用的是 `SpotOpportunity`
- [live_spot_flow.py](file:///d:/old/FuRunSystemV4/app/runtime/live_spot_flow.py) 仍按“最低 ask / 最高 bid”的多交易所现货语义生成机会
- [redis_flow.py](file:///d:/old/FuRunSystemV4/app/runtime/redis_flow.py) 仍把结果发布到：
  - `arb:zset:spot`
  - `stream:spot_opps`

### 3.3 当前差距不在“能不能发机会”，而在“机会语义不够对”

也就是说，系统已经能：

- 从行情中挑出一个最小可执行的机会
- 发到 Redis
- 在部分模式下触发后续主链

但它表达的是：

- 跨交易所现货搬砖

而不是总设计真正需要的：

- 现货腿 + 衍生品腿
- 开仓机会 + 平仓机会
- 价差 + funding + 年化收益

## 4. 问题定义

如果继续沿用当前 `spot` 机会语义作为主扩展方向，会带来四类系统性问题：

### 4.1 机会类型混淆

`SpotOpportunity` 当前只表达：

- 在某交易所买入现货
- 在另一交易所卖出现货

它不能表达：

- 现货腿在哪个交易所
- 衍生品腿在哪个交易所
- 当前机会是开仓还是平仓

### 4.2 Redis 键语义不匹配

当前：

- `arb:zset:spot`
- `stream:spot_opps`

这些键名天然绑定“spot”语义，不适合作为后续完整套利系统的统一机会入口。

### 4.3 总设计与落地实现之间缺一层桥梁

总设计已经明确了未来形态，但当前代码如果直接从 `SpotOpportunity` 硬切到完整套利任务模型，会产生过大的跨层改动：

- 机会模型
- Redis payload
- dispatcher 消费
- executor 输入

一次性同时变化，风险过高。

### 4.4 无法稳定并行推进 A 轨和 B 轨

如果 B 轨一上来就推翻当前可运行的 `spot` 主链，就会干扰 A 轨刚刚打稳的：

- `dispatcher -> executor -> repair`

生产闭环节奏。

## 5. 设计目标

本次设计满足以下目标：

1. 建立一套与总设计一致方向的新机会模型
2. 让 `open` / `close` 机会可以独立发布和观察
3. 保留现有 `spot` 主链，避免阻塞 A 轨生产闭环推进
4. 让后续 `dispatcher` 能逐步迁移到新机会语义，而不是一次性翻车式替换
5. 把字段、Redis 键、兼容策略和测试边界先定义清楚

## 6. 方案比较

### 6.1 方案 A：直接改造 `SpotOpportunity`

做法：

- 在现有 `SpotOpportunity` 上直接追加衍生品字段
- 让旧 publisher 直接改发新 payload

优点：

- 表面上改动文件更少

缺点：

- 会把“纯现货机会”和“现期套利机会”混成一个模型
- 旧消费方容易被无意打断
- 迁移不可控

### 6.2 方案 B：新增 `ArbitrageOpportunity`，与旧 `spot` 主链并存，推荐

做法：

- 保留 `SpotOpportunity`
- 新增 `ArbitrageOpportunity`
- 新增独立 publisher、独立 Redis 键和独立 payload
- 先并存，后迁移消费方

优点：

- 语义最清晰
- 风险最低
- 更符合“先补边界，再迁移主链”的节奏

缺点：

- 短期内会同时维护两套机会发布通道

### 6.3 方案 C：暂不做新模型，只在文档层继续讨论

做法：

- 先不落地明确字段与键

优点：

- 没有立即改动成本

缺点：

- B 轨会继续停留在抽象层
- 后续 plan 难以收敛

### 6.4 推荐方案

本次采用方案 B。

原因：

- 它能在不打断当前 `spot` 主链的前提下，补出真正的跨所现期机会语义
- 它给后续 `dispatcher`、任务建模和执行迁移提供清晰边界
- 它最符合当前“双轨并行、单轨短闭环”的节奏

## 7. 核心设计

### 7.1 新模型：`ArbitrageOpportunity`

本次新增一个独立模型：

- `ArbitrageOpportunity`

最小字段定义如下：

- `symbol`
- `spot_exchange`
- `derivative_exchange`
- `opportunity_type`
- `open_spread_bps`
- `close_spread_bps`
- `funding_rate`
- `annualized_bps`
- `redis_member`
- `timestamp`

字段语义如下：

- `symbol`
  - 标准化交易对，例如 `BTC/USDT`
- `spot_exchange`
  - 现货腿所属交易所
- `derivative_exchange`
  - 合约腿所属交易所
- `opportunity_type`
  - 只允许：
    - `OPEN`
    - `CLOSE`
- `open_spread_bps`
  - 当前按开仓语义计算的价差
- `close_spread_bps`
  - 当前按平仓语义计算的价差
- `funding_rate`
  - 当前或最近可用 funding 费率
- `annualized_bps`
  - 含价差和 funding 的年化表达
- `redis_member`
  - Redis 排行成员标识，要求同一条机会可唯一追踪
- `timestamp`
  - 机会生成时间

### 7.2 模型边界

`ArbitrageOpportunity` 与 `SpotOpportunity` 的边界必须明确：

- `SpotOpportunity`
  - 表达“多交易所现货价差”
  - 服务于当前最小 `spot` 主链
- `ArbitrageOpportunity`
  - 表达“现货 + 衍生品”的现期套利机会
  - 服务于总设计对 `open/close` 机会层的要求

本次不允许把两者混成一个兼容所有场景的超大模型。

### 7.3 `Opportunity` 旧模型的处理

当前 [opportunity.py](file:///d:/old/FuRunSystemV4/app/market/opportunity.py) 已存在旧的 `Opportunity`：

- 字段接近现期套利方向
- 但命名过于泛化
- 且未成为当前运行时显式边界

本次建议：

- 不继续扩展旧 `Opportunity` 作为新主模型
- 用更明确的 `ArbitrageOpportunity` 取代其设计角色

如果实现阶段为了减少改动需要复用旧实现片段，也必须以新名称、新边界为准，而不是继续把“套利机会”抽象成泛用 `Opportunity`。

### 7.4 `opportunity_type` 设计

本次只定义两类机会：

- `OPEN`
- `CLOSE`

语义要求：

- `OPEN`
  - 表示当前市场更适合新建一组现期对冲仓位
- `CLOSE`
  - 表示当前市场更适合对已有现期对冲仓位做平仓

注意：

- `open_spread_bps` 和 `close_spread_bps` 两个字段在两类机会里都保留
- `opportunity_type` 决定的是“本次发布时按哪类机会排行和消费”
- 而不是“另一个 spread 字段无意义”

这样做的原因是：

- 便于后续策略层同时参考开仓和平仓指标
- 也便于同一快照同时映射到两种排行与 stream 事件

### 7.5 Redis 键设计

本次新增以下 Redis 键：

- `arb:zset:open`
- `arb:zset:close`
- `stream:opportunities`

语义如下：

- `arb:zset:open`
  - 按 `OPEN` 机会写入排行
  - score 默认使用 `open_spread_bps`
- `arb:zset:close`
  - 按 `CLOSE` 机会写入排行
  - score 默认使用 `close_spread_bps`
- `stream:opportunities`
  - 统一承载套利机会事件流
  - payload 中必须带 `opportunity_type`

### 7.6 与旧键的并存策略

本次明确保留旧键：

- `arb:zset:spot`
- `stream:spot_opps`

并存原则如下：

1. 旧 `spot` 键在本次不删除
2. 新套利机会只写入新键，不复用旧键名
3. 旧消费方继续读取旧键
4. 新消费方未来读取新键
5. 迁移完成前，两套通道允许并存

这条边界是 B1 成功的关键，因为它保证：

- 不为“语义升级”付出“把现有主链打断”的代价

### 7.7 Stream Payload 设计

`stream:opportunities` 的最小 payload 应包含：

- `symbol`
- `spot_exchange`
- `derivative_exchange`
- `opportunity_type`
- `open_spread_bps`
- `close_spread_bps`
- `funding_rate`
- `annualized_bps`
- `redis_member`
- `timestamp`

编码要求：

- 与现有 Redis stream 写法一致，统一转为字符串字段
- 数值字段写字符串值，避免 consumer 端类型漂移

### 7.8 排行成员设计

`redis_member` 需满足：

- 可以唯一标识一条机会
- 能区分：
  - `spot_exchange`
  - `derivative_exchange`
  - `symbol`
  - 时间点
  - 必要时可包含 `opportunity_type`

推荐格式：

- `{spot_exchange}:{derivative_exchange}:{symbol}:{opportunity_type}:{timestamp_ms}`

本次不要求引入额外 `opportunity_id` 持久化键。

### 7.9 Publisher 设计

本次推荐新增独立 publisher，例如：

- `ArbitrageOpportunityPublisher`

职责如下：

1. 接收 `ArbitrageOpportunity`
2. 根据 `opportunity_type` 选择对应 zset：
   - `OPEN -> arb:zset:open`
   - `CLOSE -> arb:zset:close`
3. 始终向 `stream:opportunities` 写入一条事件

本次不要求删除现有 `MarketOpportunityPublisher`。

### 7.10 计算层边界

本次先定义语义，不强行规定实现一定如何采集行情，但要求计算层以后输出 `ArbitrageOpportunity` 时遵守以下边界：

1. 现货腿与衍生品腿的数据源必须显式区分
2. `open_spread_bps` 与 `close_spread_bps` 必须分别基于各自方向公式计算
3. `annualized_bps` 可以先沿用“价差 + funding 的近似表达”
4. 本次不要求一次实现完整资金费率预测模型

### 7.11 与当前 `LiveSpotFlowService` 的关系

当前 [live_spot_flow.py](file:///d:/old/FuRunSystemV4/app/runtime/live_spot_flow.py) 仍是 `SpotOpportunity` 主链。

本次明确：

- 不直接把 `LiveSpotFlowService` 改造成完整套利引擎
- 后续实现阶段可以新增独立服务，或在其旁边增加新的套利机会生成路径
- 但不能通过“偷偷改现有 `spot` 逻辑含义”的方式完成迁移

## 8. 数据流

本次目标数据流如下：

1. 行情层采集现货腿与衍生品腿市场快照
2. 机会计算层生成 `ArbitrageOpportunity`
3. 按 `opportunity_type` 写入：
   - `arb:zset:open` 或 `arb:zset:close`
4. 同时向 `stream:opportunities` 写入事件
5. 旧 `spot` 链路继续发布到：
   - `arb:zset:spot`
   - `stream:spot_opps`
6. 后续策略层迁移时，再决定哪些消费方转向新通道

## 9. 错误处理与边界

本次先定义以下稳定边界：

### 9.1 字段完整性

若缺少以下任一字段，则不得发布新套利机会：

- `symbol`
- `spot_exchange`
- `derivative_exchange`
- `opportunity_type`
- `redis_member`
- `timestamp`

### 9.2 机会类型合法性

若 `opportunity_type` 不是：

- `OPEN`
- `CLOSE`

则必须稳定拒绝发布，而不是默默降级写入某个默认 zset。

### 9.3 新旧通道隔离

新机会发布失败时：

- 不应影响旧 `spot` 通道的发布

旧 `spot` 通道异常时：

- 不应迫使新套利机会回退写入旧键

## 10. 测试策略

本次至少覆盖以下 focused tests：

### 10.1 模型测试

- `ArbitrageOpportunity` 字段完整
- `opportunity_type` 仅接受 `OPEN / CLOSE`
- `redis_member` 可稳定区分不同机会

### 10.2 Publisher 测试

- `OPEN` 机会写入 `arb:zset:open`
- `CLOSE` 机会写入 `arb:zset:close`
- 两类机会都写入 `stream:opportunities`
- stream payload 含最小字段集合

### 10.3 兼容性测试

- 现有 `MarketOpportunityPublisher` 行为不变
- `arb:zset:spot` / `stream:spot_opps` 相关测试不回归
- 新旧 publisher 可并存存在

### 10.4 计算语义测试

- `open_spread_bps` 与 `close_spread_bps` 的方向语义不被混淆
- `annualized_bps` 至少保持稳定、可预测的近似公式

## 11. 验收标准

满足以下条件即可视为 B1 设计落地完成：

1. 仓库中存在明确的 `ArbitrageOpportunity` 语义边界
2. 存在独立于 `spot` 通道的新 Redis 键设计：
   - `arb:zset:open`
   - `arb:zset:close`
   - `stream:opportunities`
3. 新旧机会发布通道可以并存
4. 旧 `spot` 主链不被强制打断
5. 后续 `dispatcher` 和任务模型可以基于新机会语义继续演进

## 12. 实施顺序建议

本次 spec 完成后，推荐按以下顺序实现：

1. 先补模型与 payload 转换
2. 再补独立 publisher 与 focused tests
3. 再补最小机会生成入口
4. 最后评估是否让 `dispatcher` 开始消费新机会

这样可以把 B1 控制在一条短闭环里，而不是演变成一次跨层总重构。

## 13. 与总设计的关系

本设计是对总设计中机会层的一次落地收敛，不是另起炉灶。

它直接对齐了总设计里的以下部分：

- `arb:zset:open`
- `arb:zset:close`
- `stream:opportunities`
- 开仓 / 平仓双机会语义
- 现货腿 / 衍生品腿区分

同时它有意保留了当前仓库现实：

- 现有 `SpotOpportunity` 仍然有效
- 当前 runtime 仍可继续跑最小 `spot` 主链

因此本设计是“向总设计靠拢的桥梁层”，而不是“推翻已有代码的替代层”。

## 14. 后续演进

本次完成后，后续可以继续推进，但不属于本次范围：

- `dispatcher` 基于 `ArbitrageOpportunity` 的用户级筛选
- `arbitrage_task` 与 `arbitrage_leg` 的真实运行时映射
- funding 与持仓状态参与平仓机会判断
- 现期套利执行链与 repair 链的完整接入
