# 多用户跨交易所现期套利系统设计文档

## 1. 文档目标

本文档定义一个基于 Python 3.10、`asyncio`、`ccxt/ccxt.pro`、Redis 与 MySQL/PostgreSQL 的多用户跨交易所现期套利系统设计。系统面向生产环境，支持：

- 测试网与主网双模式兼容
- 同所现期套利与跨所现期套利
- 多用户账户隔离
- 每用户、每交易所账户、每代理的独立网络出口
- 全自动机会发现、下单、补偿、降级、恢复
- 多区域分布式部署

本文档聚焦架构设计、数据模型、状态机、风控和故障恢复，不包含最终实现代码。

## 2. 设计原则

### 2.1 核心原则

- 公共行情与私有交易彻底解耦
- 账户级客户端与代理隔离，避免多用户共享私有交易连接
- 任务驱动而非订单驱动，使用“套利任务”统一管理双腿执行与补偿
- 自动化优先，默认不依赖人工操作完成风险收敛
- 幂等优先，所有关键动作都必须支持重试与恢复
- 多区域部署时优先区域内就近执行，避免跨区域共享交易出口

### 2.2 非目标

首版不承诺以下目标：

- 覆盖所有交易所全部产品线与全部边缘功能
- 极端行情下零滑点、零残差
- 完全无人工审计与无人工配置

## 3. 问题定义

系统需要解决以下问题：

- 统一监听多个交易所现货与永续/交割合约行情
- 实时计算现期价差与含资金费率的预期收益
- 将公共套利机会分发给符合条件的多个用户
- 使用每个用户独立代理与独立 API 凭证执行对冲下单
- 在单边成交、代理失效、接口超时、交易所限流时自动补偿
- 在多区域部署中维持故障隔离、任务接管和状态恢复

## 4. 总体架构

### 4.1 逻辑分层

系统分为六层：

1. 公共行情层
2. 机会计算层
3. 平台治理与管理控制层
4. 用户策略与任务分发层
5. 账户级交易执行层
6. 风险补偿与恢复层

### 4.2 服务划分

建议首版按以下服务运行，而不是将所有能力塞入单一进程：

- `market-scanner`
  - 使用 `ccxt.pro` 订阅各交易所现货与合约 orderbook、funding rate、预测 funding
  - 只负责标准化市场数据，不做用户决策
- `opportunity-engine`
  - 基于标准化行情计算开仓价差、平仓价差、预期年化收益
  - 将机会写入 Redis ZSET 与事件流
- `strategy-dispatcher`
  - 读取用户策略配置、账户状态、风险阈值
  - 将全局机会转化为用户级套利任务
- `admin-control-plane`
  - 负责管理员额度规则、平台开关、公告管理、强制风控动作下发
  - 为分发层、执行层和风控层提供统一控制面
- `trade-worker`
  - 负责账户级 CCXT 实例化、代理注入、并发双腿下单、查单、撤单、补单
- `risk-manager`
  - 负责单边成交补偿、持仓风控、保证金风险处理、自动降级、恢复与冻结

### 4.3 首版代码落地映射

虽然运行时是分层服务，代码结构仍按需求中的五个主文件组织：

- `config.py`
- `models.py`
- `market_scanner.py`
- `trading_engine.py`
- `main.py`

其中：

- `market_scanner.py` 在首版中同时承载 `market-scanner` 与 `opportunity-engine`
- `trading_engine.py` 在首版中同时承载 `strategy-dispatcher`、`trade-worker`、基础 `risk-manager` 与管理控制规则校验
- `main.py` 提供按角色启动不同服务的入口

## 5. 数据流设计

### 5.1 开仓链路

1. `market-scanner` 采集现货与合约 orderbook、funding rate
2. 标准化后写入 Redis 市场缓存与 `stream:market_events`
3. `opportunity-engine` 计算价差与年化收益
4. 结果写入 `arb:zset:open`、`arb:zset:close` 和 `stream:opportunities`
5. `strategy-dispatcher` 根据用户阈值、账户余额、风控规则筛选机会
6. `admin-control-plane` 提供平台开关、额度规则与公告强提醒状态
7. 为命中的用户创建 `arbitrage_task`
8. `trade-worker` 获取任务并实例化用户专属交易上下文
9. 下单前进行余额、精度、最小下单量、杠杆、代理健康、额度规则校验
10. 使用 `asyncio.gather()` 并发提交现货腿与合约腿
11. 进入异步成交确认与状态推进
12. 成功则进入持仓状态，失败则进入自动补偿流程

