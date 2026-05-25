# Executor Repair Planned Event 远端验证设计

## 1. 文档目标

本文档定义如何在主服务器对 `executor.repair_planned` 做一次与本地实现解耦、且可重复执行的远端闭环验证，确认 executor 在真实消费执行消息时，能够把 `OPEN_PARTIAL` 对应的 repair 计划正确发成结构化运行时事件。

本次目标是在不触发真实交易所下单、不依赖现网 systemd 服务状态、也不扩大到真实 repair worker 或新持久化设计的前提下，验证以下事实：

- `OPEN_PARTIAL` 且存在 repair 动作时，会发出 `executor.repair_planned`
- `OPEN_HEDGED` 不会误发 `executor.repair_planned`
- preflight 失败不会误发 `executor.repair_planned`
- 远端事件 payload 与本地测试锁定的字段语义一致

## 2. 范围

本次只做以下能力：

- 为 `executor.repair_planned` 新增主服务器远端验证 helper
- 定义远端 helper 的 canary 场景与输出结构
- 定义本地同步入口脚本
- 定义远端输出 JSON 的落盘方式
- 定义运维文档中的验证记录落点
- 定义验收标准

本次不做以下能力：

- 不走真实交易所下单
- 不依赖 `furun-spot-executor.service` 正在运行
- 不校验真实 systemd 日志采集链
- 不新增 repair worker
- 不新增事件持久化、repair 表或报表
- 不修改 `RiskManager.build_repair_plan(...)` 规则

## 3. 背景与现状

当前系统已经具备以下基础：

- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py)
  中 `RedisExecutionTaskConsumer` 已新增 `executor.repair_planned`
