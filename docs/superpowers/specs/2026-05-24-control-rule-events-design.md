# 控制链命中运行事件设计

## 1. 文档目标

本文档定义当前 `control-admin -> Redis 真值 -> dispatcher / executor 双层校验` 链路上的最小可观测性补齐方案。

本次目标不是扩展新的管理接口，也不是引入新的命中记录存储，而是把控制链中“被拦截”和“被缩量”的关键决策正式打成结构化运行事件，方便：

- 本地联调时快速判断命中发生在哪一层
- 远端部署后通过 `journalctl` 黑盒排障
- 后续管理员后台或命中历史查询能力复用统一事件语义

## 2. 范围

本次只做以下能力：

- 为 `dispatcher` 的控制命中增加结构化运行事件
- 为 `executor` 的控制命中增加结构化运行事件
- 明确拦截事件与缩量事件的字段语义
- 为事件补最小测试覆盖

本次不做以下能力：

- 不新增 HTTP 查询接口
- 不新增 Redis 命中历史存储
- 不新增数据库审计表
- 不新增飞书或 QQ 邮件通知规则
- 不新增 Web 页面

## 3. 背景与现状

当前项目已经具备以下事实：

- [control_admin_service.py](file:///d:/old/FuRunSystemV4/app/runtime/control_admin_service.py) 会对管理员操作输出 `control.admin.*` 事件
- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py) 已完成 `dispatcher` 与 `executor` 的控制规则接入
- [runtime_events.py](file:///d:/old/FuRunSystemV4/app/runtime/runtime_events.py) 已提供统一 `RuntimeEvent` 结构
- [alerting.py](file:///d:/old/FuRunSystemV4/app/runtime/alerting.py) 已把所有运行事件写入结构化日志，并只对少量事件发外部通知

当前缺口在于：

- 管理员知道自己“下发了规则”，但运行日志里还不能清楚看到“哪次任务被规则命中”
- 我们虽然已经验证过 `dispatcher` 会缩量、`executor` 会兜底拦截，但这些行为当前没有独立事件语义
- 远端黑盒联调时，只能通过结果侧旁证控制链是否生效，不够直接

## 4. 问题定义

如果继续保持现状，会有以下问题：

1. `dispatcher` 丢任务时，无法快速区分是“路由没命中”还是“控制规则拦了”
2. `executor` 没有实际下单时，无法直接从日志看出是不是控制规则二次拦截
3. 缩量发生后，日志里没有明确记录“原请求额度”和“批准额度”的差异
4. 后续要做命中历史查询时，没有稳定统一的事件字段可复用

因此，这一步需要先把控制命中的运行事件语义补完整。

## 5. 设计目标

本次设计满足以下目标：

1. 任何控制链拦截都能从结构化日志中直接识别
2. 任何控制链缩量都能看到原额度与批准额度
3. 能区分事件发生在 `dispatcher` 还是 `executor`
4. 不改变现有飞书/QQ 通知量
5. 不引入新存储和新服务

## 6. 方案比较

### 6.1 方案 A：只补结构化运行事件

做法：

- 在 `dispatcher` 与 `executor` 命中控制规则时发 `RuntimeEvent`
- 事件只进入结构化日志
- 不引入额外查询接口和命中存储

优点：

- 改动最小
- 最适合当前阶段
- 能立刻提升黑盒可见性

缺点：

- 仍需通过日志检索，不支持直接查询“最近命中历史”

### 6.2 方案 B：结构化事件 + Redis 命中历史

做法：

- 除结构化事件外，再把命中记录写入 Redis 列表或 Stream

优点：

- 后续可直接做命中列表查询

缺点：

- 范围明显变大
- 需要增加清理策略和存储边界

### 6.3 方案 C：结构化事件 + 外部通知

做法：

- 控制命中时额外发飞书或邮件

优点：

- 管理员能实时感知规则命中

缺点：

- 会明显增加消息量
- 与当前“尽量减少外部通知噪音”的方向冲突

## 7. 推荐方案

推荐采用 `方案 A：只补结构化运行事件`。

原因：

- 当前最缺的是“日志中能否直接看懂控制链为什么生效”
- 这一步不需要新服务、新数据面和新通知策略
- 后续如果要扩到命中历史或查询接口，也可以直接复用本次定义的事件字段

## 8. 事件设计

### 8.1 新增事件类型

本次新增两类运行事件：

- `control.rule.blocked`
- `control.rule.resized`

语义：

- `control.rule.blocked`：当前请求被控制规则拒绝，不再继续本层后续动作
- `control.rule.resized`：当前请求没有被完全拒绝，但被缩量到更小额度

### 8.2 事件级别

建议级别如下：

- `control.rule.blocked` 使用 `INFO`
- `control.rule.resized` 使用 `INFO`

原因：

- 这两类事件属于预期内业务决策，不是系统故障
- 不应触发当前 `AlertRouter` 的外部告警策略

### 8.3 服务归属

事件的 `service` 字段直接反映命中发生层：

- 在 `dispatcher` 命中时：`service="dispatcher"`
- 在 `executor` 命中时：`service="executor"`

这样可以直接区分：

- 分发前首次过滤
- 执行前二次兜底

## 9. 事件字段语义

### 9.1 顶层字段

每个事件至少包含：

- `event_type`
- `level`
- `service`
- `region`
- `symbol`
- `exchange`
- `message`
- `payload`
- `created_at`

其中：

- `symbol` 取当前任务或机会的交易对
- `exchange` 先取当前控制判断时使用的主交易所，也就是当前实现里的 `buy_exchange`

### 9.2 payload 字段

`payload` 至少包含：

- `user_id`
- `source_message_id`
- `requested_notional`
- `approved_notional`
- `reason`

如果当前上下文里拿得到，再补：

- `rule_scope_type`
- `rule_scope_id`

说明：

- `requested_notional` 表示命中前的原始请求额度
- `approved_notional` 表示控制面计算后的允许额度
- `reason` 至少记录当前 `ControlDecision.reason`
- 当 `reason` 为空但发生缩量时，允许写成 `limit_rule_applied`

### 9.3 message 文本

建议固定为：

- blocked：`control rule blocked request`
- resized：`control rule resized request`

## 10. 触发时机

### 10.1 dispatcher

在 `RedisNodeTaskDispatcher` 中：

- 计算出 `decision` 后
- 若 `allowed=False`，发 `control.rule.blocked`，然后跳过任务发布
- 若 `approved_notional < requested_notional`，发 `control.rule.resized`，然后改写 `target_quote_amount`

### 10.2 executor

在 `RedisExecutionTaskConsumer` 中：

- 二次计算出 `decision` 后
- 若 `allowed=False`，发 `control.rule.blocked`，然后不再调用后续执行 dispatcher
- 若 `approved_notional < requested_notional`，发 `control.rule.resized`，然后改写 `target_quote_amount`

## 11. 告警与通知策略

本次不改变 [alerting.py](file:///d:/old/FuRunSystemV4/app/runtime/alerting.py) 的外部通知规则。

意味着：

- 新增的 `control.rule.*` 事件仍会进入结构化日志
- 默认不会触发飞书通知
- 默认不会触发 QQ 邮件

这样可以保证：

- 可观测性提升
- 消息量保持稳定

## 12. 测试要求

至少补以下测试：

1. `dispatcher` 在 `allowed=False` 时发出 `control.rule.blocked`
2. `dispatcher` 在缩量时发出 `control.rule.resized`
3. `executor` 在 `allowed=False` 时发出 `control.rule.blocked`
4. `executor` 在缩量时发出 `control.rule.resized`
5. 事件 `payload` 至少包含 `user_id`、`requested_notional`、`approved_notional`、`source_message_id`
6. `AlertRouter` 对这些新事件只记录日志，不发送外部通知

## 13. 非目标约束

本次明确不做：

- 不记录持久化命中历史
- 不实现 `/control/hits` 查询接口
- 不把 `reason` 扩展成复杂规则解释树
- 不要求一次性给出精确命中的规则主键

如果后续需要“最近控制命中查询”，建议基于本次事件语义再单独设计。

## 14. 结论

本次采用“只补结构化运行事件”的最小方案：

- 新增 `control.rule.blocked`
- 新增 `control.rule.resized`
- 覆盖 `dispatcher` 与 `executor` 两层命中
- 只进结构化日志，不扩展外部通知

这样可以在不放大系统复杂度的前提下，把控制链从“功能可用”提升到“运行可见、便于联调和排障”。
