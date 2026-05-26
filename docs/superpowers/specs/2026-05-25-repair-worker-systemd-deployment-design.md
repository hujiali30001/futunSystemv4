# Repair Worker Systemd 部署设计

## 1. 文档目标

本文档定义如何为当前最小 `repair worker` 补齐默认部署资产，让系统从“代码已实现、helper 可闭环、默认 executor 会产出 repair task”推进到“repair role 可以作为常驻 systemd 服务被部署和运行”。

本次目标是在不扩大到真实线上联调、不新增复杂部署编排、也不重构 runtime 逻辑的前提下，完成以下能力：

- 新增 `furun-spot-repair.service`
- 复用现有 `.env.worker` 配置口径运行 `WorkerApp(role=repair)`
- 更新运维文档中的拓扑、安装、启停与最小验收步骤

## 2. 范围

本次只做以下能力：

- 在 `deploy/systemd/` 下新增 repair worker service 文件
- 复用现有 `.env.worker` 配置模型
- 更新 `.env.worker.example`
- 更新 `docs/ops/live-workers-systemd.md`
- 让 repair role 部署说明与现有 scanner / dispatcher / executor 一致

本次不做以下能力：

- 不修改 `repair worker` 业务逻辑
- 不修改 Redis stream 协议
- 不新增 `REPAIR_*` 专用环境变量
- 不做新的主服务器真实联调
- 不做新的 helper canary
- 不新增更复杂的多进程编排

## 3. 背景与现状

当前系统已经具备以下基础：

- 默认 `executor` 路径已会写入 `stream:repair_tasks:{node_id}`
- `repair` worker role 已能被 `WorkerApp` 启动
- `RedisRepairTaskConsumer` 与 `RuntimeRepairExecutionService` 已可消费 repair task 并执行最小补单
- helper canary 已完成：
  - repair 成功闭环
  - repair 失败闭环
  - no repair 非误发

当前部署资产现状如下：

- `deploy/systemd/` 下已有：
  - `furun-spot-scanner.service`
  - `furun-spot-consumer.service`
  - `furun-spot-dispatcher.service`
  - `furun-spot-executor.service`
- 但还没有：
  - `furun-spot-repair.service`

因此，当前 repair 主链虽然代码可运行，但默认部署形态还不完整。

## 4. 问题定义

如果继续保持现状，会有以下问题：

1. 默认 executor 已能发布 repair task，但默认部署层没有 repair 常驻消费方
2. 运维文档里缺少 repair role 的安装、启停和验收步骤
3. 后续若要推进更接近真实生产形态的联调，还要先补一轮部署资产

因此，本次需要优先补齐 repair worker 的 systemd 部署资产。

## 5. 设计目标

本次设计满足以下目标：

1. repair role 能像 executor 一样通过 systemd 常驻运行
2. 部署方式尽量复用现有 executor 资产与运维口径
3. 配置模型保持统一，不新增额外环境变量体系
4. 运维文档给出可直接执行的最小验收步骤

## 6. 方案比较

### 6.1 方案 A：只补 repair systemd 部署资产

做法：

- 新增 `furun-spot-repair.service`
- 更新 `.env.worker.example`
- 更新运维文档

优点：

- 改动最小
- 直接命中部署缺口
- 不会扩大到运行时业务改造

缺点：

- 尚未包含真实线上联调结果

### 6.2 方案 B：repair systemd + 主服务器真实联调

做法：

- 在方案 A 基础上，再把 repair role 在主服务器启动并联调

优点：

- 更接近生产闭环

缺点：

- 容易受环境状态影响
- 会把本轮从“补部署资产”扩大成“线上运行联调”

### 6.3 方案 C：继续只保留 helper 验证，不补 systemd

做法：

- 保持当前状态
- repair role 只停留在代码与 helper 层面

优点：

- 零部署改动

缺点：

- 默认运行形态仍不完整
- 后续迟早还要补

### 6.4 推荐方案

本次采用方案 A。

原因：

- 它是当前最短、最高价值、最不容易扩散范围的缺口修补
- 它能把系统从“repair 代码可运行”推进到“repair 可部署运行”
- 它为后续真实联调提供必要前提

## 7. 核心设计

### 7.1 新增 repair systemd unit

新增文件：

- `deploy/systemd/furun-spot-repair.service`

整体形态对齐现有 `furun-spot-executor.service`：