- [test_live_workers.py](file:///d:/old/FuRunSystemV4/tests/test_live_workers.py)
  已通过本地 TDD 锁定：
  - `OPEN_PARTIAL` 会发 `executor.repair_planned`
  - `OPEN_HEDGED` 不发
  - preflight 失败不发
- [executor.repair_planned 事件设计](file:///d:/old/FuRunSystemV4/docs/superpowers/specs/2026-05-25-executor-repair-planned-event-design.md)
  已定义事件契约与触发时机
- [live-workers-systemd.md](file:///d:/old/FuRunSystemV4/docs/ops/live-workers-systemd.md)
  已沉淀过：
  - execution result summary 远端验证
  - spot probe richer result 远端验证
  - `executor.execution_result` 远端验证

但当前仍有一个缺口：

1. `executor.repair_planned` 只在本地测试里通过，还没有主服务器闭环记录
2. 运维文档中还没有这类 repair 编排事件的远端验证章节
3. 后续如果要继续接 repair worker 或联调日志链，缺少一次“远端事件确实能发出来”的基线

因此，本次需要补一次与前几轮风格一致的主服务器远端验证。

## 4. 问题定义

如果不做这次远端验证，会有以下问题：

1. 无法确认主服务器环境中的代码、虚拟环境和 helper 运行方式是否真的能产出该事件
2. 运维文档缺少 `executor.repair_planned` 的复跑步骤
3. 后续如果远端日志没有该事件，很难区分是实现缺陷、部署遗漏还是验证缺口

因此，本次需要提供一条最短路径的远端 canary 验证链。

## 5. 设计目标

本次设计满足以下目标：

1. 主服务器可以重复验证 `executor.repair_planned`
2. 验证链尽量贴近真实 executor 主循环，而不是只调用纯 builder
3. 验证过程不依赖现网 systemd 服务状态
4. 验证结果可落到 JSON 文件与运维文档中，供后续复跑

## 6. 方案比较

### 6.1 方案 A：主服务器上直接拉起 `RedisExecutionTaskConsumer` canary helper

做法：

- 在远端 helper 中直接实例化：
  - `RedisExecutionTaskConsumer`
  - `FakeSpotService`
  - `FakeEventRouter`
  - `FakeRedis`
  - `FakeTaskRepository`
- 直接喂一条执行消息并收集结构化事件

优点：

- 最接近真实 executor 主循环
- 不依赖 systemd 服务状态
- 不会被 `journalctl` 截断或混入现网噪音
- 与前几轮 helper 风格一致

缺点：

- 需要再维护一份远端 helper 脚本

### 6.2 方案 B：直接依赖 `furun-spot-executor.service` 和 `journalctl`

做法：

- 向远端执行流写 canary 消息
- 从 `journalctl` 中抓取 `executor.repair_planned`

优点：

- 更接近完整线上运行形态

缺点：

- 依赖服务当前状态、日志时序和外部噪音
- 更容易出现假失败
- 不适合作为这轮最小闭环

### 6.3 方案 C：只在远端调用 `_build_executor_repair_planned_event(...)`

做法：

- 直接 import builder
- 构造 payload、`repair_plan` 和结果字段，校验返回 `RuntimeEvent`

优点：

- 最轻量

缺点：

- 无法验证主循环里的触发时机
- 无法证明 preflight 失败不会误发
- 过于脱离实际消费路径

### 6.4 推荐方案

本次采用方案 A。

原因：

- 它在成本与真实性之间最平衡
- 它能验证 `dispatch -> build_repair_plan -> event_router.dispatch(...)` 的完整链路
- 它与此前 execution summary / richer result / `executor.execution_result` 的远端 helper 路线一致

## 7. 核心设计

### 7.1 远端 helper 形态

新增一个主服务器 helper，例如：

- `.tmp-ssh/executor_repair_planned_event_remote_helper.py`

其职责是：

- 在远端直接实例化 `RedisExecutionTaskConsumer`
- 使用 fake 依赖隔离真实外部系统
- 分别执行 3 类 canary 场景
- 打印结构化 JSON 结果供本地同步脚本消费

helper 不负责：

- 修改系统服务
- 重启 systemd
- 写入真实业务数据库

### 7.2 本地同步入口

新增一个本地同步脚本，例如：

- `.tmp-ssh/sync_and_validate_executor_repair_planned_event.py`

其职责是：

- 同步最小必要文件到主服务器
- 上传远端 helper
- 在主服务器项目虚拟环境中执行 helper
- 将返回 JSON 友好打印到本地终端
- 将完整 JSON 结果保存到本地文件

### 7.3 同步文件范围

首版只同步最小文件集合：

- `app/runtime/live_workers.py`
- `app/runtime/runtime_events.py`

如果 helper 直接复用仓库内更多 runtime 依赖，再按实际 import 最小补齐，但不做整仓同步。

### 7.4 Fake 依赖设计

远端 helper 内部建议定义最小 fake：

- `FakeRedis`
  - 提供 `xread(...)`
  - 按测试场景返回单条消息
- `FakeSpotService`
  - `run_task(...)` 直接返回预设结果对象
- `FakeEventRouter`
  - `dispatch(event)` 只收集事件
- `FakeTaskRepository`
  - 提供 executor 主循环可能访问的方法：
    - `mark_executing`
    - `mark_execution_result`
    - `mark_failed`

这样可以保证：

- 仍走真实 `RedisExecutionTaskConsumer.run()` 主流程
- 但不接真实 Redis、数据库和交易所

### 7.5 验证场景

helper 至少覆盖以下 3 个模式：

#### 1. `partial_with_repair`

输入：

- 一条合法执行消息
- `FakeSpotService` 返回 `OPEN_PARTIAL`

断言：

- `processed = 1`
- 存在 1 条 `executor.repair_planned`
- 该事件至少包含：
  - `execution_status = OPEN_PARTIAL`
  - `filled_exchanges = ["okx"]`
  - `failed_exchanges = ["gate"]`
  - `repair_action = AUTO_HEDGE_REPAIRING`
  - `repair_reason = one_leg_failed`
  - `target_exchanges = ["gate"]`

#### 2. `hedged_no_repair`

输入：

- 一条合法执行消息
- `FakeSpotService` 返回 `OPEN_HEDGED`

断言：

- `processed = 1`
- `repair_planned_events = []`
- 可以看到正常 `executor.execution_result`
- 不把 fully hedged 结果误当成 repair 计划

#### 3. `preflight`

输入：

- 一条 `buy_exchange == sell_exchange` 的非法执行消息

断言：

- `processed = 0`
- `repair_planned_events = []`
- `execution_result_events = []`
- 仍可看到失败路径被处理，但不把 preflight 误当成真实 repair 编排

### 7.6 远端输出结构

helper 最终打印的 JSON 建议至少包含：

- `partial_with_repair`
- `hedged_no_repair`
- `preflight`

每个模式下至少输出：

- `processed`
- `repair_planned_events`
- `execution_result_events`
- `processed_events`
- `failed_events`
- `repository_execution_results`
- `repository_failures`

其中：

- `repair_planned_events`
  - 只保留 `event_type == "executor.repair_planned"` 的事件字典
- `execution_result_events`
  - 只保留 `event_type == "executor.execution_result"` 的事件字典
- `processed_events`
  - 只保留 `executor.task.processed`
- `failed_events`
  - 只保留 `executor.task.failed`

这样本地同步脚本可以直接把关键结果打印出来，不需要再二次解析日志文本。

### 7.7 本地落盘文件

本次同步脚本执行成功后，将完整结果保存到：

- `.tmp-ssh/executor_repair_planned_event_remote_output.json`

该文件用途是：

- 作为文档回填依据
- 为后续复跑提供可比对样本
- 为 GitHub 推送前的人工复核保留证据

### 7.8 与 systemd / 线上服务的关系

本次刻意不依赖线上 `furun-spot-executor.service`。

原因：

- 这轮要验证的是事件实现本身，而不是服务部署状态
- 如果直接走 `journalctl`，很容易掺杂其他流量或被日志截断影响
- helper 方式更适合做可重复、可控制的 canary

因此，本次结论应表述为：

- “主服务器代码与虚拟环境下，`executor.repair_planned` 事件闭环已通过”

而不是：

- “线上 systemd 日志链路已完整演练”

### 7.9 文档落点

验证通过后，在
[live-workers-systemd.md](file:///d:/old/FuRunSystemV4/docs/ops/live-workers-systemd.md)
新增一个独立小节：

- `Executor Repair Planned Event Validation`

记录内容至少包括：

- 验证方式是主服务器 helper，而非真实下单
- `partial_with_repair` 已通过
- `hedged_no_repair` 非误发已通过
- `preflight` 非误发已通过
- helper、同步脚本和输出 JSON 路径

## 8. 数据流

目标数据流如下：

1. 本地运行同步脚本
2. 同步最小 runtime 文件与远端 helper 到主服务器
3. 远端 helper 构造 fake 依赖
4. helper 调用 `RedisExecutionTaskConsumer.run()`
5. `FakeSpotService` 返回预设执行结果
6. `FakeEventRouter` 收集事件
7. helper 输出 JSON
8. 本地把 JSON 保存到文件
9. 根据结果补运维文档

## 9. 错误处理

错误处理规则如下：

- 如果 helper 执行报 import 或环境错误：
  - 先视为远端环境问题
  - 不直接判定业务逻辑失败
- 如果 `partial_with_repair` 未产生 `executor.repair_planned`
  - 视为本次功能远端闭环失败
- 如果 `hedged_no_repair` 或 `preflight` 产生了 `executor.repair_planned`
  - 视为严重语义回归
- 如果 helper 输出格式缺少关键字段：
  - 视为脚本设计不完整
  - 需要先修 helper，再重复验证

## 10. 测试策略

本次至少执行以下验证：

### 10.1 本地脚本自检

- `python -m py_compile` 检查：
  - 远端 helper
  - 本地同步脚本

### 10.2 主服务器远端 canary

- 运行本地同步脚本
- 获取 `partial_with_repair / hedged_no_repair / preflight` 三段 JSON 结果
- 对照 spec 中的字段断言人工复核

### 10.3 文档回填

- 将主服务器实测结果写入运维文档
- 记录 helper、同步脚本、输出 JSON 路径和验证范围说明

## 11. 验收标准

满足以下条件即可视为完成：

1. 主服务器 helper 能成功运行
2. `partial_with_repair` 场景出现 `executor.repair_planned`
3. `hedged_no_repair` 场景不出现 `executor.repair_planned`
4. preflight 场景不出现 `executor.repair_planned`
5. 远端输出字段与本地测试契约一致
6. 本地保存远端 JSON 输出
7. 运维文档完成记录

## 12. 后续演进

本次完成后，后续可以继续沿以下方向推进，但不属于本次范围：

- 验证 `journalctl` 中真实 systemd 日志链路是否也能稳定看到该事件
- 让 repair worker 或运维报表直接消费 `executor.repair_planned`
- 把 repair 编排同步落到专用表
- 建立统一的 runtime event 远端联调工具集合
