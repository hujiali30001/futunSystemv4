# 交易所会话统一关闭设计

## 1. 文档目标

本文档定义如何修复当前运行链路中的交易所资源释放问题，消除类似：

- `okx requires to release all resources with an explicit call to the .close() coroutine`

本次设计覆盖以下链路：

- 常驻 `scanner`
- 常驻 `consumer` 所依赖的现货执行链路
- `sandbox_probe`
- `spot_arbitrage_probe`

目标是在不推翻当前 worker、probe 和交易所适配结构的前提下，建立统一、明确、可复用的会话关闭规范。

## 2. 背景与现状

当前交易所访问的基本结构为：

1. `ExchangeClientFactory.create_session()` 创建 `ExchangeAccountSession`
2. 业务层基于 session 构建 `ExchangeAdapter`
3. 调用 `mark_ready()` 加载市场
4. 在调用结束后，由各业务链路自行 `adapter.close()`

已确认存在以下现状：

- `ExchangeAccountSession` 只有 `mark_ready()`，没有统一的 `close()` 生命周期方法
- `ExchangeAdapter.close()` 直接调用底层 client 的 `close()`
- `live_spot_flow` 在 `finally` 中批量关闭 adapter
- `sandbox_probe` 在 `finally` 中关闭单个 adapter
- `spot_arbitrage_probe` 在 `finally` 中批量关闭 adapter

这意味着当前关闭责任分散在多个调用点中，没有单一资源所有权边界。

## 3. 问题定义

当前结构的主要风险有四类：

1. 关闭职责分散，后续新增调用点时容易遗漏
2. 关闭协议不统一，session 与 adapter 的职责边界不清晰
3. 异常路径虽部分存在 `finally`，但缺少统一幂等保证
4. 当底层 ccxt client 对资源释放有严格要求时，分散式关闭更容易留下隐患

本次告警的根因不一定只来自“完全没调用 close”，也可能来自：

- 某些链路没有把 session 视为真正的资源所有者
- 重复关闭、异常关闭、部分初始化失败时没有统一收口
- 未来新代码继续照搬当前分散关闭方式，重复制造同类问题

## 4. 设计目标

本次修复满足以下目标：

1. 明确 `ExchangeAccountSession` 是底层交易所 client 的资源所有者
2. 为 session 提供统一异步关闭方法，并保证幂等
3. 让 adapter 层与业务层都复用同一套关闭协议
4. 覆盖成功路径、失败路径和部分初始化路径
5. 尽量保持现有业务调用结构不大改

## 5. 非目标

本次不做以下内容：

- 不重构为新的连接池或全局 session 注册中心
- 不改造成 websocket 长连接统一托管框架
- 不重写 worker 主循环
- 不处理 `bitget` 测试网业务错误
- 不扩展到数据库、Redis 以外的其他资源统一生命周期框架

## 6. 方案比较

### 6.1 方案 A：以 session 为中心统一关闭

做法：

- 在 `ExchangeAccountSession` 增加统一 `close()`
- `ExchangeAdapter.close()` 仅转调 session
- 所有业务链路继续保留自己的 `finally`，但都调用统一关闭协议

优点：

- 资源所有权最清晰
- 改动范围可控
- 对现有业务代码侵入较小
- 最适合当前项目渐进式修复

缺点：

- 调用点的 `finally` 仍然保留，未进一步抽象成上下文管理器

### 6.2 方案 B：继续以 adapter 为中心关闭

做法：

- 不改 session
- 只增强 `ExchangeAdapter.close()` 的幂等与异常保护

优点：

- 改动最小

缺点：

- 资源所有者仍然不明确
- session 继续只是“半生命周期对象”
- 后续容易再次出现边界混乱

### 6.3 方案 C：新增会话托管器或异步上下文

做法：

- 新增一层统一托管 session 创建、ready、close 的管理器

优点：

- 长期最整洁

缺点：

- 改动过重
- 当前为修复资源释放告警投入过大

## 7. 推荐方案

推荐采用 `方案 A：以 session 为中心统一关闭`。

选择原因：

- 能在当前代码结构上最自然地补齐生命周期边界
- 不需要推翻现有 worker、flow、probe 调用方式
- 可以同时覆盖 `scanner`、`consumer` 执行链路和两个 probe
- 后续若要继续抽象为上下文管理器，也能以 session `close()` 为基础逐步演进

## 8. 生命周期设计

### 8.1 资源所有权

统一定义：

- `ExchangeAccountSession` 持有底层 ccxt client
- session 是交易所连接资源的唯一所有者
- `ExchangeAdapter` 只是对 session 的操作包装，不拥有底层资源

这意味着：

- 真正的关闭逻辑必须位于 session
- adapter 只能做兼容层转发，不应再直接定义自己的底层关闭策略