### 5.2 平仓链路

1. `opportunity-engine` 持续计算平仓价差
2. `strategy-dispatcher` 对已持仓任务评估平仓阈值、资金费率窗口、风控退出条件
3. `admin-control-plane` 可下发只减仓、强制减仓、强制平仓等控制信号
4. `trade-worker` 并发提交平仓双腿
5. `risk-manager` 处理单边未平、订单丢失、代理异常等场景
6. 任务最终进入 `CLOSED` 或自动隔离状态

## 6. 数据模型

### 6.1 关系数据库模型

#### `users`

保存用户基础信息、状态、风控等级、所属区域和是否启用自动交易。

建议字段：

- `id`
- `username`
- `status`
- `risk_level`
- `home_region`
- `is_trading_enabled`
- `default_quote_currency`
- `created_at`
- `updated_at`

#### `proxies`

保存代理出口配置与健康状态。

建议字段：

- `id`
- `proxy_type`
- `host`
- `port`
- `username`
- `password_ciphertext`
- `region`
- `provider`
- `health_status`
- `last_checked_at`
- `created_at`
- `updated_at`

#### `exchange_accounts`

一行代表用户在某交易所的一个账户实例。

建议字段：

- `id`
- `user_id`
- `exchange`
- `account_label`
- `market_type_scope`
- `env_mode`
- `api_key_ciphertext`
- `secret_ciphertext`
- `passphrase_ciphertext`
- `proxy_id`
- `is_enabled`
- `is_auto_trade_enabled`
- `account_region`
- `created_at`
- `updated_at`

约束建议：

- `user_id + exchange + account_label + env_mode` 唯一

#### `user_strategy_configs`

保存用户级交易策略参数。

建议字段：

- `id`
- `user_id`
- `is_enabled`
- `open_spread_threshold_bps`
- `close_spread_threshold_bps`
- `min_annualized_yield_bps`
- `max_single_order_notional`
- `max_total_exposure`
- `slippage_tolerance_bps`
- `execution_preference`
- `allowed_exchanges_json`
- `symbol_whitelist_json`
- `symbol_blacklist_json`
- `auto_transfer_enabled`
- `auto_recover_enabled`
- `created_at`
- `updated_at`

#### `symbols`

维护标准化交易对与交易所映射关系。

建议字段：

- `id`
- `canonical_symbol`
- `base_asset`
- `quote_asset`
- `spot_symbol_map_json`
- `swap_symbol_map_json`
- `future_symbol_map_json`
- `spot_precision_json`
- `derivative_precision_json`
- `lot_size_json`
- `contract_size_json`
- `status`

#### `arbitrage_tasks`

系统编排的核心表，一条记录代表一次完整套利任务。

建议字段：

- `id`
- `task_uuid`
- `user_id`
- `strategy_config_id`
- `opportunity_id`
- `env_mode`
- `task_type`
- `symbol`
- `spot_exchange`
- `derivative_exchange`
- `target_notional`
- `expected_spread_bps`
- `expected_funding_bps`
- `status`
- `status_reason`
- `idempotency_key`
- `home_region`
- `created_at`
- `updated_at`

约束建议：

- `idempotency_key` 唯一

#### `arbitrage_legs`

每个套利任务下包含两条或多条腿。

建议字段：

- `id`
- `task_id`
- `leg_type`
- `exchange_account_id`
- `side`
- `market_type`
- `order_type`
- `target_price`
- `target_qty`
- `reduce_only`
- `hedge_mode`
- `status`
- `created_at`
- `updated_at`

#### `order_records`

保存标准化订单结果与交易所回执。

建议字段：

