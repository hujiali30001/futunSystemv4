# Executor Preflight / Account Truth / Repair 收口强化设计

## 1. 文档目标

本文档定义一条新的高优先级生产闭环主线，用于把当前的

- `dispatcher -> executor -> repair`

从“最小可运行”推进到“更接近生产可依赖”。

本次目标不是扩新的机会发现能力，也不是直接上真实交易所大联调，而是在现有
`executor preflight`、`account truth`、`repair worker` 最小闭环基础上，再补三类关键能力：

1. `executor` 严格执行 dispatcher 已经决定的买卖边，不再在执行层漂移重选
2. `account truth` 从“能解析”提升到“能稳定拒绝错误绑定/错误账户/错误运行区”
3. `repair` 从“最小一次补单”提升到“可解释、可观测、可稳定收口的单次自动修复”

这样可以先把执行底座打稳，再继续并行推进更大的跨所现期功能面。

## 2. 范围

本次只做以下能力：

- 收紧 `executor` preflight，让最终执行输入与 dispatcher 决策保持一致
- 强化 `account truth` 的校验与失败边界
- 强化 `repair` 的输入约束、执行边界和结果收口
- 增补 focused tests 与运行时可观测性

本次不做以下能力：

- 不引入新的衍生品/funding 机会模型
- 不做完整多阶段 repair 优先级链
- 不引入 Redis consumer group 或 offset 持久化
- 不重构整个 runtime worker 框架
- 不直接做真实交易所完整生产联调

## 3. 背景与现状

当前系统已经具备以下基础：

- `dispatcher` 已能从策略和账户真值出发生成用户级执行任务
- `executor` 已能做最小 preflight、control rule、account truth 解析、执行结果摘要和 repair 发布
- `repair worker` 已能消费 repair task 并做一次最小自动补单
- `systemd` 与最小远端双服务 canary 已经打通

当前最关键的缺口也很明确：

1. `RuntimeTradeExecutionService` 仍会基于实时 ticker 重新选择买卖边，而不是严格执行 payload 中已决策好的 `buy_exchange / sell_exchange`
2. `account truth` 虽然能解析账户，但仍偏“能解析就行”，对绑定结果与最终执行输入的一致性约束还不够强
3. `repair` 当前只做一次最小补单，收口虽存在，但对“为什么修、修了哪条腿、还剩什么风险”的表达仍偏薄

这三个问题都不属于“缺功能”，而属于“已有主链离生产可依赖还差一层加固”。

## 4. 问题定义

如果继续保持现状，会出现三类系统性风险：

### 4.1 执行语义漂移

`dispatcher` 已经根据账户、策略和路由决定了：

- `buy_exchange`
- `sell_exchange`

但 `executor` 内部的 `RuntimeTradeExecutionService` 仍会再次基于实时 ticker 选最优边。

这会带来：

- dispatcher 决策与 executor 实际执行不一致
- 账户真值与真实执行边的对应关系变得不可靠
- 后续 repair / 审计 / 任务摘要对“原计划执行哪一边”的判断被污染

### 4.2 账户真值边界不够硬

当前 `ExecutorAccountTruthResolver` 已能解析：

- 绑定账户
- 自动选择账户
- 凭证解密
- 代理恢复

但距离“生产入口硬边界”还差：

- 更强的一致性校验
- 更稳定的失败 reason
- 更清晰的执行输入和账户真值对照关系

### 4.3 repair 收口可运行但不够稳

当前 repair 已经能做一次最小补单尝试，但它仍存在：

- 默认按 `target_quote_amount` 估量，而不直接表达“剩余失败腿到底修了多少”
- 缺少更强的输入约束与单次修复边界
- 结果事件和任务收口虽可用，但对生产排障还不够清晰

## 5. 设计目标

本次设计满足以下目标：

1. `executor` 只执行 dispatcher 已经决定的交易所边，不再内部改写买卖边
2. `account truth` 失败时输出更稳定的边界原因，成功时保证最终执行输入与账户真值一致
3. `repair` 单次自动补单仍保持最小范围，但收口更稳定、更可解释
4. 全部改动尽量收敛在 runtime 执行层，不扩到机会发现或更大状态机
5. 为下一步真实交易所小范围联调打稳基础