- `WorkingDirectory` 保持一致
- `EnvironmentFile` 继续使用 `.env.worker`
- `Restart=always`
- `RestartSec=3`

唯一核心差异是：

- `ExecStart` 改为：
  - `python -m app.runtime.worker_service --role repair`

### 7.2 配置口径保持统一

本次不新增新的 `.env` 文件，也不新增 repair 专用环境变量。

继续复用当前 `.env.worker` 中已有配置：

- `REDIS_URL`
- `ENV_MODE`
- `WORKER_ROLE`
- `WORKER_REGION`
- `NODE_ID`
- `REPAIR_STREAM_KEY`
- 交易所凭证与代理配置

因此 repair role 的配置方式为：

- 在 repair 节点或 repair 服务配置中，设置 `WORKER_ROLE=repair`

### 7.3 stream key 口径

本次继续沿用当前 runtime 约定：

- `REPAIR_STREAM_KEY`
- 默认值为：
  - `stream:repair_tasks:{node_id}`

systemd 部署层不需要新增新的 stream 规则说明，只要在文档里明确：

- repair role 默认消费 `resolved_repair_stream_key`

### 7.4 部署拓扑更新

运维文档拓扑需从：

- 主服务器：`scanner + dispatcher`
- 专用执行节点：`executor`

扩展为：

- 主服务器：`scanner + dispatcher`
- 专用执行节点：`executor`
- repair 节点或同类执行节点：`repair`

本次不强制规定 repair 与 executor 必须同机或分机，只要求：

- repair role 已具备标准 systemd 运行资产

### 7.5 文档更新点

`docs/ops/live-workers-systemd.md` 至少补以下内容：

1. 文件清单
   - 补上 `deploy/systemd/furun-spot-repair.service`
2. 拓扑说明
   - 补上 repair role
3. `.env.worker.example`
   - 增加 repair 相关 role 示例或明确 `WORKER_ROLE=repair`
4. 安装步骤
   - `cp deploy/systemd/furun-spot-repair.service /etc/systemd/system/`
5. 启停步骤
   - `systemctl enable/restart furun-spot-repair.service`
6. 最小验收步骤
   - `systemctl is-active furun-spot-repair.service`
   - `journalctl -u furun-spot-repair.service`
   - `redis-cli XLEN stream:repair_tasks:<node_id>`

### 7.6 最小验收口径

本次最小验收只看以下事实：

1. `furun-spot-repair.service` 能被安装
2. `furun-spot-repair.service` 能进入 `active`
3. repair role 能基于现有 `.env.worker` 启动
4. 运维文档提供清晰的安装、启停与验收步骤

本次不要求：

- 一定要拿到真实线上 repair task 消费成功记录
- 一定要完成新的远端 helper 验证

## 8. 错误处理

本次不新增新的业务异常分支。

部署层只需保持与现有 service 一致的 systemd 行为：

- 进程退出时自动重启
- 配置错误时可通过 `journalctl` 直接排查

运维文档中应明确：

- 若 `furun-spot-repair.service` 启动失败，优先检查：
  - `.env.worker` 中 `WORKER_ROLE`
  - `REDIS_URL`
  - 交易所凭证
  - `REPAIR_STREAM_KEY`

## 9. 测试策略

本次以静态部署资产与文档验证为主，至少覆盖以下内容：

1. 检查新增 service 文件内容与 executor service 风格一致
2. 检查 `.env.worker.example` 已包含 repair role 使用口径
3. 检查运维文档已包含 repair 部署与验收步骤

如需本地辅助验证，可补：

- service 文件内容读取断言
- 文档关键段落断言

但本次不强制新增复杂自动化测试。

## 10. 验收标准

满足以下条件即可视为完成：

1. 新增 `deploy/systemd/furun-spot-repair.service`
2. `.env.worker.example` 明确 repair role 用法
3. `docs/ops/live-workers-systemd.md` 完成 repair 部署、启停、验收说明
4. 不影响现有 scanner / dispatcher / executor 部署说明

## 11. 后续演进

本次完成后，后续可以继续沿以下方向推进，但不属于本次范围：

- 启动真实 `furun-spot-repair.service` 做主服务器或执行节点联调
- 补 repair systemd 的主服务器远端验证记录
- 继续扩展更接近生产的 executor + repair 线上协同运行形态