- `id`
- `task_id`
- `leg_id`
- `exchange_account_id`
- `client_order_id`
- `exchange_order_id`
- `status`
- `price`
- `avg_price`
- `qty`
- `filled_qty`
- `fee_json`
- `raw_payload_json`
- `created_at`
- `updated_at`

#### `fill_records`

保存成交明细，用于计算滑点、真实对冲比例与实际盈亏。

#### `position_snapshots`

保存现货余额、合约持仓、保证金、强平价、未实现盈亏等快照。

#### `risk_events`

保存单边成交、自动补偿、保证金风险、代理故障、交易所限流、账户隔离等事件。

#### `asset_transfers`

保存内部划转请求与结果。

#### `audit_logs`

记录关键配置变更与系统控制行为。

#### `admin_users`

保存管理员账户、角色与状态。

#### `admin_roles`

保存管理员角色与权限集合，例如超级管理员、风控管理员、运营管理员。

#### `admin_action_logs`

记录管理员对用户、账户、规则、公告、平台开关的操作日志。

建议字段：

- `id`
- `admin_user_id`
- `action_type`
- `target_type`
- `target_id`
- `before_json`
- `after_json`
- `reason`
- `source_ip`
- `created_at`

#### `risk_limit_rules`

统一保存平台和用户侧额度规则。

建议字段：

- `id`
- `scope_type`
- `scope_id`
- `symbol`
- `exchange`
- `strategy_config_id`
- `limit_type`
- `limit_value`
- `enabled`
- `priority`
- `effective_from`
- `effective_to`
- `created_by`
- `created_at`
- `updated_at`

规则层级应至少支持：

- 平台总额度
- 用户总额度
- 用户按币种额度
- 用户按交易所额度
- 用户按策略额度
- 单次任务额度

#### `platform_switches`

保存平台级治理开关。

建议字段：

- `id`
- `switch_key`
- `switch_value`
- `scope_type`
- `scope_id`
- `effective_from`
- `effective_to`
- `updated_by`
- `updated_at`

建议支持的开关包括：

- 全平台停新仓
- 全平台只减仓
- 按交易所停新仓
- 按币种停新仓
- 按用户停新仓

#### `announcements`

保存公告主体内容与投放规则。

建议字段：

- `id`
- `title`
- `content`
- `priority`
- `is_pinned`
- `audience_type`
- `audience_filter_json`
- `channels_json`
- `requires_ack`
- `effective_from`
- `effective_to`
- `status`
- `created_by`
- `created_at`
- `updated_at`

#### `announcement_receipts`

保存公告触达、已读与外部推送结果。

建议字段：

- `id`
- `announcement_id`
- `user_id`
- `channel`
- `delivery_status`
- `delivery_result_json`
- `is_read`
- `read_at`
- `created_at`

### 6.2 模型设计原则

- 任务与订单分离
- 配置与事实分离
- 测试网与主网显式区分
- 账户与代理分离
- 管理规则与用户策略分离
- 数据恢复以事实账本与交易所真实状态为准

## 7. Redis 结构

### 7.1 市场缓存

- `md:orderbook:{exchange}:{market_type}:{symbol}`
- `md:funding:{exchange}:{symbol}`
- `md:ticker:{exchange}:{market_type}:{symbol}`

### 7.2 机会排行

- `arb:zset:open`
- `arb:zset:close`
- `arb:opp:{opportunity_id}`

### 7.3 事件流

- `stream:market_events`
- `stream:opportunities`
- `stream:trade_tasks`
- `stream:risk_events`
- `stream:reconcile_events`

### 7.4 锁与幂等

- `lock:task:{task_id}`
- `lock:user_symbol:{user_id}:{symbol}`
- `lock:account_order:{exchange_account_id}`
- `idem:submit:{idempotency_key}`

### 7.5 健康状态与路由

- `health:proxy:{proxy_id}`
- `health:worker:{worker_id}`
- `cache:balances:{exchange_account_id}`
- `cache:positions:{exchange_account_id}`
- `route:user_worker:{user_id}`

### 7.6 管理控制缓存

- `admin:switch:{scope_type}:{scope_id}`
- `risk:limit:{scope_type}:{scope_id}`
- `announcement:active`
- `announcement:user:{user_id}`

