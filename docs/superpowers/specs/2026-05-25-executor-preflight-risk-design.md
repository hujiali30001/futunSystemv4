# Executor 执行前风控与失败边界设计

## 1. 文档目标

本文档定义如何在 `executor` 真正执行任务前增加一层统一的 preflight 校验，让系统从“任务一到就执行”推进到“先校验任务与账户边界，再决定是否执行”。

本次目标是在不重做完整交易执行框架、不新增复杂状态机、也不引入新的后台配置面的前提下，把 executor 链路补齐以下能力：

- 对执行前必要字段做统一校验
- 对明显非法任务做稳定拒绝，而不是进入真实 dispatch 后才失败
- 对失败任务写入稳定的 `status_reason`
- 保持当前 `task-account-binding` 与 control rule 链路兼容

这样可以先把 executor 的入口收紧，减少错误任务进入真实执行流程的概率，并为后续更完整的执行编排保留清晰边界。

## 2. 范围

本次只做以下能力：

- 定义 executor preflight 校验层的职责与接入点
- 定义首版校验项与对应失败码
- 定义 preflight 失败时的任务状态更新与事件行为
- 定义与账户 binding 解析结果的衔接方式
- 定义对应的测试策略

本次不做以下能力：

- 不重构 `TradeExecutor` 为完整编排器
- 不新增订单级持久化或成交明细表
- 不新增 repair workflow 或自动补腿执行
- 不实现复杂的仓位、余额、滑点或费率风控
- 不新增独立的风控配置存储
- 不改动 dispatcher 的账户选定规则

## 3. 背景与现状

当前系统已经具备以下基础：

- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py) 中 `RedisExecutionTaskConsumer` 已经完成：
  - control rule 校验
  - 任务状态 `mark_executing / mark_succeeded / mark_failed / mark_blocked`
  - 账户 binding 优先解析
