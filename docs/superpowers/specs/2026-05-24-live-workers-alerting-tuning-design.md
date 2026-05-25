# 常驻 Worker 中文通知与降噪调整设计

## 1. 文档目标

本文档定义对当前已上线的 live workers 告警链路做一次小范围调优，目标仅包括：

- 飞书通知改为中文文案
- QQ 邮件通知改为中文文案
- 明显减少通知数量

本文档不修改内部事件模型，不修改 `journalctl` 中的结构化日志字段，也不改变当前 `systemd` 运行方式。

## 2. 调整范围

本次只调整以下内容：

- `AlertRouter` 的通知触发条件
- `FeishuNotifier` 的文本渲染
- `EmailNotifier` 的标题与正文渲染
- `.env.worker.example` 中的默认阈值
- 运维文档中的通知策略说明

本次不调整以下内容：

- `RuntimeEvent.event_type`
- JSON 结构化日志
- `journalctl` 中的英文事件字段
- `scanner/consumer/worker_service` 的事件产出逻辑

## 3. 设计目标

本次调优满足以下目标：

1. 内部事件与日志保持稳定，避免影响既有运行链路
2. 飞书和 QQ 对外通知改为中文，便于直接阅读
3. 成功类通知显著减少，避免刷屏
4. 一般异常通知频率降低，但严重异常仍实时触达

## 4. 推荐方案

推荐采用“只改通知层，不改事件层”的方案。

### 4.1 方案说明

- `RuntimeEvent` 和 `event_type` 保持英文
- `StructuredEventLogger` 保持当前 JSON 输出
- 飞书和邮件在发送前进行中文渲染
- `AlertRouter` 收紧成功类和一般异常类通知规则

### 4.2 选择原因

- 风险最小
- 不需要重写现有测试和远端运行逻辑
- 不会破坏已经跑通的 `journalctl` 与 JSON 日志分析
- 便于快速上线验证效果

## 5. 降噪策略

### 5.1 成功类通知

当前成功类通知过多，首版调优后仅保留真正有价值的机会命中通知。

#### 调整规则

- `scanner.iteration.succeeded`
  - 不发飞书
  - 不发邮件
- `consumer.message.processed`
  - 不发飞书
  - 不发邮件
- `opportunity.detected`
  - 仅当 `spread_bps >= ALERT_SUCCESS_SPREAD_BPS_THRESHOLD` 时发飞书
  - 默认不发邮件

### 5.2 一般异常通知

`ERROR` 类事件保留飞书，但要加长去重窗口。

#### 调整规则

- `ERROR` 事件仍发飞书
- 去重窗口从当前联调用的短窗口提升为更长窗口
- 推荐默认值：
  - `ALERT_DEDUPE_WINDOW_SECONDS=300`

### 5.3 严重异常通知

`CRITICAL` 保持现有强触达策略。

#### 调整规则

- `CRITICAL` 不去重
- 始终发飞书
- 始终发 QQ 邮件

## 6. 中文渲染设计

### 6.1 总体原则

- 中文通知只发生在发送层
- 内部事件模型和日志字段仍保持英文
- 首版使用轻量映射，不做复杂模板系统

### 6.2 中文标题映射

建议为常见事件建立如下映射：

- `worker.start_failed` -> `服务启动失败`
- `worker.started` -> `服务已启动`
- `worker.stopped` -> `服务已停止`
- `scanner.iteration.failed` -> `扫描任务异常`
- `consumer.message.failed` -> `机会消费异常`
- `opportunity.detected` -> `检测到套利机会`

若事件未命中映射，回退到原始 `event_type`。

## 7. 飞书通知格式

飞书通知保持简洁，建议为 3 到 6 行中文短句。

### 7.1 成功通知示例

```text
检测到套利机会
服务：scanner
交易对：BTC/USDT
买入交易所：bitget
卖出交易所：gate
价差：88.0 bps
```

### 7.2 一般异常通知示例

```text
扫描任务异常
服务：scanner
交易对：BTC/USDT
交易所：okx
原因：timeout
```

### 7.3 严重异常通知示例

```text
服务启动失败
服务：scanner
区域：default
原因：missing credentials for exchanges: okx
```

## 8. QQ 邮件格式

邮件只发严重异常，因此标题应短，正文应完整。

### 8.1 标题示例

```text
[严重告警] 服务启动失败
```

### 8.2 正文建议

正文至少包含：

- 中文事件标题
- 服务名
- 区域
- 时间
- 关键错误原因
- 原始事件类型

这样既方便直接阅读，也保留一定排障上下文。

## 9. 配置调整

### 9.1 默认阈值

为降低成功类通知数量，建议把默认阈值从联调用的 `0` 调整为更合理的值。

推荐默认值：

- `ALERT_SUCCESS_SPREAD_BPS_THRESHOLD=20`
- `ALERT_DEDUPE_WINDOW_SECONDS=300`

### 9.2 配置原则

- 成功类阈值由环境变量控制，避免写死在代码中
- 不同环境可以使用不同阈值
- 若后续仍觉得消息多，再继续上调阈值

## 10. 测试策略

### 10.1 单元测试

至少覆盖以下内容：

- 飞书中文文本渲染
- 邮件中文标题和正文渲染
- `consumer.message.processed` 不再发通知
- `opportunity.detected` 低于阈值时不发，高于阈值时发
- `ERROR` 去重窗口变更后仍生效

### 10.2 远端验证

远端至少验证以下场景：

1. 一条中文 success 飞书通知
2. 一条中文 error 飞书通知
3. 一条中文 critical 邮件通知

并同时确认：

- `journalctl` 仍保留英文 JSON 事件字段
- 结构化日志未受影响

## 11. 实施边界

本次只做“通知表现层”和“通知频率”调优，不处理其他业务 bug。

例如：

- 不修复 `SpotOpportunity` 相关业务错误
- 不增加新事件类型
- 不改 worker 主循环的主要流程

## 12. 结论

本次推荐采用“小范围通知层调优”方案：

- 内部英文事件与 JSON 日志保持不变
- 飞书和 QQ 邮件改为中文
- 只保留高价值 success 通知
- 拉长一般异常去重窗口

该方案风险低、上线快，最适合先解决“消息太多、文案不直观”的当前问题。