## 8. 价差计算与机会评分

### 8.1 开仓价差

`(合约买一价 - 现货卖一价) / 现货卖一价`

### 8.2 平仓价差

`(合约卖一价 - 现货买一价) / 现货买一价`

### 8.3 机会评分

机会评分可采用以下任一或组合方式：

- 纯价差百分比
- 含资金费率的预期年化收益
- 扣除预估手续费与滑点后的净收益分数
- 根据深度可成交量折算后的实际容量分数

建议首版使用以下综合分数：

`net_score = spread_bps + funding_adjust_bps - fee_bps - slippage_bps`

## 9. 任务状态机

### 9.1 主状态

- `CREATED`
- `RESERVED`
- `PRECHECKING`
- `SUBMITTING`
- `OPEN_PARTIAL`
- `OPEN_HEDGED`
- `AUTO_HEDGE_REPAIRING`
- `AUTO_FLATTENING`
- `HOLDING`
- `CLOSING`
- `CLOSE_PARTIAL`
- `AUTO_DEGRADED`
- `AUTO_QUARANTINED`
- `CLOSED`
- `FAILED`
- `MANUAL_REQUIRED`

### 9.2 腿状态

- `PENDING`
- `SUBMITTED`
- `PARTIALLY_FILLED`
- `FILLED`
- `CANCELED`
- `REJECTED`
- `EXPIRED`
- `REPAIRING`

### 9.3 状态推进原则

- 所有状态推进使用条件更新，避免并发覆盖
- 所有任务恢复以交易所订单查询结果优先
- `MANUAL_REQUIRED` 仅用于自动系统已完成降风险后仍无法确认最终一致性的极端场景

## 10. 风控设计

### 10.1 准入风控

下单前校验：

- 平台级开关是否允许开新仓
- 用户状态是否允许自动交易
- 账户是否启用
- API 权限是否完整
- 代理是否健康
- 标的是否在白名单内
- 是否超过用户/币种/交易所敞口限制

### 10.2 资金风控

- 同所现期检查现货与衍生品账户余额是否可用
- 跨所现期要求两边账户库存同时满足
- 启用自动划转时，可在允许范围内执行内部划转
- 禁止在明显库存不足时先下单后补资产

### 10.3 管理额度风控

系统必须支持统一额度规则引擎，按如下优先级计算实际允许额度：

`平台总额度 > 用户总额度 > 用户按币种额度 > 用户按交易所额度 > 用户按策略额度 > 单次任务额度`

设计要求：

- `strategy-dispatcher` 在创建任务前预先裁剪目标名义金额
- `trade-worker` 在下单前再次校验剩余额度，防止并发超额
- 当额度不足但仍可部分成交时，允许自动缩量
- 当额度规则变更为只减仓时，系统必须立即阻止新开仓
- 对已持仓任务，额度规则只影响是否允许加仓，不阻止平仓与减仓

### 10.4 执行风控

- 使用 orderbook top5 深度估算冲击成本
- 预估滑点超阈值时拒绝交易或缩小下单量
- 按用户偏好支持市价单或保护价格带限价单
- 所有数量与价格按 `load_markets()` 返回精度自动裁剪

### 10.5 持仓风控

持续监控：

- 对冲比例
- 强平价距离
- 保证金率
- 未实现盈亏
- 资金费率窗口
- 网络与接口异常时长

### 10.6 管理强制动作

管理员应可下发以下控制动作，并要求运行时即时生效：

- 暂停某用户自动交易
- 暂停某账户或某交易所新开仓
- 启用只减仓模式
- 强制减仓到指定额度以下
- 强制平仓
- 冻结账户并进入自动隔离状态
- 下发平台风险模式，例如临时降低全平台单笔开仓额度

生效要求：

- 管理动作优先级高于用户策略配置
- 对已进入交易所的订单只能执行后续撤单、减仓、平仓，不能回滚已成交事实
- 所有强制动作必须写入 `admin_action_logs` 与 `audit_logs`

### 10.7 自动化风险处置

