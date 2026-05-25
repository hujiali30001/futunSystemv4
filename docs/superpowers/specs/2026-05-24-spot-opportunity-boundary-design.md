# SpotOpportunity 链路边界统一设计

## 1. 文档目标

本文档定义如何修复当前 live scanner 中的 `SpotOpportunity` 兼容问题，并顺手统一“现货机会发现 -> Redis 发布 -> scanner 事件”这条链路的数据边界。

本次目标包括：

- 修复 `SpotOpportunity` 被误当作 `dict` 使用导致的运行期错误
- 统一该链路内部的数据结构
- 明确在哪些边界需要从强类型对象转换为可序列化 payload

## 2. 背景与问题

当前远端 scanner 持续报错：

- `'SpotOpportunity' object has no attribute 'get'`

根因是：

- [live_spot_flow.py](file:///d:/old/FuRunSystemV4/app/runtime/live_spot_flow.py) 的 `run_once()` 返回 [SpotOpportunity](file:///d:/old/FuRunSystemV4/app/market/opportunity.py)
- 但 [live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py) 在构造 `opportunity.detected` 事件时，把该返回值当作 `dict`，直接使用 `result.get(...)`

这说明当前同一条链路中存在 `dict` 和 `dataclass` 边界不清的问题。

## 3. 设计目标

本次修复满足以下目标：

1. `LiveSpotFlowService.run_once()` 的返回结构明确且稳定
2. scanner 不再依赖“可能是 dict，也可能是对象”的模糊输入
3. Redis 发布和 dispatcher 输入使用显式序列化，而不是隐式混用结构
4. 修复只聚焦当前 SpotOpportunity 链路，不扩大到整个系统

## 4. 推荐方案

推荐采用“链路内部统一使用 `dataclass`，边界层显式序列化”的方案。

### 4.1 方案说明

- `SpotOpportunity` 作为“现货机会发现层”的唯一返回结构
- `LiveSpotFlowService.run_once()` 明确返回 `SpotOpportunity`
- `ContinuousSpotScanner` 只用属性访问，例如：
  - `result.buy_exchange`
  - `result.sell_exchange`
  - `result.spread_bps`
- 需要写 Redis、发 dispatcher payload、发运行时事件时，再显式构造字典

### 4.2 选择原因

- 能从根上避免 `.get()` / 属性访问混用
- 类型边界清晰，后续更容易测试和维护
- 改动范围可控，不需要重构整个 runtime

## 5. 非目标

本次不做以下内容：

- 不统一整个项目所有 `dict` 和 `dataclass` 用法
- 不重构下单执行链路
- 不改 `consumer` 的 Redis stream 消息结构
- 不改告警系统事件模型

## 6. 数据边界设计

### 6.1 内部返回类型

`LiveSpotFlowService.run_once()` 的正式返回类型定义为：

- `SpotOpportunity`

`SpotOpportunity` 继续保留在 [opportunity.py](file:///d:/old/FuRunSystemV4/app/market/opportunity.py) 中，不引入新的机会模型。

### 6.2 边界层序列化

在以下边界将 `SpotOpportunity` 显式转换为字典：

- Redis Stream / ZSET 写入所需字段
- dispatcher 调用输入
- runtime event payload

推荐新增一个轻量转换入口，二选一即可：

1. 给 `SpotOpportunity` 增加 `to_payload()`
2. 新增独立函数 `spot_opportunity_to_payload(opportunity)`

推荐第 2 种，原因是：

- 不污染纯数据对象
- 更容易限定“不同边界需要哪些字段”

## 7. 组件改动设计

### 7.1 `app/market/opportunity.py`

保持 `SpotOpportunity` 作为 dataclass，不改其核心字段。

如果采用独立函数方式，可在同文件新增辅助函数，例如：

- `spot_opportunity_to_payload(opportunity: SpotOpportunity) -> dict[str, object]`

该函数至少返回：

- `symbol`
- `buy_exchange`
- `sell_exchange`
- `buy_ask`
- `sell_bid`
- `spread_bps`
- `redis_member`
- `timestamp`

### 7.2 `app/runtime/live_spot_flow.py`

该文件做两件事：

1. 明确 `run_once()` 返回 `SpotOpportunity`
2. dispatcher 输入改成显式序列化，而不是手写散落字段

这可以避免后续字段扩展时不同调用点再次漂移。

### 7.3 `app/runtime/live_workers.py`

scanner 成功事件构造改为属性访问：

- `result.buy_exchange`
- `result.sell_exchange`
- `result.spread_bps`

不再调用：

- `result.get(...)`

这是当前远端报错的直接修复点。

### 7.4 `app/runtime/redis_flow.py`

如果当前 `MarketOpportunityPublisher` 已经直接使用 `SpotOpportunity` 属性，则保持不动。

若存在手写零散字典转换，则可以收敛到统一辅助函数，但仅限本次涉及到的 SpotOpportunity 链路，不顺手扩展其它模型。

## 8. 测试策略

### 8.1 单元测试

至少补充或更新以下测试：

1. `LiveSpotFlowService.run_once()` 返回 `SpotOpportunity`
2. `ContinuousSpotScanner` 能从 `SpotOpportunity` 构造 `opportunity.detected` 事件
3. Redis 发布与 dispatcher 输入在当前链路下仍保持可用

### 8.2 回归重点

本次回归重点不是增加很多新测试，而是锁住以下事实：

- `run_once()` 返回值类型不漂移
- scanner 不再因 `.get()` 报错
- live worker 的成功路径恢复

### 8.3 远端验证

远端至少验证：

1. `scanner.iteration.failed` 中不再出现 `SpotOpportunity object has no attribute 'get'`
2. scanner 保持运行
3. `opportunity.detected` / Redis 指标恢复正常增长

## 9. 实施边界

本次修复只覆盖：

- `app/market/opportunity.py`
- `app/runtime/live_spot_flow.py`
- `app/runtime/live_workers.py`
- 对应测试文件

本次不覆盖：

- consumer payload 结构重做
- 交易执行器返回值统一
- 全局 dataclass/dict 规范化改造

## 10. 风险与控制

### 10.1 风险

如果边界序列化改动过大，可能影响：

- Redis Stream 字段内容
- 现有 dispatcher 输入格式
- 已有测试快照

### 10.2 控制策略

- 优先保持现有 Redis / dispatcher 字段不变
- 只修正“对象和字典混用”的调用方式
- 通过现有 `tests/test_live_spot_flow.py` 和 `tests/test_live_workers.py` 做回归

## 11. 结论

本次推荐采用“内部统一 `SpotOpportunity` dataclass，边界层显式序列化”的方案。

该方案既能直接修复当前远端 scanner 的 `.get()` 报错，也能把当前现货机会发现链路的数据边界整理清楚，避免后续继续出现同类兼容问题。
