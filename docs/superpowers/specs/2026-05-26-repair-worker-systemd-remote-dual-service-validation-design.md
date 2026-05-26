# Repair Worker Systemd 双服务联调远端验收设计

## 1. 文档目标

本文档定义如何在主服务器上对 `repair worker` 的 systemd 部署资产做一次最小真实联调验收。

本次目标不是继续补代码，也不是扩大到完整生产演练，而是在已经具备以下前提的基础上，验证真实 systemd 运行形态下的双服务协同是否成立：

- `furun-spot-executor.service` 已具备默认 `executor -> repair` 自动接线
- `furun-spot-repair.service` 已具备最小 systemd 部署资产
- `repair worker` 业务逻辑与 helper canary 已分别在本地和主服务器虚拟环境下通过

本次要确认的是：

- `executor` 真实运行时能产出 repair task
- `repair` 真实运行时能消费 repair task
- 两个 systemd 服务在一次最小 canary 下能形成可观察、可解释、可回溯的闭环证据

## 2. 范围

本次只做以下能力：

- 在主服务器上重启并检查 `furun-spot-executor.service`
- 在主服务器上重启并检查 `furun-spot-repair.service`
- 注入一条最小 canary 输入，触发 `executor -> repair` 联动
- 观察 `journalctl`、Redis stream、任务结果摘要
- 回填运维文档中的远端联调记录

本次不做以下能力：

- 不修改 runtime 业务逻辑
- 不修改 Redis stream 协议
- 不触发真实交易所下单
- 不做多用户、多任务并发压测
- 不扩大到 scanner、dispatcher、control admin 的完整联调
- 不新增新的 helper 脚本设计作为本次主路径

## 3. 背景与现状

当前系统已经具备以下基础能力：

- `executor.repair_planned` 事件已实现，并完成过主服务器 helper 验证
- 最小 `repair worker` 自动补单链已实现，并完成过主服务器 helper 双消费者验证
- 默认 `WorkerApp(role=executor)` 已会自动注入 `RepairTaskPublisher`
- `repair worker` 的 systemd 部署资产已补齐并已推送 GitHub

当前仍缺的不是代码能力，而是以下运行态事实：

1. systemd 形态下的 `executor` 是否真的会把 repair message 写入 `stream:repair_tasks:{node_id}`
2. systemd 形态下的 `repair` 是否真的会消费该 stream
3. 两个服务真实联动时，是否能在日志、Redis 与任务摘要中留下稳定证据

因此，本次需要补的是“主服务器真实双服务联调验收”，而不是继续扩展功能。

## 4. 问题定义

如果只停留在 helper canary 与部署资产层，仍有以下缺口：

1. 还不能证明 `furun-spot-executor.service` 与 `furun-spot-repair.service` 在真实 systemd 形态下能够协同
2. 还不能证明部署文档里的安装、启停与验收步骤在主服务器上真实可走通
3. 后续若继续推进更接近生产的闭环，仍要先补这轮最小真实联调

因此，本次优先补齐真实 systemd 运行态的最小联调证据。

## 5. 设计目标

本次设计满足以下目标：

1. 在不扩大风险的前提下，验证 `executor -> repair` 双服务联动
2. 验收证据尽量复用现有 systemd、Redis、数据库与结构化日志
3. 让这轮验证可以被运维文档复用，成为后续远端排障基线
4. 把范围控制在一次最小 canary，不演变成完整生产联调

## 6. 方案比较

### 6.1 方案 A：只做 repair service 启动验收

做法：

- 只重启 `furun-spot-repair.service`
- 检查 `active`、日志和 `stream:repair_tasks:{node_id}` 可观察性

优点：

- 最快
- 最稳
- 对环境依赖最少

缺点：

- 不能证明 `executor` 真实会发布 repair task
- 不能证明双服务联动成立

### 6.2 方案 B：repair service 启动验收 + 手工注入 repair task

做法：

- 重启 `repair` 服务
- 手工向 `stream:repair_tasks:{node_id}` 注入一条 repair message
- 验证 `repair` 消费

优点：

- 能证明 repair 服务真实可消费
- 排障面比双服务联调小

缺点：

- 仍不能覆盖 `executor -> repair` 自动接线

### 6.3 方案 C：最小 executor + repair 双服务联调

做法：

- 同时重启 `furun-spot-executor.service` 与 `furun-spot-repair.service`
- 注入一条最小 canary 输入，让 `executor` 进入 `OPEN_PARTIAL`
- 验证 repair task 被发布、被消费，并最终形成结果收口

优点：

- 最接近生产闭环
- 能直接覆盖当前最高价值的真实缺口
- 产出的文档和排障结论更有长期价值

缺点：

- 对主服务器环境状态依赖最高
- 排障面比方案 A/B 更大

### 6.4 推荐方案

本次采用方案 C，但强制按“最小双服务联调”收敛。

这里的“最小”指：

- 只验证一条 canary
- 只验证一次 `executor -> repair` 联动
- 不扩展到真实下单
- 不扩展到长时间 soak、并发压测或多节点编排

## 7. 核心设计

### 7.1 服务启动与基线确认

联调前先确认以下事实：

- 主服务器工作目录、虚拟环境和 `.env.worker` 已同步最新代码
- `furun-spot-executor.service` 与 `furun-spot-repair.service` 都可被 `systemctl restart`
- 两个服务重启后保持 `active`

联调基线至少记录：

- `systemctl is-active` 结果
- 两个服务最近日志中的结构化事件
- `stream:spot_exec_tasks:{node_id}` 当前长度
- `stream:repair_tasks:{node_id}` 当前长度

这样后续可以区分“联调新增流量”与“历史残留流量”。

### 7.2 最小 canary 输入策略

本次 canary 输入要满足两个条件：