系统默认目标是“自动发现、自动执行、自动补偿、自动降级、自动恢复”。

处置策略采用分层模型：

- 高流动性标的优先自动补对冲
- 低流动性或高波动标的优先自动保命退出
- 高风险账户缩短补单超时与追价带宽
- 低风险账户可给予更长补单窗口，但必须有重试上限

### 10.8 自动化动作

系统必须支持以下自动化动作：

- 自动补单
- 自动撤单
- 自动减仓
- 自动平仓
- 自动切换备用代理
- 自动重建 CCXT 会话
- 自动冻结账户开新仓权限
- 自动恢复健康账户
- 自动告警与审计落库

## 11. 多用户独立 Proxy 与 CCXT 客户端设计

### 11.1 账户级上下文

一个 `exchange_account` 对应一个私有交易上下文，至少包含：

- 账户元信息
- 环境模式
- 代理配置
- `ccxt` 或 `ccxt.pro` 客户端实例
- 精度与市场元数据缓存
- 账户级限流器
- 时间同步状态
- 最近错误计数

### 11.2 代理注入原则

- 所有私有交易请求必须使用账户绑定代理
- 代理配置只从数据库或安全配置源读取
- 运行中禁止任务级覆盖代理，避免串线
- 代理健康失败时禁止开新仓

### 11.3 生命周期

- 懒加载创建
- 首次交易前预热 `load_markets()`
- 运行中连接复用
- 长时间空闲自动回收
- 连续异常自动销毁并重建

### 11.4 统一适配层

业务层不直接操作具体交易所客户端，而是通过统一适配器接口操作：

- `load_markets()`
- `fetch_balance()`
- `fetch_positions()`
- `set_leverage()`
- `ensure_margin_mode()`
- `create_spot_order()`
- `create_derivative_order()`
- `cancel_order()`
- `fetch_order()`
- `transfer_asset()`

## 12. 并发执行设计

### 12.1 任务级并发

套利任务内部使用 `asyncio.gather()` 并发提交现货腿与合约腿，目标是在毫秒级窗口内完成双腿送单。

### 12.2 账户级串并控制

- 同一账户允许多任务并发，但受账户级限流器控制
- 同一用户同一 symbol 应持有开仓锁，避免重复建仓
- 设置杠杆、切换保证金模式、切换持仓模式等动作必须串行

### 12.3 精度与保护逻辑

- 下单前根据 `load_markets()` 的精度与限额校验价格和数量
- 保护价格必须基于最新深度与滑点容忍度计算
- 当深度不足以支撑目标名义金额时，允许自动缩量

## 13. 自动补偿策略

### 13.1 开仓补偿

- 现货成交、合约未成交：优先补合约腿；若偏离超阈值则自动反向平掉现货腿
- 合约成交、现货未成交：优先补现货腿；若偏离超阈值则自动回补合约平风险
- 双边已提交但回报丢失：进入自动对账，以交易所查询结果恢复状态

### 13.2 平仓补偿

- 一条腿先平掉、另一条腿未平：剩余腿优先使用 reduce-only 或等效安全模式退出
- 若交易所限流或代理故障导致补偿失败：自动切换备用连接，失败则自动进入 `AUTO_DEGRADED`

### 13.3 恢复优先级

自动恢复按以下顺序尝试：

1. 查单恢复
2. 补单对冲
3. 撤单重发
4. 自动减仓
5. 自动全平
6. 自动隔离账户

## 14. 多区域分布式部署

### 14.1 区域角色

- 区域公共层：行情接入与本地机会计算
- 区域私有层：账户级执行与风控
- 全局控制层：配置管理、审计、全局开关、汇总报表

### 14.2 任务路由

- 用户与账户绑定 `home_region`
- 任务默认路由到所属区域 worker
- 正常情况下不允许任意区域抢占其他区域账户任务
- 管理员公告与平台开关应跨区域同步，但交易执行仍保持区域内生效

### 14.3 高可用与接管

- worker 周期性上报心跳
- 任务采用租约机制
- 租约失效后由同区域 worker 接管
- 接管前必须查数据库和交易所真实状态

### 14.4 灾备原则

