# 常驻 Worker 结构化日志与告警设计

## 1. 文档目标

本文档定义如何为当前已经完成 `systemd` 常驻化的现货扫描器与 Redis 消费者补充以下运行能力：

- 结构化日志
- 飞书异常告警
- QQ 邮箱严重告警
- 机会命中与消费成功通知
- 高频事件限流与降噪

本文档聚焦 `scanner/consumer/live worker` 运行链路，不扩展到更深的交易执行链路，也不引入数据库级告警历史存储。

## 2. 背景与现状

当前系统已经具备以下基础能力：

- [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py) 已实现 `ContinuousSpotScanner` 与 `RedisSpotConsumer`
- [worker_service.py](file:///d:/old/FuRunSystemV4/app/runtime/worker_service.py) 已支持 `scanner`/`consumer` 双角色常驻运行
- 远端 `systemd` 服务已经跑通，Redis 指标持续增长
- [notifier.py](file:///d:/old/FuRunSystemV4/app/admin/notifier.py) 仅提供非常薄的通知抽象
- [logger.py](file:///d:/old/FuRunSystemV4/app/audit/logger.py) 仅提供内存态审计记录，不适合常驻运行时可观测

当前缺口主要体现在：

- 成功与异常事件没有统一模型
- `journalctl` 里缺少结构化可筛选信息
- 运行时没有飞书或邮件告警闭环
- 高频成功与失败事件没有限流降噪

## 3. 设计目标

本次增强满足以下目标：

1. `scanner` 与 `consumer` 统一产出结构化运行事件
2. 所有关键事件都能写入标准输出并被 `journald` 接收
3. 异常与严重故障可推送到飞书和 QQ 邮箱
4. 机会命中与消费成功通知可控地推送，不刷屏
5. 保持与现有 `systemd worker` 架构一致，不把告警逻辑散落在业务循环中

## 4. 非目标

本次不做以下内容：

- 不扩展到真实下单、查单、撤单、补单的完整告警体系
- 不引入数据库告警历史表
- 不实现 Web 管理台告警面板
- 不引入 Prometheus、Sentry、ELK 等外部观测平台
- 不实现复杂富文本模板系统

## 5. 推荐方案

推荐采用“统一事件模型 + JSON 结构化日志 + `AlertRouter` + 飞书/邮件双渠道”的方案。

### 5.1 方案说明

- 业务循环只负责产出标准化事件
- `StructuredEventLogger` 负责把事件写成单行 JSON 到 stdout
- `AlertRouter` 负责：
  - 根据事件类型和级别决定是否告警
  - 选择飞书、邮件或仅日志
  - 执行限流、抽样、去重
- `FeishuNotifier` 负责 webhook 推送
- `EmailNotifier` 负责通过 QQ 邮箱 SMTP 发送严重告警

### 5.2 选择原因

- 与现有 `systemd + journalctl` 运行方式完全兼容
- 不需要引入额外基础设施即可落地
- 成功通知和异常告警可以共用一套事件与路由模型
- 后续如果要扩展到交易执行链路，可直接复用事件和路由器

### 5.3 未选方案

#### 直接在 worker 中发送飞书和邮件

不推荐，原因如下：

- 通知逻辑会散落到多个循环
- 难以统一做限流和抽样
- 业务层会混入过多运行控制细节

#### 只做日志，不做内建告警

不推荐，原因如下：

- 用户已经明确需要飞书/QQ 告警
- 机会命中通知不适合只依赖外部日志系统
- 当前阶段引入额外观测平台成本高于收益

## 6. 事件模型设计

### 6.1 统一事件对象

新增统一事件模型，例如 `RuntimeEvent`，建议字段如下：

- `event_type`
- `level`
- `service`
- `region`
- `symbol`
- `exchange`
- `exchanges`
- `message`
- `payload`
- `created_at`

其中：

- `event_type` 描述具体行为，例如：
  - `worker.started`
  - `worker.stopped`
  - `worker.start_failed`
  - `scanner.iteration.succeeded`
  - `scanner.iteration.failed`
  - `consumer.message.processed`
  - `consumer.message.failed`
  - `opportunity.detected`
- `level` 取值建议为：
  - `INFO`
  - `WARNING`
  - `ERROR`
  - `CRITICAL`
- `payload` 保存结构化上下文，例如：
  - `spread_bps`
  - `buy_exchange`
  - `sell_exchange`
  - `message_id`
  - `error`
  - `processed_count`

### 6.2 事件设计原则

- 同一事件对象既用于日志，也用于飞书和邮件渲染
- 业务代码不直接拼接最终通知文本，只填充语义字段
- 首版保持字段数量有限，避免过度设计

## 7. 日志设计

### 7.1 日志输出方式

首版继续输出到标准输出，由 `systemd/journald` 接管，不额外引入文件日志。

新增 `StructuredEventLogger`，负责把事件写成单行 JSON，例如：

```json
{"event_type":"consumer.message.processed","level":"INFO","service":"consumer","symbol":"BTC/USDT","message":"spot opportunity dispatched","payload":{"buy_exchange":"bitget","sell_exchange":"gate","message_id":"12-0"}}
```

错误事件示例：

```json
{"event_type":"scanner.iteration.failed","level":"ERROR","service":"scanner","message":"scanner iteration failed","payload":{"exchange":"okx","error":"timeout"}}
```

### 7.2 日志写入边界

日志组件只负责序列化与输出，不负责渠道判断，也不负责网络发送。

这意味着：

- `StructuredEventLogger` 是纯日志组件
- `AlertRouter` 决定何时调用通知渠道
- worker 主循环只负责构造事件并交给统一入口

## 8. 告警路由设计

### 8.1 `AlertRouter`

新增统一路由器，例如 `AlertRouter.dispatch(event)`，负责以下职责：

1. 总是调用 `StructuredEventLogger`
2. 根据 `event_type` 和 `level` 判断是否需要飞书或邮件
3. 对高频事件执行限流、去重、抽样
4. 保证 `CRITICAL` 事件始终优先发送

### 8.2 渠道组件

新增以下轻量组件：

- `StructuredEventLogger`
- `FeishuNotifier`
- `EmailNotifier`

各自职责如下：

#### `StructuredEventLogger`

- 把 `RuntimeEvent` 序列化为 JSON
- 输出到 stdout

#### `FeishuNotifier`

- 从事件渲染轻量文本消息
- 调用飞书 webhook 发送
- 返回发送结果或抛出异常

#### `EmailNotifier`

- 使用 QQ 邮箱 SMTP
- 仅发送严重异常和关键启动故障
- 标题与正文都基于事件对象渲染

## 9. 告警分级与渠道策略

### 9.1 分级

建议将事件分为三档：

#### `INFO`

用于：

- worker 启动成功
- worker 正常停止
- 机会命中
- 消费成功

#### `ERROR`

用于：

- 单轮扫描失败
- 单条消息处理失败
- 某交易所 ticker 拉取失败
- Redis 短时读取异常

#### `CRITICAL`

用于：

- worker 启动失败
- Redis 初始化失败
- 必需环境变量缺失
- 凭证装配失败
- 连续失败超阈值

### 9.2 渠道策略

建议策略如下：

- `INFO`
  - 总是写结构化日志
  - 机会命中和消费成功仅在满足阈值时发飞书
  - 默认不发邮件
- `ERROR`
  - 写结构化日志
  - 发飞书
  - 默认不发邮件
- `CRITICAL`
  - 写结构化日志
  - 发飞书
  - 发 QQ 邮件

## 10. 限流与降噪设计

### 10.1 目标

由于成功类事件和短时异常都可能高频出现，必须做降噪，否则飞书会被刷屏。

### 10.2 首版规则

建议首版规则如下：

- `scanner.iteration.succeeded`
  - 只写日志
  - 不发通知
- `opportunity.detected`
  - 只有当 `spread_bps >= ALERT_SUCCESS_SPREAD_BPS_THRESHOLD` 时才发飞书
- `consumer.message.processed`
  - 只在机会满足阈值时发飞书
- `ERROR` 类事件
  - 基于 `event_type + symbol + exchange` 做时间窗口去重
  - 在 `ALERT_DEDUPE_WINDOW_SECONDS` 内只发一次飞书
- `CRITICAL`
  - 不限流
  - 始终发飞书 + 邮件

### 10.3 可选聚合

首版可加入简单聚合能力：

- 如果同类失败在短时间内连续发生多次，飞书中发送摘要，例如：
  - `okx ticker 拉取连续失败 8 次`

如果首版实现成本偏高，可以先只做时间窗口去重，把聚合作为下一阶段增强。

## 11. 首版事件接入点

首版只在关键运行节点接入事件，不扩展到整个交易执行栈。

### 11.1 `WorkerApp`

接入事件：

- `worker.started`
- `worker.start_failed`
- `worker.stopped`

### 11.2 `ContinuousSpotScanner`

接入事件：

- `scanner.iteration.succeeded`
- `scanner.iteration.failed`
- `opportunity.detected`

成功事件中记录：

- `symbol`
- `buy_exchange`
- `sell_exchange`
- `spread_bps`

### 11.3 `RedisSpotConsumer`

接入事件：

- `consumer.message.processed`
- `consumer.message.failed`

成功事件中记录：

- `message_id`
- `symbol`
- `buy_exchange`
- `sell_exchange`

## 12. 配置设计

### 12.1 环境变量

建议新增以下配置项，并继续放在 `.env.worker`：

- `ALERTS_ENABLED=1`
- `ALERT_FEISHU_ENABLED=1`
- `ALERT_FEISHU_WEBHOOK=`
- `ALERT_EMAIL_ENABLED=1`
- `ALERT_EMAIL_SMTP_HOST=smtp.qq.com`
- `ALERT_EMAIL_SMTP_PORT=465`
- `ALERT_EMAIL_USERNAME=`
- `ALERT_EMAIL_PASSWORD=`
- `ALERT_EMAIL_TO=`
- `ALERT_SUCCESS_SPREAD_BPS_THRESHOLD=`
- `ALERT_DEDUPE_WINDOW_SECONDS=60`

### 12.2 配置原则

- 飞书和邮件都支持单独开关
- 没配某个渠道时不影响主链路运行
- 告警发送失败只记录错误，不应阻塞 scanner/consumer 主循环

## 13. 错误处理设计

### 13.1 通知发送失败

如果飞书或邮件发送失败：

- 记录一个新的 `ERROR` 级事件，例如 `alert.dispatch.failed`
- 不再递归触发新的外部告警，避免自激增
- 主业务循环继续运行

### 13.2 渠道缺失

如果渠道未配置：

- `AlertRouter` 将其视为“该渠道禁用”
- 仍保留结构化日志输出
- 不将其视为主业务异常

## 14. 测试策略

### 14.1 单元测试

至少覆盖以下内容：

- 事件对象字段序列化
- `AlertRouter` 渠道选择
- 去重窗口逻辑
- `INFO/ERROR/CRITICAL` 分发规则
- 飞书文本渲染
- 邮件标题与正文渲染

### 14.2 组件测试

覆盖以下方向：

- worker 触发事件时是否正确调用 logger
- 机会命中是否按阈值触发飞书
- 严重异常是否同时触发飞书和邮件

### 14.3 远端验证

远端至少验证两类事件：

1. 一条成功类通知
2. 一条异常类通知

验收标准为：

- `journalctl` 可看到 JSON 结构化日志
- 飞书收到事件通知
- 严重异常可收到 QQ 邮件

## 15. 实施边界

本次只覆盖：

- `scanner`
- `consumer`
- `worker_service`

本次不覆盖：

- 下单执行器
- 风控补单
- 数据库存储
- 后台管理页面

## 16. 后续演进

本次落地后，下一阶段可继续扩展：

1. 把交易执行链路事件接入同一 `AlertRouter`
2. 引入 Redis consumer group 与 offset 告警
3. 增加告警聚合摘要
4. 告警事件落库，支持历史查询
5. 接入更完整的监控平台

## 17. 结论

本次推荐方案是在当前已上线的 `systemd worker` 基础上，增加统一事件模型、JSON 结构化日志和飞书/QQ 双渠道告警。

该方案能够在不打散现有运行链路的前提下，补齐“服务出了问题能及时知道、机会命中时能收到通知”的运行闭环，并为后续扩展到更深的交易执行链路预留一致的事件与告警边界。