### 8.2 Session 关闭语义

`ExchangeAccountSession.close()` 需要满足以下要求：

1. 如果 client 不存在，直接返回
2. 如果 client 不支持 `close()`，直接返回
3. 如果已经关闭过，再次调用直接返回
4. 如果调用 `close()` 过程中抛错，错误要向上返回，由调用方决定是否吞掉
5. 成功关闭后，记录已关闭状态，避免重复关闭

推荐增加的状态字段包括：

- `closed: bool = False`

必要时可在关闭后清理：

- `client`
- `markets`
- `markets_loaded`

其中，是否将 `client` 置空，以“避免重复使用已关闭 client”为首要原则。

### 8.3 Adapter 关闭语义

`ExchangeAdapter.close()` 保留，但只做以下事情：

- 调用 `await self.session.close()`

这样可以兼容现有调用点，避免一次性改完所有地方。

## 9. 调用链改造设计

### 9.1 `live_spot_flow`

保留 `finally` 中的批量关闭逻辑，但其关闭动作应统一走 session 关闭协议。

要求：

- 即使某个交易所在 `mark_ready()` 后、抓深度前或发布 Redis 前报错，也必须继续收口关闭已创建 session
- 批量关闭继续使用 `asyncio.gather(..., return_exceptions=True)`
- 如需记录关闭异常，应通过日志或后续事件体系记录，但不能覆盖主异常

### 9.2 `sandbox_probe`

`probe_exchange()` 与 `probe_order_lifecycle()` 保留 `finally`，但统一通过 session 关闭协议收口。

要求：

- 连接失败后也不应遗漏已创建 session 的释放
- 订单生命周期探测中任何一步失败，都必须关闭 session

### 9.3 `spot_arbitrage_probe`

保留批量关闭结构，但关闭逻辑统一走 session。

要求：

- 任一腿下单、查询、撤单失败时，已创建 session 仍需全部关闭
- 双腿并发执行失败不影响 finally 收口

### 9.4 `worker_service`

本次不要求 `worker_service` 额外直接管理交易所 session。

原因：

- 当前交易所 session 生命周期主要发生在 flow 与 probe 内部
- 先把底层 session 关闭协议统一，已能覆盖当前主要告警来源

## 10. 错误处理策略

关闭阶段遵循以下原则：

1. 主业务异常优先
2. 关闭异常不能吞掉主异常
3. 批量关闭时单个交易所关闭失败，不应阻断其他交易所继续关闭
4. 重复关闭不能再制造新的异常噪音

推荐执行方式：

- 调用点继续使用 `asyncio.gather(..., return_exceptions=True)`
- 对返回的关闭异常，可在后续补充低噪音日志，但不在本次修复中强制要求新增告警

## 11. 测试策略

### 11.1 Session 单元测试

至少覆盖：

1. client 存在且支持 `close()` 时会成功关闭
2. 重复调用 `close()` 只执行一次底层关闭
3. client 不存在时安全返回
4. client 不支持 `close()` 时安全返回

### 11.2 现有链路回归测试

至少覆盖：

1. `live_spot_flow.run_once()` 成功结束后会关闭全部 session
2. `live_spot_flow.run_once()` 异常退出后也会关闭已创建 session
3. `sandbox_probe.probe_exchange()` 完成后会关闭 session
4. `sandbox_probe.probe_order_lifecycle()` 完成后会关闭 session
5. `spot_arbitrage_probe.run_task()` 完成后会关闭全部 session
6. `spot_arbitrage_probe.run_task()` 异常退出后也会关闭已创建 session

### 11.3 远端验收

远端至少验证：

1. 常驻 `scanner` 持续运行，不再频繁出现 OKX 资源释放告警
2. `consumer` 正常消费 Redis 机会流
3. `sandbox_probe` 执行后无新的显式资源释放告警
4. `spot_arbitrage_probe` 执行后无新的显式资源释放告警

## 12. 实施边界

本次实施只覆盖：

- `app/exchanges/session_manager.py`
- `app/exchanges/adapters.py`
- `app/runtime/live_spot_flow.py`
- `app/runtime/sandbox_probe.py`
- `app/runtime/spot_arbitrage_probe.py`
- 对应测试文件

本次不要求：

- 改写成异步上下文管理器风格
- 引入新的 session 池抽象
- 修改现有 Redis 生命周期管理方式

## 13. 结论

本次采用“以 `ExchangeAccountSession` 为资源所有者”的统一关闭方案，把底层交易所 client 的关闭职责正式收口到 session，并让 adapter、scanner 链路和两个 probe 共用同一套幂等关闭协议。

这样可以在最小必要改动下，系统性降低资源释放遗漏和边界混乱问题，并为后续进一步抽象交易所连接生命周期打下基础。