- Redis 缓存可重建，但数据库事实状态不可丢
- 区域故障时优先自动降风险，而不是优先扩张新交易
- 仅在代理与订单查询链路完整时允许跨区域灾备接管

## 15. 安全设计

### 15.1 密钥保护

- API Key、Secret、Passphrase 必须加密存储
- 应使用应用层密钥管理机制解密后在内存短暂使用
- 禁止在日志、告警、异常栈中输出明文凭证

### 15.2 操作审计

必须记录以下事件：

- 管理员公告发布、撤回、置顶
- 管理员额度规则变更
- 平台开关变更
- 用户策略变更
- 代理切换
- 账户启停
- 自动熔断
- 自动恢复
- 自动平仓
- 自动隔离

### 15.3 最小权限

- 管理后台与运行服务分离
- 只授予服务必要数据库与 Redis 权限
- 测试网与主网凭证逻辑隔离

## 16. 监控与告警

关键监控指标：

- 平台总敞口占用率
- 用户额度命中率
- 被管理规则拒绝的任务数
- 行情延迟
- 机会生成速率
- 任务创建速率
- 下单成功率
- 单边成交率
- 自动补偿成功率
- 自动平仓成功率
- 账户熔断数
- 代理健康率
- 区域 worker 心跳

关键告警事件：

- 平台额度接近上限
- 某用户额度反复触顶
- 平台被切换为只减仓或停新仓
- 单边裸露超时
- 保证金风险升高
- 自动补偿失败
- 某交易所连续拒单
- 某代理批量失效
- 某区域 worker 全部离线

## 17. 首版实施边界

### 17.1 首版必须完成

- 公共行情扫描
- Redis 机会排行
- 双模式兼容
- 同所与跨所现期统一任务模型
- 管理控制平面基础能力
- 分层额度规则引擎
- 站内公告与外部推送能力
- 用户独立代理与账户级 CCXT 会话
- 异步双腿并发下单
- 自动补偿与自动降级
- 基础持仓风控
- 审计与告警基础能力

### 17.2 次阶段增强

- 自动划转全覆盖
- 更细粒度资金费率优化
- 多区域自动灾备接管完善
- 组合级净敞口管理
- 运维后台与策略可视化
- 更复杂的多渠道通知编排与模板化消息

## 18. 风险与开放问题

### 18.1 已知风险

- 各交易所合约产品接口字段差异较大
- 部分交易所测试网能力弱于主网
- 代理质量直接影响交易执行稳定性
- 极端行情中补单与保命退出之间存在不可避免的收益/风险权衡

### 18.2 已定设计取舍

- 默认优先自动化收敛风险，不依赖人工实时介入
- 默认按账户级隔离 CCXT 会话与代理，不追求最少实例数
- 默认以数据库事实表加交易所真实查询作为恢复依据
- 默认采用统一管理规则引擎，而不是把额度字段散落在用户表中

## 19. 验收标准

设计落地后，系统应至少满足以下验收标准：

- 支持 5 家目标交易所的统一行情监听接口
- 支持主网与测试网环境切换
- 支持同所现期与跨所现期任务建模
- 支持平台总额度、用户总额度、币种额度、交易所额度、策略额度、单次任务额度控制
- 支持管理员发布站内公告并通过外部通道推送
- 支持每用户独立代理与独立私有交易会话
- 双腿订单可通过异步并发方式提交
- 出现单边成交时可自动补偿或自动保命退出
- worker 重启后可自动恢复未完成任务
- 关键自动化动作、审计和告警可落库与查询

## 20. 结论

该系统应采用“公共行情共享、私有交易隔离、任务编排驱动、自动补偿闭环、多区域分布式部署”的架构。首版实现重点不是覆盖所有交易所细枝末节，而是优先打通以下生产关键链路：

- 统一行情采集
- 用户级机会筛选
- 账户级独立代理交易
- 双腿并发执行
- 自动补偿与自动恢复
- 幂等状态管理与故障恢复

在该架构下，后续可以逐步扩展更多交易所差异适配、划转能力、组合风控与运维界面，而不会推翻核心设计。
