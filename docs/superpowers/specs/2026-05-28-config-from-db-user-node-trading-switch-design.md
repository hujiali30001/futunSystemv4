# 配置入库 + 用户节点绑定 + 交易开关可视化 设计文档

## 1. 文档目标

本文档定义三个关联功能，共同实现"所有配置进 DB、每用户在自己的节点用自己 API Key 交易、用户可从控制台控制开关"：

- **配置入库**：淘汰 `.env.worker` 中非启动必需的参数，迁入 `platform_config` 表，管理后台可编辑
- **用户节点绑定**：`users` 表新增 `node_id`，替换 env 中的 `user_node_routes` CSV 配置
- **交易开关可视化**：控制台首页加交易状态卡片 + 总开关，executor 加二层保护

## 2. 第一部分：配置入库

### 2.1 `platform_config` 表

```python
class PlatformConfig(TimestampMixin, Base):
    __tablename__ = "platform_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    config_value: Mapped[str] = mapped_column(Text, nullable=False)
    config_type: Mapped[str] = mapped_column(String(32), default="string")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
```

### 2.2 迁移策略：留 `.env` vs 迁 DB

| 原 `.env` 字段 | 留 `.env` | 迁 DB | 原因 |
|----|:--:|:--:|------|
| DATABASE_URL | ✅ | | 启动先决条件（鸡和蛋） |
| REDIS_URL | ✅ | | 同上 |
| ENCRYPTION_KEY | ✅ | | 密钥，不能存 DB |
| NODE_ID | ✅ | | 进程级标识 |
| WORKER_ROLE | ✅ | | CLI 参数 |
| ALERT_* 类 | | ✅ | 告警通知配置 |
| env_mode | | ✅ | 环境模式 |
| spot_exchanges | | ✅ | 交易所列表 |
| target_quote_amount | | ✅ | 默认名义金额 |
| scanner_poll_interval_seconds | | ✅ | Scanner 轮询间隔 |
| arb_scanner_poll_interval_seconds | | ✅ | Arb scanner 轮询间隔 |
| orderbook_depth_limit | | ✅ | 订单簿深度 |
| consumer_block_ms | | ✅ | 消费阻塞时间 |
| dispatch_source_stream | | ✅ | 数据源流 key |
| executor_stream_key | | ✅ | executor 流 key |
| repair_stream_key | | ✅ | repair 流 key |
| route_admin_enabled / port / token | | ✅ | 路由管理 |
| control_admin_enabled / port / token | | ✅ | 控制面管理 |
| spot_symbol | | ✅ | 单币种符号 |
| spot_symbols | | ✅ | 多币种列表 |
| spot_symbols_auto_quote | | ✅ | auto 报价货币 |
| database_enabled | | ✅ | 是否启用 DB |
| worker_region | | ✅ | worker 区域 |
| dispatch_user_ids | | ✅ | 分发用户 ID（可被 DB 查询替代后续淘汰） |
| user_node_routes | | ✅ | 旧格式，被第二部分替代 |

### 2.3 Worker 启动流程变更

```
之前：读 .env → pydantic WorkerSettings → 直接用
之后：读 .env (最小) → 查 platform_config 表 → 合并为 WorkerSettings → 启动
```

`WorkerSettings` 不变，新增 `load_platform_config()` 函数在 `worker_service.py` 的 `DefaultWorkerFactory` 初始化时调用，用 DB 中的值覆盖默认值。

```python
def load_platform_config(session: Session, defaults: dict) -> dict:
    rows = session.query(PlatformConfig).all()
    for row in rows:
        defaults[row.config_key] = _coerce(row.config_value, row.config_type)
    return defaults
```

### 2.4 管理后台端点

在 `app/api/admin/` 下新增 `configs.py`：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/admin/configs | 列表所有配置 |
| PUT | /api/admin/configs/{config_key} | 更新单个配置 + 记录 admin_action_log |
| GET | /api/admin/configs/{config_key} | 获取单个配置 |

### 2.5 首页配置

`platform_config` 表预置默认行（首次迁移脚本写入）：

| config_key | config_value | config_type |
|------------|-------------|-------------|
| env_mode | testnet | string |
| spot_exchanges | ["okx","binance","bybit","bitget","gate"] | json |
| target_quote_amount | 10.0 | float |
| arb_scanner_poll_interval_seconds | 5.0 | float |
| alert_feishu_webhook | | string |
| alert_feishu_enabled | false | bool |