1. 能让 `executor` 进入 `OPEN_PARTIAL`
2. 不触发真实交易所下单

因此，本次优先采用“远端最小假执行输入”而不是生产真实订单。

实现口径为：

- 注入一条语义上与现有 helper 验证一致的最小任务输入
- 让 `executor` 使用可控、可预测的假执行返回
- 目标结果固定为：
  - `execution_status = OPEN_PARTIAL`
  - `repair_action = AUTO_HEDGE_REPAIRING`
  - `repair_reason = one_leg_failed`

本次设计允许使用最小化的远端临时辅助脚本或受控注入方式来制造 canary，但这些辅助动作的目标只能是“驱动现有 systemd 服务进入联调路径”，不能变成本轮主实现内容。

### 7.3 观测点设计

本次联调必须同时具备以下三类观测点：

1. systemd 与日志观测
   - `furun-spot-executor.service` 保持 `active`
   - `furun-spot-repair.service` 保持 `active`
   - `journalctl` 中可见 `executor` 与 `repair` 的结构化事件
2. Redis stream 观测
   - `stream:spot_exec_tasks:{node_id}` 中能定位到本次 canary
   - `stream:repair_tasks:{node_id}` 中能观察到 `executor` 产出的 repair message
   - repair 消费后，stream 长度或最新 entry 变化应与联调动作一致
3. 任务结果观测
   - 若数据库真值开启，`arbitrage_tasks` 中能看到该 canary 的最终状态
   - 若事件路由可观测，可同时记录 `repair.task.finished`

本次验收以“多证据互相印证”为准，不依赖单一日志行判断成功与否。

### 7.4 成功路径定义

成功路径指以下顺序成立：

1. `executor` 服务接收到 canary 输入
2. `executor` 产出 `OPEN_PARTIAL`
3. `executor` 发布 repair task 到 `stream:repair_tasks:{node_id}`
4. `repair` 服务消费该 repair task
5. 最终出现以下两类收口之一：
   - repair 成功：任务进入 `SUCCEEDED` 且 `execution_status = OPEN_HEDGED`
   - repair 失败：任务进入 `FAILED` 且 `execution_status = OPEN_PARTIAL`，`status_reason = manual_required`

成功不要求“一定修复成功”，只要求“双服务联动后的结果可解释并正确收口”。

### 7.5 失败边界与排障顺序

为避免联调一旦受阻就扩大范围，本次明确失败边界：

1. 如果两个服务无法保持 `active`
   - 优先排查 `.env.worker`
   - 优先排查 `journalctl`
   - 不先改业务代码
2. 如果 `executor` 未产出 repair task
   - 优先检查 canary 是否真的命中 `OPEN_PARTIAL + AUTO_HEDGE_REPAIRING`
   - 再检查默认装配是否已同步到远端
3. 如果 repair stream 有消息但 `repair` 不消费
   - 优先检查 `WORKER_ROLE`
   - 优先检查 `NODE_ID`
   - 优先检查 `REPAIR_STREAM_KEY`
4. 如果结果无法写回摘要
   - 优先检查数据库开关与任务表写回状态
   - 不把问题直接归因于 repair 执行逻辑

本次排障只解决“让最小双服务联调成立”所必需的问题，不顺手扩展为新的功能开发。

### 7.6 文档回填

联调完成后，需在 `docs/ops/live-workers-systemd.md` 中新增一节远端实测记录，至少包含：

- 执行日期
- 使用的最小 canary 场景
- `systemctl` 结果
- 关键 Redis 观测
- 关键日志结论
- 最终任务状态或事件收口
- 本次排障备注

这样后续若 systemd 双服务链再出现问题，可以直接对照本次记录回溯。

## 8. 错误处理

本次不新增 runtime 业务异常分支。

远端验收中的错误处理只遵循以下原则：

- 先看 `systemd` 进程状态
- 再看 `journalctl`
- 再看 Redis stream 与数据库状态
- 最后才判断是否存在代码回归

如果远端环境异常明显超出本次范围，例如数据库锁链、Redis 服务故障、凭证体系失效，应记录为环境阻塞，不在本次设计内顺手扩写新需求。

## 9. 测试策略

本次以主服务器最小真实联调为主，不新增复杂本地自动化测试。

至少覆盖以下检查：

1. `systemctl is-active furun-spot-executor.service`
2. `systemctl is-active furun-spot-repair.service`
3. `journalctl -u furun-spot-executor.service`
4. `journalctl -u furun-spot-repair.service`
5. `redis-cli XLEN stream:spot_exec_tasks:{node_id}`
6. `redis-cli XLEN stream:repair_tasks:{node_id}`
7. `redis-cli XREVRANGE stream:repair_tasks:{node_id} + - COUNT 5`
8. 如数据库可用，查询 `arbitrage_tasks` 中该 canary 的结果

重点不是命令数量，而是确认“双服务联动证据链”完整。

## 10. 验收标准

满足以下条件即可视为完成：

1. 主服务器上的 `furun-spot-executor.service` 与 `furun-spot-repair.service` 都保持 `active`
2. 本次 canary 触发了真实 `executor -> repair` 联动
3. `stream:repair_tasks:{node_id}` 中存在与 canary 对应的 repair message
4. `repair` 服务对该 canary 完成真实消费
5. 最终结果进入以下之一：
   - `SUCCEEDED / OPEN_HEDGED`
   - `FAILED / OPEN_PARTIAL / manual_required`
6. 运维文档完成远端实测记录回填

## 11. 后续演进

本次完成后，后续可以继续推进，但不属于本次范围：

- 做更接近完整生产形态的 `scanner + dispatcher + executor + repair` 多服务联调
- 做多任务或长时间运行稳定性验证
- 把 repair systemd 远端联调沉淀为更标准化的一键脚本
