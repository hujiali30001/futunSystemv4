# Executor 默认接入 Repair Publisher 设计

## 1. 文档目标

本文档定义如何把当前最小 `repair` 主链从“helper 中可闭环”推进到“默认 executor 装配路径也可闭环”，让 `WorkerApp(role=executor)` 在不需要额外手工注入的情况下，默认具备向 repair stream 发布 repair task 的能力。

本次目标是在不扩大到 repair systemd 部署、不扩展为完整线上联调链、也不重构现有 consumer/service 的前提下，完成以下能力：

- `DefaultWorkerFactory.build_executor_worker()` 默认注入 `RepairTaskPublisher`
- 默认 executor 在 `OPEN_PARTIAL + AUTO_HEDGE_REPAIRING` 时，除了发 `executor.repair_planned` 外，还会真实写入 `stream:repair_tasks:{node_id}`
- `WorkerApp(role=executor)` 的默认装配路径可直接产出 repair stream 消息

## 2. 范围

本次只做以下能力：

- 在默认 executor 装配路径中自动注入 `RepairTaskPublisher`
- 保持 `RedisExecutionTaskConsumer` 现有 repair publish 逻辑不变
- 为默认装配路径补 focused 测试
- 为 `WorkerApp(role=executor)` 补 focused 装配验证

本次不做以下能力：

- 不新增 repair systemd unit
- 不让 executor 自动同时拉起 repair worker
- 不扩展多进程联调
- 不改 repair task payload 协议
- 不改 `repair.task.finished` 契约
- 不改数据库模型
- 不做新的主服务器远端验证

## 3. 背景与现状

当前系统已经具备以下基础：

- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py)
  中 `RedisExecutionTaskConsumer` 已支持 `repair_task_publisher`
