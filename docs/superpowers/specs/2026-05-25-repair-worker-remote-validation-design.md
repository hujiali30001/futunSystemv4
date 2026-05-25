# Repair Worker 主服务器远端验证设计

## 1. 文档目标

本文档定义如何在主服务器对当前最小 `repair worker` 做一次与本地实现解耦、且可重复执行的远端闭环验证，确认系统已经能够从 executor 的部分成交结果出发，发布 repair task，并由 repair worker 消费后完成最小 repair 收口。

本次目标是在不触发真实交易所下单、不依赖现网 systemd 服务状态、也不扩大到完整生产联调链的前提下，验证以下事实：

- `OPEN_PARTIAL` 执行结果能够触发 repair task 发布
- repair worker 能消费 repair task 并发出 `repair.task.finished`
- repair 成功路径会把任务收口到 `SUCCEEDED + OPEN_HEDGED`
- repair 失败路径会把任务收口到 `FAILED + OPEN_PARTIAL + manual_required`
- 非 repair 场景不会误发 repair task 或 `repair.task.finished`

## 2. 范围

本次只做以下能力：

- 为最小 `repair worker` 新增主服务器远端验证 helper
- 在一个 helper 中串起 executor 段与 repair worker 段
- 定义 `repair_success / repair_failure / no_repair_publish` 三个 canary 场景
- 定义本地同步入口脚本
- 定义远端输出 JSON 的落盘方式
- 定义运维文档中的验证记录落点
- 定义验收标准

本次不做以下能力：

- 不走真实交易所下单
- 不依赖 `furun-spot-executor.service` 或未来 repair service 正在运行
- 不校验真实 systemd 日志采集链
- 不扩展默认 `WorkerApp` 线上装配方式
- 不新增 repair 专用持久化表
- 不把 helper 变成完整生产联调工具

## 3. 背景与现状

当前系统已经具备以下基础：

- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py)
  中 `RedisExecutionTaskConsumer` 已能在 `OPEN_PARTIAL` 下：
  - 发出 `executor.repair_planned`
  - 发布 repair task 到 `stream:repair_tasks:{node_id}`
- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py)
  中 `RedisRepairTaskConsumer` 已能消费 repair task，并发出 `repair.task.finished`
