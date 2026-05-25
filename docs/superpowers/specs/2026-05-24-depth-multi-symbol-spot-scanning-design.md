# 深度版多币种现货机会扫描设计

## 1. 文档目标

本文档定义如何把当前单币种、ticker 级别的现货机会扫描升级为：

- 白名单多币种扫描
- 基于 `orderbook` 前几档的深度机会计算
- 更接近真实可成交条件的现货机会输出

本次升级保持现有常驻 worker、Redis、告警和 `systemd` 架构不推翻，只增强机会发现质量和扫描范围。

## 2. 背景与现状

当前系统已具备：

- 常驻 scanner / consumer
- Redis `ZSET + Stream`
- 远端 `systemd` 运行
- 告警与结构化日志

但当前现货机会发现仍有两个明显限制：

1. 只支持单币种扫描
2. 只基于 ticker 的 `bid/ask` 粗略估算机会

这意味着：

- 监控范围太窄
- 机会质量不够真实
- 排行榜和告警可能包含较多“单价看似有利，但深度不足”的噪音机会

## 3. 设计目标

本次升级满足以下目标：

1. 支持通过白名单配置扫描多个主流币种
2. 机会计算从 ticker 升级为 orderbook 深度快照
3. 使用目标成交金额计算更真实的有效买价/卖价
4. Redis 输出保留现有架构，但携带更多深度字段
5. 不推翻当前 worker 和告警体系

## 4. 非目标

本次不做以下内容：

- 不接 `ccxt.pro` websocket 深度订阅
- 不自动发现全市场所有币种
- 不重构交易执行器为按深度拆单
- 不引入复杂滑点模拟或市场冲击模型
- 不扩展到期现或资金费率模型

## 5. 推荐方案

推荐采用“白名单多币种 + REST 深度快照 + 固定目标金额”的方案。

### 5.1 方案说明

- 新增 `SPOT_SYMBOLS` 白名单配置
- 每轮 scanner 按币种依次扫描
- 单个币种内部并发拉取多个交易所的 orderbook
- 用前几档深度和目标成交金额，计算有效买价与卖价
- 基于有效价差生成 `SpotOpportunity`

### 5.2 选择原因

- 与现有轮询式 worker 最兼容
- 风险远低于一次性切到 websocket
- 容易先在远端稳定跑起来
- 便于先覆盖主流币，再逐步扩容

## 6. 扫描范围设计

### 6.1 新增多币种白名单

新增配置：

- `SPOT_SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT`

scanner 不再只围绕一个 `SPOT_SYMBOL`，而是遍历白名单中的多个币种。

### 6.2 兼容策略

为兼容当前单币种配置：

- 若配置了 `SPOT_SYMBOLS`，优先使用多币种列表
- 若未配置 `SPOT_SYMBOLS`，退回到单个 `SPOT_SYMBOL`

这样当前部署不会因为配置升级而直接失效。

## 7. 深度快照模型

### 7.1 快照结构

每个交易所、每个币种在一轮扫描中形成一个 `OrderbookSnapshot`，至少包含：

- `best_bid`
- `best_ask`
- `bids`
- `asks`
- `timestamp`
- `exchange`
- `symbol`

其中：

- `bids` / `asks` 只取前 `N` 档
- 第一版推荐：
  - `ORDERBOOK_DEPTH_LIMIT=5`

### 7.2 数据来源

第一版继续使用 REST 接口获取 orderbook 快照，不切换 websocket。

理由：

- 更容易接入现有 `ExchangeAdapter`
- 与当前轮询 scanner 模型一致
- 降低连接管理和重连复杂度

## 8. 机会计算模型

### 8.1 当前问题

如果只用 `best ask` 和 `best bid`：

- 可能低估买入滑点
- 可能高估卖出成交价
- 无法反映目标金额能否被当前深度承接

### 8.2 推荐计算方式

新增固定目标金额：

- `TARGET_QUOTE_AMOUNT`

例如：

- `100 USDT`
- 或 `500 USDT`

对于每个交易所 orderbook：

- 买入侧：按 `asks` 从低到高吃单，直到满足目标金额，得到 `effective_buy_price`
- 卖出侧：按 `bids` 从高到低吃单，直到满足目标金额，得到 `effective_sell_price`

最终机会计算使用：

- `effective_buy_price`
- `effective_sell_price`