- executor 在 `OPEN_PARTIAL` 且 `repair_action != "NONE"` 时，已能调用 publisher 写入 repair stream
- [redis_flow.py](file:///d:/old/FuRunSystemV4/app/runtime/redis_flow.py)
  已提供 `RepairTaskPublisher`
- [worker_service.py](file:///d:/old/FuRunSystemV4/app/runtime/worker_service.py)
  已支持独立 `repair` worker role
- 本地测试已证明：
  - 手工注入 `RepairTaskPublisher` 时，executor 能发布 repair task
  - repair worker 能消费 repair task 并完成最小收口

但当前仍有一个关键缺口：

1. 默认 `build_executor_worker()` 还没有注入 `RepairTaskPublisher`
2. 这意味着 `WorkerApp(role=executor)` 默认只能发 `executor.repair_planned` 事件
3. 如果不手工注入 publisher，默认 executor 装配路径并不会把 repair task 写进 Redis

因此，本次需要补齐默认 executor 装配缺口。

## 4. 问题定义

如果继续保持现状，会有以下问题：

1. 当前 repair 闭环仍依赖“额外手工注入 publisher”这一非默认路径
2. 默认 `WorkerApp(role=executor)` 与本地/远端 helper 闭环路径不一致
3. 后续要补 repair systemd 或更接近真实生产链的联调时，还要先返工装配层

因此，本次需要优先把默认 executor 装配路径补齐。

## 5. 设计目标

本次设计满足以下目标：

1. 默认 executor 装配自动携带 `RepairTaskPublisher`
2. 不要求调用方手工传入 publisher
3. 保持现有 executor 与 repair 执行逻辑不重构
4. 用最小改动提升主链真实度

## 6. 方案比较

### 6.1 方案 A：只补默认 executor -> repair publisher 自动接线

做法：

- 修改 `DefaultWorkerFactory.build_executor_worker()`
- 在其中直接基于同一个 `redis_client` 创建 `RepairTaskPublisher`
- 注入到 `RedisExecutionTaskConsumer`

优点：

- 改动最小
- 命中当前真实缺口
- 不改变 runtime 责任边界

缺点：

- 仍未包含 repair service 的 systemd 部署

### 6.2 方案 B：默认 executor 自动接线 + repair systemd 一起补

做法：

- 除了自动接线，还同时新增 repair systemd 资产与运维文档

优点：

- 更接近完整生产形态

缺点：

- 范围扩大
- 容易把本轮从“装配缺口修补”变成“部署改造”

### 6.3 方案 C：继续保持手工注入，不改默认装配

做法：

- 保持当前状态
- 只有测试或 helper 手工传 publisher

优点：

- 完全零改动

缺点：

- 默认主链仍不完整
- 后续推进时迟早要补

### 6.4 推荐方案

本次采用方案 A。

原因：

- 它是当前最短、最高价值、最不容易扩散范围的缺口修补
- 它能把系统从“helper 可闭环”推进到“默认 executor 路径也可闭环”
- 它为后续 repair systemd 和更接近真实线上联调打下基础

## 7. 核心设计

### 7.1 默认 executor 装配自动注入 publisher

本次修改点集中在：

- `DefaultWorkerFactory.build_executor_worker()`

在现有 executor consumer 构造时，默认加入：

- `repair_task_publisher=RepairTaskPublisher(redis_client)`

这样：

- executor 与 repair publisher 共用同一个 Redis 连接来源
- 不需要额外新增配置项
- 不需要调用方手工注入 publisher

### 7.2 现有 consumer 行为保持不变

本次不修改 `RedisExecutionTaskConsumer` 的业务条件判断：

- 仍然只在 `execution_status == OPEN_PARTIAL`
- 且 `failed_exchanges` 非空
- 且 `repair_plan.action != "NONE"`
- 且 `repair_task_publisher is not None`

时发布 repair task。

本轮变化只是：

- 默认装配时，`repair_task_publisher` 不再为空

### 7.3 数据流

默认链路会变成：

1. `WorkerApp(role=executor)` 启动
2. `DefaultWorkerFactory.build_executor_worker()` 自动注入 `RepairTaskPublisher`
3. executor 消费 `stream:spot_exec_tasks:{node_id}`
4. 当执行结果为 `OPEN_PARTIAL` 且满足 repair 条件时：
   - 发出 `executor.repair_planned`
   - 同时写入 `stream:repair_tasks:{node_id}`

本次不要求：

- 默认 executor 同时拉起 repair worker
- 默认路径里立刻消费 repair stream

### 7.4 与现有 repair worker 的关系

本次只补 repair 输入发布链，不改 repair 消费链。

职责分离如下：

- executor：
  - 负责执行结果
  - 负责 repair 计划
  - 负责发布 repair task
- repair worker：
  - 负责消费 repair task
  - 负责执行最小自动补单
  - 负责发出 `repair.task.finished`

### 7.5 测试策略

本次只补 focused 测试，至少覆盖以下事实：

1. `build_executor_worker()` 默认会注入 `RepairTaskPublisher`
2. `WorkerApp(role=executor)` 启动后，executor consumer 上持有 publisher
3. 默认装配路径下，现有 repair task 发布逻辑不回归

建议重点测试：

- `test_build_executor_worker_uses_repair_task_publisher_by_default`
- `test_worker_app_runs_executor_role_with_repair_task_publisher`
- 若必要，再补一个基于默认 factory 构造的 smoke test，确认 `consumer.repair_task_publisher` 非空

### 7.6 配置与兼容性

本次不新增环境变量。

继续复用：

- `resolved_executor_stream_key`
- `resolved_repair_stream_key`
- 现有 `redis_client`

因此：

- 不需要迁移配置
- 不会改变现有 `.env.worker` 结构

### 7.7 风险与边界控制

本次主要风险是：

- 默认 executor 启动后会比以前多写一条 repair stream 消息

但这是本次设计的目标本身，不是副作用。

为控制风险，本次明确不做：

- repair worker 自动启动
- repair systemd 自动部署
- 远端默认联调链改造

## 8. 错误处理

本次不新增新的业务异常分支。

若 `RepairTaskPublisher(redis_client)` 构造成功，则 executor 侧发布行为仍沿用现有异常路径。

本轮重点是：

- 保持装配层尽量薄
- 不在 `build_executor_worker()` 中新增复杂条件判断

## 9. 验收标准

满足以下条件即可视为完成：

1. `DefaultWorkerFactory.build_executor_worker()` 默认注入 `RepairTaskPublisher`
2. `WorkerApp(role=executor)` 运行时，consumer 上持有 publisher
3. 现有 repair task 发布行为不回归
4. 现有 executor / repair / worker_service 相关测试不回归

## 10. 后续演进

本次完成后，后续可以继续沿以下方向推进，但不属于本次范围：

- 为 repair worker 新增 systemd 单元
- 让 executor 与 repair role 在更接近生产的部署形态下联调
- 为默认主链增加主服务器远端验证