## 6. 方案比较

### 6.1 方案 A：聚焦执行底座加固，推荐

做法：

- 强化 `executor preflight`
- 强化 `account truth`
- 强化 `repair` 收口
- 不动更大的功能模型

优点：

- 最短路径提升现有主链稳定性
- 与最近真实远端问题高度一致
- 改动面可控、测试目标明确

缺点：

- 不能直接扩大总设计的功能覆盖面

### 6.2 方案 B：直接把 spot 机会升级到完整跨所现期语义

做法：

- 先做 derivative leg、funding、open/close 双向机会

优点：

- 对总设计覆盖推进更快

缺点：

- 建立在仍不够稳的执行底座上
- 会放大 executor/repair 已存在的问题

### 6.3 方案 C：直接做真实交易所生产联调

做法：

- 不再优先补代码边界，直接上真实路径验证

优点：

- 最接近生产

缺点：

- 风险最高
- 一旦失败，排障成本会明显大于先加固底座

### 6.4 推荐方案

本次采用方案 A。

原因：

- 当前主链已经具备“可跑”的基础
- 当前最大的收益不是再扩能力，而是把已有能力的执行语义与收口边界做稳
- 这样最符合“尽快完成整个项目”的节奏：先补最短板，再继续双轨并行

## 7. 核心设计

### 7.1 Executor 执行语义一致化

本次要求 `RuntimeTradeExecutionService` 严格执行传入的交易所边。

即：

- `payload["buy_exchange"]` 决定买腿交易所
- `payload["sell_exchange"]` 决定卖腿交易所

执行层不再基于实时 ticker 重新选：

- 最低 ask 作为买边
- 最高 bid 作为卖边

新的执行服务职责是：

1. 使用 payload 指定的交易所建立 session
2. 仅在这两个交易所上拉取 ticker / market 精度 / 深度
3. 基于当前行情为这两个既定边生成价格与数量
4. 提交双腿并返回结构化执行结果

这保证：

- dispatcher 决策
- account truth 绑定
- task 摘要
- repair 计划

四者都围绕同一组执行边运行。

### 7.2 Executor Preflight 强化

此前的 preflight 设计已经定义了最小 reason code，本次在此基础上继续加强，但仍保持轻量。

新增的核心要求是：

1. preflight 校验基于 `effective_payload`
2. preflight 校验必须覆盖最终执行边与账户真值的一致性
3. preflight 一旦失败，任务不能进入真实 dispatch

本次继续沿用“validator + 稳定 reason code”的模式，不把规则散落在主流程里。

新增重点校验包括：

- payload 中 `buy_exchange / sell_exchange` 与 `execution_accounts_by_exchange` 键必须完全一致
- 若 payload 带 `buy_account_id / sell_account_id`，则解析出的 `account_id` 必须与之严格一致
- 绑定账户与最终运行 `region / env_mode / market_type_scope` 必须一致
- 执行服务收到的 `exchanges` 顺序必须固定为 `[buy_exchange, sell_exchange]`

### 7.3 Account Truth 强化

`ExecutorAccountTruthResolver` 当前职责保留不变，但输出约束加强。

强化点如下：

1. 区分“绑定账户不存在”和“绑定账户不可执行”
2. 区分“解析到了账户”与“解析到的账户与执行意图不一致”
3. 对 `region / market_type_scope / auto_trade_enabled` 的失败原因继续保持稳定 code

本次不要求把 account truth 扩展成复杂余额裁决器，但要求它至少成为一个“稳定而可信”的执行事实入口。

建议继续沿用并扩充 reason code，而不是回退为异常文本。

### 7.4 Repair 单次收口强化

本次不把 repair 扩成完整优先级链，只强化单次修复的边界。

要求如下：

1. 仍只处理 `AUTO_HEDGE_REPAIRING + OPEN_PARTIAL`
2. 仍只做一次最小自动补单
3. 但结果必须更清晰表达：
   - 修了哪条腿
   - 剩余哪些失败交易所
   - 最终是否进入人工处理

本次的核心不是“修更多次”，而是“把一次修复的输入、过程和结果写清楚”。

### 7.5 Repair 输入与执行一致化