而不是仅使用最优一档价格。

### 8.3 spread 计算

深度版 spread 计算为：

- `spread = (effective_sell_price - effective_buy_price) / effective_buy_price`

然后继续换算为：

- `spread_bps = spread * 10000`

## 9. SpotOpportunity 扩展

### 9.1 保留现有对象

推荐继续使用现有 `SpotOpportunity`，但扩展字段，而不是创建第二套机会对象。

建议新增字段：

- `effective_buy_price`
- `effective_sell_price`
- `target_quote_amount`
- `buy_depth_levels_used`
- `sell_depth_levels_used`

原有字段继续保留：

- `symbol`
- `buy_exchange`
- `sell_exchange`
- `spread_bps`
- `redis_member`
- `timestamp`

### 9.2 选择原因

- 可以平滑复用现有 Redis 发布和告警链路
- 减少对象转换复杂度
- 后续如果要显示更多机会细节，也更自然

## 10. 扫描执行方式

### 10.1 多币种轮询

每轮 scanner 采用：

- 币种之间串行
- 单个币种内部多交易所并发

即：

1. 遍历 `symbols`
2. 对当前 `symbol`，并发拉取多个交易所 orderbook
3. 计算该币种最佳机会
4. 写入 Redis
5. 继续下一个币种

### 10.2 这样做的原因

- 避免瞬时请求量过高
- 更容易定位单个币种异常
- 不会让一个币种的失败拖垮整轮所有币种

## 11. 稳定性与限流策略

### 11.1 推荐初始配置

- `SPOT_SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT`
- `ORDERBOOK_DEPTH_LIMIT=5`
- `TARGET_QUOTE_AMOUNT=100`
- `SCANNER_POLL_INTERVAL_SECONDS=2` 或 `3`

### 11.2 异常处理

如果某个交易所某个币种拉深度失败：

- 记录日志
- 跳过该交易所该轮数据
- 不终止整个 scanner

如果某个币种在该轮无法拿到至少两个交易所的有效深度：

- 本轮该币种不产出机会

## 12. Redis 输出设计

### 12.1 保留现有结构

Redis 仍然保留：

- `ZSET` 排行榜
- `Stream` 机会流

### 12.2 输出字段升级

`score` 继续使用：

- `spread_bps`

但 stream / payload 额外包含：

- `symbol`
- `buy_exchange`
- `sell_exchange`
- `effective_buy_price`
- `effective_sell_price`
- `target_quote_amount`
- `spread_bps`
- `buy_depth_levels_used`
- `sell_depth_levels_used`

这样 consumer、告警和后续执行层都能知道这是深度版机会，而不是 ticker 粗估。

## 13. 配置设计

新增配置项：

- `SPOT_SYMBOLS`
- `ORDERBOOK_DEPTH_LIMIT`
- `TARGET_QUOTE_AMOUNT`

继续保留并复用：

- `SPOT_EXCHANGES`
- `SCANNER_POLL_INTERVAL_SECONDS`
- `REDIS_URL`

## 14. 测试策略

### 14.1 单元测试

至少覆盖：

1. 多币种配置解析
2. orderbook 深度加权成交价计算
3. 不同交易所间最佳买卖腿选择
4. `SpotOpportunity` 扩展字段是否正确生成

### 14.2 组件测试

至少覆盖：

1. scanner 能遍历多个 symbol
2. 单个 symbol 内部并发抓取 orderbook
3. Redis 写入仍保持正常
4. 低深度/深度不足时不会错误产出机会

### 14.3 远端验证

远端至少验证：

1. 多个 symbol 在 Redis 中持续产生机会
2. `ZSET` / `Stream` 继续增长
3. scanner 在主流币白名单下稳定运行

## 15. 实施边界

本次只覆盖：

- 多币种白名单扫描
- REST orderbook 深度抓取
- 深度版 `SpotOpportunity` 计算
- Redis 输出字段升级

本次不覆盖：

- websocket 深度订阅
- 全市场币种扫描
- 执行器改造成深度驱动
- 更多交易所的细节优化

## 16. 结论

本次推荐采用“白名单多币种 + REST 深度快照 + 固定目标金额”的方案，在不推翻现有 worker、Redis、告警和远端部署体系的前提下，把单币种 ticker 级机会发现升级为更真实、更有业务价值的深度版多币种现货扫描。