---

## 3. 第二部分：用户绑定节点

### 3.1 `users` 表新增字段

```python
node_id: Mapped[str] = mapped_column(String(64), default="main")
```

### 3.2 Dispatcher 路由变更

`RedisArbitrageTaskDispatcher` 的 `_resolve_candidate_user_ids()` 改为从 `User.node_id` 读：

```python
# 之前：route_resolver.get_user_node(user_id)
# 之后：candidate.node_id
```

`RedisNodeTaskDispatcher` 同理。

### 3.3 管理后台

管理员用户管理页加 `node_id` 列。编辑用户时可修改。默认 "main"。

### 3.4 淘汰 `USER_NODE_ROUTES` env var

第二部分完成后，`user_node_routes` CSV 配置不再需要。Dispatcher 启动时的 `sync_default_routes` 逻辑移除。

---

## 4. 第三部分：交易开关可视化

### 4.1 控制台首页交易状态卡片

已登录用户访问排行榜首页时，顶部渲染状态栏：

```
┌──────────────────────────────────────────────────┐
│ 🟢 交易运行中  [⏸ 暂停自动交易]   策略: 2 | 节点: main │
│                                                  │
│ 🔴 交易已暂停  [▶ 启用自动交易]   策略: 0 | 节点: main │
└──────────────────────────────────────────────────┘
```

对应 `User.is_trading_enabled` 字段。调用 `PATCH /api/auth/me/trade-toggle` 切换。

### 4.2 后端 toggle 端点

PATCH `/api/auth/me/trade-toggle`：取反 `current_user.is_trading_enabled`，保存，返回新状态。

### 4.3 Executor 二层保护

`ArbitrageExecutionTaskConsumer.run_once` 认领任务后，加一层检查：

```python
task = self.task_repository.claim_next_executable_task(...)
if task is None:
    return 0

if not self._is_user_trading_enabled(task.user_id):
    self.task_repository.mark_skipped(task.task_uuid, reason="user_trading_disabled")
    return 0
```

`_is_user_trading_enabled` 用 `getattr(task, 'user', None)` 或独立 DB 查询。

### 4.4 策略开关保持

策略页的独立开关 (`PATCH /api/strategies/{id}/toggle`) 保持不变。Dispatcher 的 `list_enabled_for_user` 已按 `is_enabled` 过滤。

### 4.5 无策略引导

用户注册后没有任何策略 → 排行榜首页引导"点击任一币对的【开始套利】创建第一条策略"。

---

## 5. 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `models.py` | 新增 PlatformConfig；User 加 node_id |
| 新建 | `app/api/admin/configs.py` | 管理后台配置 CRUD |
| 修改 | `app/api/admin/__init__.py` | 注册 configs 路由 |
| 修改 | `app/api/admin/users.py` | 用户管理返回 + 编辑 node_id |
| 修改 | `app/api/auth.py` | 新增 PATCH /me/trade-toggle |
| 修改 | `app/api/positions.py` | 无改动（继续保持现有 enrich） |
| 修改 | `app/runtime/worker_service.py` | DefaultWorkerFactory 从 DB 加载 platform_config |
| 修改 | `app/runtime/live_workers.py` | Dispatcher 读 user.node_id；Executor 加 trading_enabled 检查 |
| 修改 | `app/db/dispatch_user_repository.py` | 候选用户查询带上 node_id |
| 新建 | `web/src/components/TradeStatusCard.tsx` | 交易状态卡片组件 |
| 修改 | `web/src/pages/LeaderboardPage.tsx` | 顶部嵌入 TradeStatusCard |
| 修改 | `web/src/pages/strategies/StrategyListPage.tsx` | 空状态引导 |
| 修改 | `web/src/api.ts` | 新增 toggle 接口 |
| 修改 | `web/src/pages/admin/` | 用户管理页加 node_id；新增配置管理页 |

## 6. 不涉及

- 不新增新的 dispatcher 或 executor 角色
- 不修改 Scanner 逻辑
- 不修改订单执行链路
- 不新增 Redis 数据结构
- 不修改 API Key 加密解密逻辑

---

*关联设计文档 [2026-05-24-cross-exchange-arbitrage-design.md](2026-05-24-cross-exchange-arbitrage-design.md) 第 6 节、第 14 节。*