repair 不应再隐式依赖“重新估算一次整单”的思路，而要更强地围绕 executor 传下来的失败腿上下文工作。

首版仍允许保留 `target_quote_amount` 作为最小输入，但要求：

- `target_exchanges`
- `failed_exchanges`
- `buy_exchange / sell_exchange`
- `task_uuid`

这些字段共同决定 repair 只修哪一边，而不是重新推导整单执行语义。

### 7.6 事件与摘要收口

本次继续保留以下职责分层：

- `executor.execution_result`
- `executor.repair_planned`
- `repair.task.finished`

但要求增强一致性：

- `executor.execution_result` 必须准确反映 dispatcher 指定边上的结果
- `executor.repair_planned` 必须仅围绕真实失败边生成
- `repair.task.finished` 必须明确表达修复后剩余失败面

任务摘要层继续使用现有字段，不做大状态机重写，但要求：

- repair 成功与 repair 失败的收口语义更稳定
- `status_reason` 与 `repair_reason` 更适合告警与排障

## 8. 数据流

本次目标数据流如下：

1. `dispatcher` 生成已确定买卖边的任务
2. `executor` 读任务并执行 control rule
3. `account truth` 解析执行账户
4. `preflight` 校验 payload 与账户真值的一致性
5. `executor` 严格在已决定的交易所边上执行
6. 若结果为可修复 `OPEN_PARTIAL`，发布 repair task 并发 `executor.repair_planned`
7. `repair worker` 只修失败边一次
8. 发 `repair.task.finished`
9. 任务摘要完成最小但稳定的状态收口

## 9. 错误处理

本次不引入新的大异常框架，但要求错误边界更稳定：

### 9.1 Executor

- payload / account truth / preflight 不一致：
  - 稳定失败
  - 不进入真实执行
- 真实 dispatch 异常：
  - 继续按当前失败链处理
- repairable `OPEN_PARTIAL`：
  - 不误报成普通执行失败
  - 保持 `execution_result + repair_planned`

### 9.2 Repair

- 输入不支持：
  - 稳定拒绝
- 单次补单失败：
  - 收口到 `MANUAL_REQUIRED`
- 修复执行异常：
  - 不进入无限重试
  - 仍按单次修复失败收口

## 10. 测试策略

本次至少补以下 focused tests：

### 10.1 Executor 执行语义

- dispatcher 已指定 `buy_exchange / sell_exchange` 时，执行服务不再重选边
- 生成的 open legs 顺序与交易所边严格对应 payload

### 10.2 Preflight / Account Truth

- 绑定账户和执行边不一致时稳定失败
- 自动选择账户与运行区不一致时稳定失败
- 合法绑定路径仍继续成功

### 10.3 Repair 收口

- `OPEN_PARTIAL` 的失败边只产生单次最小 repair
- repair 成功时收口到稳定成功语义
- repair 失败时收口到稳定人工处理语义
- `repair.task.finished` 含有完整最小排障字段

### 10.4 回归

- 现有 `dispatcher -> executor -> repair` 主链不回归
- 现有 canary stream 隔离与 OPEN_PARTIAL 误报警修复不回归

## 11. 验收标准

满足以下条件即可视为完成：

1. `executor` 严格执行 dispatcher 决定的买卖边
2. `account truth` 与 preflight 的失败边界更稳定
3. `repair` 单次自动补单仍最小，但结果收口更清晰
4. focused tests 与相邻回归通过
5. 后续真实交易所小范围联调的前置风险明显下降

## 12. 与旧专项设计的关系

本设计不是废弃旧设计，而是把以下两条已存在专项向前推进一层：

- `2026-05-25-executor-preflight-risk-design.md`
- `2026-05-25-repair-worker-minimal-auto-hedge-design.md`

其中：

- 旧 `executor preflight` 设计解决“入口校验”
- 旧 `repair worker` 设计解决“最小补单闭环”
- 本设计解决“执行语义一致、账户真值可信、repair 收口稳定”

## 13. 后续演进

本次完成后，后续可以继续推进，但不属于本次范围：

- Redis 消费可靠性治理
- 更完整的 repair 优先级链
- 真实交易所最小生产联调
- 跨所现期机会语义升级到 derivative/funding/open-close 双向模型