- [repair_execution_service.py](file:///d:/old/FuRunSystemV4/app/runtime/repair_execution_service.py)
  已提供最小单腿补单服务
- [task_repository.py](file:///d:/old/FuRunSystemV4/app/db/task_repository.py)
  已提供 `mark_repair_result(...)`
- [worker_service.py](file:///d:/old/FuRunSystemV4/app/runtime/worker_service.py)
  已支持 `repair` worker role

本地测试当前已经锁定以下事实：

- executor 会发布 repair task
- repair worker 成功路径会收口到 `SUCCEEDED + OPEN_HEDGED`
- repair worker 失败路径会收口到 `FAILED + OPEN_PARTIAL + manual_required`
- repair role 已能在 `WorkerApp` 中装配启动

但当前仍有一个明显缺口：

1. 以上能力只在本地测试中通过，还没有主服务器闭环记录
2. 运维文档中还没有 repair worker 这条链的远端验证章节
3. 后续若要继续推进 systemd 部署或更复杂 repair 策略，缺少一条主服务器基线

因此，本次需要补一次与前几轮风格一致的主服务器 helper canary 验证。

## 4. 问题定义

如果不做这次远端验证，会有以下问题：

1. 无法确认主服务器环境中的代码、虚拟环境和 helper 运行方式能否真的串起 repair 双消费者链
2. 运维文档缺少 repair worker 的复跑步骤与实测记录
3. 后续如果 repair 链在远端没有结果，很难区分是实现缺陷、部署遗漏还是验证缺口

因此，本次需要提供一条最短路径的主服务器 repair 闭环验证链。

## 5. 设计目标

本次设计满足以下目标：

1. 主服务器可以重复验证 repair 双消费者链
2. 验证链尽量贴近真实 runtime 主路径，而不是只测 builder
3. 验证过程不依赖 systemd 当前状态
4. 验证结果可落到 JSON 文件与运维文档中，供后续复跑

## 6. 方案比较

### 6.1 方案 A：在主服务器 helper 中手工串起 executor 与 repair 双消费者

做法：

- 在远端 helper 中直接实例化：
  - `RedisExecutionTaskConsumer`
  - `RepairTaskPublisher`
  - `RedisRepairTaskConsumer`
  - fake Redis / fake event router / fake task repository
- 让一条 fake 执行任务从 executor 段流入 repair 段

优点：

- 最接近当前真实闭环
- 不依赖默认 `WorkerApp` 线上装配缺口
- 不依赖 systemd 当前状态
- 可以一次性验证 repair 输入发布与 repair 执行收口

缺点：

- helper 复杂度比单消费者 canary 略高

### 6.2 方案 B：只远端验证 repair worker 消费端

做法：

- 直接向 repair stream 喂一条 repair task
- 只验证 `RedisRepairTaskConsumer`

优点：

- 最轻量

缺点：

- 无法证明 `executor -> repair stream` 这段在主服务器也成立
- 对整体闭环覆盖不完整

### 6.3 方案 C：依赖默认 `WorkerApp` 多角色联调

做法：

- 主服务器直接拉起 `executor` 与 `repair` role 做 canary
- 观察实际流转

优点：

- 更接近最终生产形态

缺点：

- 依赖更多环境状态
- 当前默认 executor 装配并未天然包含完整远端验证友好性
- 假失败概率更高，不适合作为这轮最短闭环

### 6.4 推荐方案

本次采用方案 A。

原因：

- 它在真实性与成本之间最平衡
- 它能一次性验证 repair 链最关键的三段：
  - `executor.repair_planned`
  - `stream:repair_tasks:{node_id}`
  - `repair.task.finished`
- 它与此前 execution result / repair planned 远端 helper 路线保持一致

## 7. 核心设计

### 7.1 远端 helper 形态

新增一个主服务器 helper，例如：

- `.tmp-ssh/repair_worker_remote_helper.py`

其职责是：

- 在远端手工构造一条 fake 执行任务
- 先运行 `RedisExecutionTaskConsumer`
- 再运行 `RedisRepairTaskConsumer`
- 收集结构化事件、repair stream 消息、任务摘要最终状态
- 打印结构化 JSON 结果供本地同步脚本消费

helper 不负责：

- 修改系统服务
- 重启 systemd
- 写入真实业务数据库
- 走真实交易所下单

### 7.2 本地同步入口

新增一个本地同步脚本，例如：

- `.tmp-ssh/sync_and_validate_repair_worker.py`

其职责是：

- 同步最小必要 runtime 文件到主服务器
- 上传远端 helper
- 在主服务器项目虚拟环境中执行 helper
- 将返回 JSON 友好打印到本地终端
- 将完整 JSON 结果保存到本地文件

### 7.3 同步文件范围

首版只同步最小文件集合：

- `app/runtime/live_workers.py`
- `app/runtime/redis_flow.py`
- `app/runtime/repair_execution_service.py`
- `app/runtime/runtime_events.py`
- `app/db/task_repository.py`

如果 helper 直接复用仓库内更多依赖，再按实际 import 最小补齐，但不做整仓同步。

### 7.4 Fake 依赖设计

远端 helper 内部建议定义以下最小 fake：

- `FakeRedis`
  - 支持 `xread(...)`
  - 支持 `xadd(...)`
  - 能缓存 `stream:repair_tasks:{node_id}` 里发布的消息
- `FakeEventRouter`
  - `dispatch(event)` 只收集事件
- `FakeTaskRepository`
  - 提供：
    - `mark_executing`
    - `mark_execution_result`
    - `mark_failed`
    - `mark_repair_result`
  - 记录最终任务状态
- `FakeExecutionDispatcherService`
  - 模拟 executor 段执行结果
- `FakeRepairExecutionService`
  - 模拟 repair 成功或失败结果

这样可以保证：

- 仍走真实 `RedisExecutionTaskConsumer.run()` 与 `RedisRepairTaskConsumer.run()` 主流程
- 但不接真实 Redis、数据库和交易所

### 7.5 验证场景

helper 至少覆盖以下 3 个模式：

#### 1. `repair_success`

输入：

- 一条合法执行消息
- executor 段返回：
  - `execution_status = OPEN_PARTIAL`
  - `filled_exchanges = ["okx"]`
  - `failed_exchanges = ["gate"]`
- repair 段返回：
  - `status = REPAIRED`

断言：

- `processed_executor = 1`
- `processed_repair = 1`
- 存在 1 条 repair task 消息
- 存在 1 条 `executor.repair_planned`
- 存在 1 条 `repair.task.finished`
- 最终任务状态至少满足：
  - `task_status = SUCCEEDED`
  - `task_execution_status = OPEN_HEDGED`
  - `task_status_reason = null`

#### 2. `repair_failure`

输入：

- 一条合法执行消息
- executor 段返回：
  - `execution_status = OPEN_PARTIAL`
  - `filled_exchanges = ["okx"]`
  - `failed_exchanges = ["gate"]`
- repair 段返回：
  - `status = MANUAL_REQUIRED`
  - `reason = repair order failed`

断言：

- `processed_executor = 1`
- `processed_repair = 1`
- 存在 1 条 repair task 消息
- 存在 1 条 `repair.task.finished`
- 最终任务状态至少满足：
  - `task_status = FAILED`
  - `task_execution_status = OPEN_PARTIAL`
  - `task_status_reason = manual_required`

#### 3. `no_repair_publish`

输入：

- 一条合法执行消息
- executor 段返回：
  - `execution_status = OPEN_HEDGED`
  - `failed_exchanges = []`

断言：

- `processed_executor = 1`
- `processed_repair = 0`
- `repair_task_messages = []`
- `repair_finished_events = []`
- 不把非 repair 场景误当成 repair 执行

### 7.6 远端输出结构

helper 最终打印的 JSON 建议至少包含：

- `repair_success`
- `repair_failure`
- `no_repair_publish`

每个模式下至少输出：

- `processed_executor`
- `processed_repair`
- `repair_task_messages`
- `repair_planned_events`
- `repair_finished_events`
- `task_status`
- `task_execution_status`
- `task_status_reason`

其中：

- `repair_task_messages`
  - 只保留写入 `stream:repair_tasks:{node_id}` 的 payload
- `repair_planned_events`
  - 只保留 `event_type == "executor.repair_planned"` 的事件字典
- `repair_finished_events`
  - 只保留 `event_type == "repair.task.finished"` 的事件字典

这样本地同步脚本可以直接把关键结果打印出来，不需要再二次解析日志文本。

### 7.7 本地落盘文件

本次同步脚本执行成功后，将完整结果保存到：

- `.tmp-ssh/repair_worker_remote_output.json`

该文件用途是：

- 作为文档回填依据
- 为后续复跑提供可比对样本
- 为 GitHub 推送前的人工复核保留证据

### 7.8 与 systemd / 线上服务的关系

本次刻意不依赖线上 `executor` 或 `repair` systemd 服务。

原因：

- 这轮要验证的是 repair 双消费者实现本身，而不是服务部署状态
- 如果直接依赖默认服务形态，容易被环境状态、流量噪音和装配差异影响
- helper 方式更适合做可重复、可控制的 canary

因此，本次结论应表述为：

- “主服务器代码与虚拟环境下，最小 repair worker 双消费者闭环已通过”

而不是：

- “线上 systemd repair 链已完整演练”

### 7.9 文档落点

验证通过后，在
[live-workers-systemd.md](file:///d:/old/FuRunSystemV4/docs/ops/live-workers-systemd.md)
新增一个独立小节：

- `Repair Worker Validation`

记录内容至少包括：

- 验证方式是主服务器 helper，而非真实下单
- `repair_success` 已通过
- `repair_failure` 已通过
- `no_repair_publish` 非误发已通过
- helper、同步脚本和输出 JSON 路径

## 8. 数据流

目标数据流如下：

1. 本地运行同步脚本
2. 同步最小 runtime 文件与远端 helper 到主服务器
3. 远端 helper 构造 fake Redis、fake repository、fake services
4. helper 调用 `RedisExecutionTaskConsumer.run()`
5. executor 段把 repair task 写入 fake `stream:repair_tasks:{node_id}`
6. helper 再调用 `RedisRepairTaskConsumer.run()`
7. repair 段发出 `repair.task.finished`
8. helper 输出 JSON
9. 本地把 JSON 保存到文件
10. 根据结果补运维文档

## 9. 错误处理

错误处理规则如下：

- 如果 helper 执行报 import 或环境错误：
  - 先视为远端环境问题
  - 不直接判定业务逻辑失败
- 如果 `repair_success` 未产生 repair task 或 `repair.task.finished`：
  - 视为本次功能远端闭环失败
- 如果 `repair_failure` 未收口到 `manual_required`：
  - 视为状态写回语义失败
- 如果 `no_repair_publish` 产生了 repair task 或 `repair.task.finished`：
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
- 获取 `repair_success / repair_failure / no_repair_publish` 三段 JSON 结果
- 对照 spec 中的字段断言人工复核

### 10.3 文档回填

- 将主服务器实测结果写入运维文档
- 记录 helper、同步脚本、输出 JSON 路径和验证范围说明

## 11. 验收标准

满足以下条件即可视为完成：

1. 主服务器 helper 能成功运行
2. `repair_success` 场景完成：
   - repair task 发布
   - `repair.task.finished` 发出
   - 任务收口到 `SUCCEEDED + OPEN_HEDGED`
3. `repair_failure` 场景完成：
   - repair task 发布
   - `repair.task.finished` 发出
   - 任务收口到 `FAILED + OPEN_PARTIAL + manual_required`
4. `no_repair_publish` 场景不产生 repair task 与 `repair.task.finished`
5. 本地保存远端 JSON 输出
6. 运维文档完成记录

## 12. 后续演进

本次完成后，后续可以继续沿以下方向推进，但不属于本次范围：

- 修补默认 `WorkerApp executor -> repair` 自动接线
- 增加 repair worker 的 systemd 部署资产
- 验证真实 systemd 日志链是否也能稳定看到 repair 结果
- 继续扩展更复杂的 repair 策略与恢复优先级链