- [executor_account_truth.py](file:///d:/old/FuRunSystemV4/app/runtime/executor_account_truth.py) 已能按 `buy_account_id / sell_account_id` 解析执行账户
- [executor.py](file:///d:/old/FuRunSystemV4/app/trading/executor.py) 已能并发提交双腿订单，并返回 `OPEN_HEDGED / OPEN_PARTIAL`
- [risk_manager.py](file:///d:/old/FuRunSystemV4/app/trading/risk_manager.py) 当前仅基于执行结果生成 repair plan

但 executor 入口仍有明显缺口：

1. 缺少统一的执行前校验层
2. payload 中如果关键字段缺失，通常要等到更深层调用时报错
3. 失败原因仍可能退化成原始异常文本，不利于稳定监控和排障
4. binding 解析成功后，缺少一层“payload 与解析结果是否一致”的入口检查

因此，本次需要在 executor 真正调用下游 dispatch 前，增加独立且可测试的 preflight 校验。

## 4. 问题定义

如果继续保持现状，会有以下问题：

1. executor 对非法 payload 的拒绝不稳定，容易出现深层异常
2. 相同类型的问题可能产生不同错误文本，影响告警聚合与运维判断
3. binding 链路虽然已打通，但入口上仍缺“最终一致性兜底”
4. 后续如果要增加更多执行前限制，没有合适的统一挂点

因此，本次需要新增一个轻量、稳定、职责单一的 executor preflight 层。

## 5. 设计目标

本次设计满足以下目标：

1. executor 在真实 dispatch 前统一执行 preflight 校验
2. preflight 失败时输出稳定 reason code
3. preflight 通过时不改变现有成功路径
4. 与 control rule 和 account binding 路径保持兼容
5. 通过少量集中代码实现，便于快速落地和测试

## 6. 方案比较

### 6.1 方案 A：在 `RedisExecutionTaskConsumer` 前增加轻量 preflight validator

做法：

- 新增一个聚焦 executor 入口的校验器
- 在 `RedisExecutionTaskConsumer.run()` 中、调用真实 `dispatcher.dispatch()` 前执行
- 失败时直接返回稳定 reason code，并写回任务状态

优点：

- 改动集中
- 易于测试
- 能立刻收紧 executor 入口
- 与现有 control rule / binding 逻辑自然衔接

缺点：

- 仍属于入口防线，不覆盖更深层交易所侧风险

### 6.2 方案 B：把校验直接散落在 `run()` 主流程里

做法：

- 不新增组件
- 在 `RedisExecutionTaskConsumer.run()` 中用条件分支直接校验

优点：

- 文件数最少

缺点：

- 主流程进一步膨胀
- 不利于单测
- 后续扩展会让 executor 入口越来越难维护

### 6.3 方案 C：直接重构成完整执行编排框架

做法：

- 把 preflight、下单、结果处理、repair 全部整合成新的执行编排器

优点：

- 终局能力更完整

缺点：

- 范围过大
- 会把本次“补入口边界”的小目标拉成大改造

### 6.4 推荐方案

本次采用方案 A。

原因：

- 它最符合当前阶段“小步快跑、紧贴已完成 binding 链路”的目标
- 可以先把 executor 入口标准化，再逐步演进到更完整的执行框架
- 改动面最小，最适合先做本地回归与主服务器复验

## 7. 核心设计

### 7.1 新增 `ExecutorPreflightValidator`

本次新增一个轻量 validator，职责只有一件事：

- 在 executor 调用真实 dispatch 前，对任务 payload 和解析结果做统一校验

建议位置：

- `app/runtime/live_workers.py`

首版保持轻量，避免为一个很小的职责再拆新模块；等后续规则增多，再考虑独立文件。

### 7.2 输入与输出

validator 输入包括：

- `payload`
- `execution_accounts_by_exchange`

其中：

- `payload` 是 executor 实际要执行的最终 payload，可能已经被 control rule 改写过 `target_quote_amount`
- `execution_accounts_by_exchange` 是账户真值解析结果；当 account truth 路径不可用时，可以是 `None`

validator 输出采用“通过或抛出带 reason 的异常”模式，和当前 executor 失败处理兼容。

建议新增一个轻量异常类，例如：

- `ExecutorPreflightError`

字段：

- `reason`
- `detail`

处理方式：

- `RedisExecutionTaskConsumer.run()` 捕获该异常
- 继续沿用现有 `mark_failed(..., reason=...)`

### 7.3 首版校验项

首版只做确定性、低歧义、无需外部状态的新校验：

1. 必要字段存在：
   - `task_uuid`
   - `user_id`
   - `symbol`
   - `buy_exchange`
   - `sell_exchange`
2. 双边交易所不能相同
3. `target_quote_amount` 必须能解析为正数
4. 如果 payload 带 `buy_account_id / sell_account_id`，则：
   - 必须已经拿到 `execution_accounts_by_exchange`
   - 解析结果必须同时包含 `buy_exchange` 与 `sell_exchange`
5. 若解析结果存在，则其账户 `exchange` 必须与 payload 指定交易所一致

说明：

- 首版不校验余额、持仓、滑点、费率、最小下单数量
- 首版不做基于市场行情的动态风控
- 首版不依赖新的数据库查询

### 7.4 失败码设计

本次为 preflight 增加稳定 reason code，首版包括：

- `executor_preflight_invalid_payload`
  - 必要字段缺失或为空
- `executor_preflight_same_exchange`
  - `buy_exchange == sell_exchange`
- `executor_preflight_invalid_amount`
  - `target_quote_amount` 非法或小于等于 `0`
- `executor_preflight_account_resolution_failed`
  - payload 需要 binding，但解析结果缺失或不完整
- `executor_preflight_account_exchange_mismatch`
  - 解析结果中的账户交易所与 payload 不一致

要求：

- `status_reason` 优先写这些稳定 code
- detail 文本只用于日志或事件，不作为主 reason

### 7.5 接入顺序

executor 入口顺序调整为：

1. 读消息
2. `mark_executing`
3. 执行 control rule
4. 解析账户真值
5. 执行 preflight validator
6. 调用真实 `dispatcher.dispatch()`
7. 成功则 `mark_succeeded`
8. 失败则 `mark_failed`

说明：

- preflight 放在 account truth 解析之后，这样可以同时校验 payload 和解析结果
- preflight 放在真实 dispatch 前，这样非法任务不会进入真实执行

### 7.6 与 control rule 的关系

control rule 仍先于 preflight 执行。

原因：

- control rule 已经是 executor 当前正式入口的一部分
- 它可能调整 `target_quote_amount`
- preflight 应校验“最终生效的执行 payload”，而不是原始消息

因此：

- 若 control rule 拦截，则沿用当前 `mark_blocked`
- 若 control rule 放行或缩量，则 preflight 基于 `effective_payload` 继续校验

### 7.7 与 account binding 的关系

preflight 不替代 `ExecutorAccountTruthResolver`，而是为其结果做最终兜底。

关系分工如下：

- resolver 负责“按账户真值解析出可执行账户”
- preflight 负责“确认 payload 与解析结果已满足进入真实 dispatch 的最低条件”

这样可以把“解析失败”和“入口前最终校验失败”都统一落成稳定 reason code。

## 8. 数据流

目标数据流如下：

1. executor 从 Redis 读取节点任务
2. control rule 判断是否阻断或缩量
3. account truth resolver 按 binding 或按交易所解析账户
4. preflight validator 校验 payload 与解析结果
5. 通过后再调用真实 dispatch
6. 失败时更新任务状态并发失败事件

这条链路保证 executor 不再把明显非法任务推到更深层逻辑中。

## 9. 错误处理

错误处理规则如下：

- control rule block：
  - 维持当前行为
  - `mark_blocked`
- account truth resolver 抛错：
  - 维持当前行为
  - 若异常自带 `reason`，沿用其稳定 reason code
- preflight validator 抛错：
  - `mark_failed`
  - `status_reason` 写 validator 提供的稳定 reason code
- 真实 dispatch 抛错：
  - 维持当前行为
  - 若异常无稳定 reason，则继续退化为异常文本

说明：

- 本次不试图统一所有历史异常的 reason code
- 本次只把 preflight 这一层的失败码标准化

## 10. 测试策略

本次至少补以下测试：

1. `tests/test_live_workers.py`
   - payload 缺少必要字段时，executor 标记失败并写 `executor_preflight_invalid_payload`
   - `buy_exchange == sell_exchange` 时，executor 标记失败并写 `executor_preflight_same_exchange`
   - `target_quote_amount <= 0` 时，executor 标记失败并写 `executor_preflight_invalid_amount`
   - binding 存在但解析结果缺失时，executor 标记失败并写 `executor_preflight_account_resolution_failed`
   - 解析账户交易所与 payload 不一致时，executor 标记失败并写 `executor_preflight_account_exchange_mismatch`
   - 合法 binding 路径继续成功，不影响已有成功测试

2. 如实现拆出独立 helper：
   - 对 validator 本身补充直接单元测试

回归要求：

- 不影响当前 `task-account-binding` 已通过的成功链路
- 不影响现有 control rule block / resize 行为

## 11. 验收标准

满足以下条件即可视为完成：

1. executor 新增统一 preflight 校验入口
2. 首版 5 个稳定 reason code 已落地
3. 非法任务不会进入真实 `dispatcher.dispatch()`
4. 相关测试通过
5. 已有 binding 成功链路与 control rule 链路不回归

## 12. 后续演进

本次完成后，后续可以继续沿以下方向推进，但不属于本次范围：

- 在 `TradeExecutor` 中补更细的执行结果结构化信息
- 统一真实 dispatch 层的失败码
- 引入余额、持仓、最小下单量等更强执行前风控
- 把 preflight、执行与 repair 组合成完整执行编排器
