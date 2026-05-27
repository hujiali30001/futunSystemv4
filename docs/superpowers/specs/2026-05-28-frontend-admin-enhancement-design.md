# 前端完善 + 管理员功能 设计文档

## 1. 文档目标

本文档定义对现有"多用户跨交易所现期套利系统"前端和管理员功能的增强计划，包含三个阶段：

- **阶段 A**：WebSocket 实时推送 + 持仓监控页面 + 用户个人设置页
- **阶段 C**：管理后台（admin_users 表、可视化控制台、审计日志）+ API 端口合并
- **阶段 B**：多级开清仓阈值 UI + 收益走势图

## 2. 设计原则

- **端口统一**：将 :8788 (control_admin) 和 :8787 (route_admin) 管理 API 合并到 FastAPI（:8000）`/api/admin/...` 路径下，共用 JWT 鉴权
- **用户独占通知**：每个用户自有飞书 Webhook + 邮箱，告警改为按 user_id 查配置发送
- **独立管理员账号**：admin_users 表 + 独立登录页，不与用户共用
- **审计全覆盖**：所有管理操作落 admin_action_logs 表
- **渐进式**：三个阶段分别独立交付，不做大爆炸

## 3. 阶段 A：WebSocket 实时推送 + 持仓监控

### 3.1 WebSocket 端点

新增 `app/api/ws.py`：

```
WS /api/ws/leaderboard?direction=spot_futures
```

行为：
- 连接后首次推送全量排行榜（复用现有 `leaderboard` 接口逻辑，但不过分页，推 TOP 100）
- 之后每 5 秒增量推送变化的行（基于 `sort_value` diff）
- 客户端收到更新后原地替换行数据，不跳页不重置用户操作

前端 `LeaderboardPage` 改动：
- 初始加载仍走 HTTP（保证首屏速度）
- 页面 `onMount` 后建立 WS 连接，收到增量数据 patch 到 `rows` state
- 切换 tab（现期/期现）时关闭旧 WS 开新 WS
- 暂停刷新 = 断开 WS + 保留最后数据

### 3.2 持仓监控页

路由：`/positions`

数据来源：`arbitrage_tasks` 表 `WHERE status = 'HOLDING' OR status = 'OPEN_HEDGED'`

展示字段：

| 列 | 来源 |
|----|------|
| 币种 | `symbol` |
| 现期方向 | `task_type` + `spot_exchange` / `derivative_exchange` |
| 开仓价差 | `expected_spread_bps` / 100 → % |
| 资金费率 | `expected_funding_bps` / 100 → % |
| 持仓量 | `target_notional` + 成交数量和价格 |
| 当前状态 | `execution_status` / `auto_recovery_status` |

API 端点：
```
GET /api/positions?status=holding    → 返回当前用户所有持仓任务
GET /api/positions/{task_uuid}       → 单个任务详情
```

### 3.3 用户个人设置页

路由：`/settings`

功能区块：
- **邮箱**：编辑 users.email
- **飞书 Webhook URL**：编辑 users.feishu_webhook_url
- **交易所 API Key**：按交易所列出现有 exchange_accounts 记录（5 所：binance / okx / bybit / gate / bitget），每个可编辑 api_key / secret / passphrase / 启用开关 / 代理

API 端点：
```
GET  /api/settings              → 返回当前用户信息 + 交易所账户列表
PUT  /api/settings/profile      → 更新 email + feishu_webhook_url
POST /api/settings/exchange     → 新增交易所账户
PUT  /api/settings/exchange/{id} → 更新交易所账户
DELETE /api/settings/exchange/{id} → 删除交易所账户
```

## 4. 阶段 C：管理后台 + API 合并

### 4.1 数据模型新增

`models.py` 新增两表：

```python
class AdminUser(TimestampMixin, Base):
    __tablename__ = "admin_users"
    id: Mapped[int]
    username: Mapped[str]        # 唯一
    password_hash: Mapped[str]   # bcrypt
    role: Mapped[str]            # superadmin / risk_admin / ops_admin

class AdminActionLog(TimestampMixin, Base):
    __tablename__ = "admin_action_logs"
    id: Mapped[int]
    admin_user_id: Mapped[int]   # FK → admin_users.id
    action_type: Mapped[str]     # create / update / delete / toggle / force_close 等
    target_type: Mapped[str]     # limit_rule / switch / announcement / user_account
    target_id: Mapped[str | None]
    before_json: Mapped[dict | None]
    after_json: Mapped[dict | None]
    reason: Mapped[str | None]
    source_ip: Mapped[str | None]
```

`users` 表新增字段：

```python
email: Mapped[str | None] = mapped_column(String(255), nullable=True)
feishu_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
```

### 4.2 API 端口合并

将 `route_admin_service.py` 和 `control_admin_service.py` 的管理 API 合并到 FastAPI 下：

| 原 :8787/:8788 端点 | 新端点 | 认证 |
|-----|-----|-----|
| `GET /routes` | `GET /api/admin/routes` | Admin JWT (role: superadmin/ops_admin) |
| `PUT /routes/{user_id}` | `PUT /api/admin/routes/{user_id}` | Admin JWT |
| `GET /control/limits` | `GET /api/admin/limits` | Admin JWT |
| `POST /control/limits` | `POST /api/admin/limits` | Admin JWT (role: risk_admin+) |
| `PUT /control/limits/{id}` | `PUT /api/admin/limits/{id}` | Admin JWT |
| `DELETE /control/limits/{id}` | `DELETE /api/admin/limits/{id}` | Admin JWT |
| `GET /control/switches` | `GET /api/admin/switches` | Admin JWT |
| `PUT /control/switches/{key}` | `PUT /api/admin/switches/{key}` | Admin JWT |
| `GET /announcements` | `GET /api/admin/announcements` | Admin JWT |
| `POST /announcements` | `POST /api/admin/announcements` | Admin JWT |
| `PUT /announcements/{id}` | `PUT /api/admin/announcements/{id}` | Admin JWT |
| `DELETE /announcements/{id}` | `DELETE /api/admin/announcements/{id}` | Admin JWT |

新增端点：
```
POST /api/admin/login              → 管理员登录，返回 Admin JWT (含 role)
GET  /api/admin/audit              → 操作审计日志，分页查询
GET  /api/admin/users              → 用户列表（方便管理员查用户状态）
```

鉴权方式：
- 管理员 JWT 含 `admin_id` + `admin_role`，与用户 JWT 区分
- 前端管理页面用 `AdminGuard` 组件拦截，无 token 跳 `/admin/login`

### 4.3 管理后台前端页面

路由前缀：`/admin`

| 路由 | 页面 | 可见角色 |
|------|------|---------|
| `/admin/login` | 管理员登录 | 无需登录 |
| `/admin/limits` | 额度规则 CRUD | superadmin, risk_admin |
| `/admin/switches` | 平台开关控制 | superadmin, risk_admin |
| `/admin/announcements` | 公告管理 | superadmin, ops_admin |
| `/admin/audit` | 操作审计日志 | superadmin |
| `/admin/users` | 用户列表 + 状态 | superadmin, ops_admin |

前端实现方式：
- 管理后台用 React 同项目内独立路由组（`/admin/*`），不另起前端项目
- 管理布局与用户端不同（左侧菜单 + 内容区），用 `AdminLayout` 组件
- 管理页面可直接复用用户端 Tailwind 暗色主题

### 4.4 告警改造：用户级通知

现有 `FeishuNotifier` / `EmailNotifier` 用全局配置发送所有通知。

改造后：
- `RuntimeEvent` 数据类新增可选字段 `user_id: str | None`
- `AlertRouter.dispatch()` 中，若 `event.user_id` 存在，从 DB/Redis 查该用户的 `feishu_webhook_url` 和 `email`，动态构造对应的 Notifier 实例发送
- 若 `user_id` 不存在，回退到全局配置（兼容现有逻辑）
- 管理员事件（`control.admin.*`）仍走全局通知配置

## 5. 阶段 B：多级阈值 + 趋势图

### 5.1 多级开清仓 UI

策略编辑页新增梯度配置：

- 现有 `open_tiers_json` / `close_tiers_json` 已建字段
- 前端展示为可编辑表格：每行一个 `spread_bps` + `ratio`，ratio 总和应 ≤ 1.0，新增/删除行
- 空列表时回退到单级阈值（现有体验不变）

Dispatcher 解析逻辑（后续迭代在 `_iter_matching_strategies` 中解析 `open_tiers_json`，本篇不涉及）。

### 5.2 收益走势图

页面：策略详情页 `/strategies/{id}` 新增"收益统计"tab

图表：
- 累计已实现盈亏折线图（横轴时间、纵轴 USDT，从 CLOSED 状态的 `arbitrage_tasks` 聚合）
- 按月/周/日切换粒度
- 标注每笔任务的开仓价差 vs 实际价差（反映滑点）

技术：Recharts（已在 package.json 中）

### 5.3 币种迷你价格走势

排行榜每行左侧新增迷你 Sparkline（微小折线图），数据来源：

- 从 Redis `md:ticker:{exchange}:{symbol}` 聚合最近 N 个 ticker 快照
- 首次加载后 WS 推送 ticker 变化时增量更新

## 6. 文件变更清单

### 阶段 A

| 操作 | 文件 |
|------|------|
| 新建 | `app/api/ws.py` — WebSocket endpoint |
| 新建 | `app/api/positions.py` — 持仓 API |
| 新建 | `app/api/settings.py` — 用户设置 API |
| 修改 | `app/api/__init__.py` — 注册新路由 |
| 修改 | `models.py` — users 加 email + feishu_webhook_url |
| 新建 | `web/src/pages/PositionsPage.tsx` |
| 新建 | `web/src/pages/SettingsPage.tsx` |
| 修改 | `web/src/pages/LeaderboardPage.tsx` — WS 连接 |
| 修改 | `web/src/App.tsx` — 路由 + 导航 |
| 新建 | `web/src/hooks/useWebSocket.ts` |

### 阶段 C

| 操作 | 文件 |
|------|------|
| 修改 | `models.py` — 新增 AdminUser, AdminActionLog |
| 新建 | `app/api/admin/__init__.py` |
| 新建 | `app/api/admin/auth.py` — 管理员登录 |
| 新建 | `app/api/admin/limits.py` — 额度规则 API |
| 新建 | `app/api/admin/switches.py` — 平台开关 API |
| 新建 | `app/api/admin/announcements.py` — 公告管理 API |
| 新建 | `app/api/admin/audit.py` — 审计日志 API |
| 新建 | `app/api/admin/users.py` — 用户管理 API |
| 修改 | `app/api/__init__.py` — 注册 admin 路由 |
| 修改 | `app/api/deps.py` — 新增 get_current_admin 依赖 |
| 修改 | `app/runtime/alerting.py` — user 级通知支持 |
| 修改 | `app/runtime/runtime_events.py` — 新增 user_id 字段 |
| 新建 | `web/src/pages/admin/AdminLoginPage.tsx` |
| 新建 | `web/src/pages/admin/AdminLayout.tsx` |
| 新建 | `web/src/pages/admin/LimitsPage.tsx` |
| 新建 | `web/src/pages/admin/SwitchesPage.tsx` |
| 新建 | `web/src/pages/admin/AnnouncementsPage.tsx` |
| 新建 | `web/src/pages/admin/AuditPage.tsx` |
| 新建 | `web/src/pages/admin/UsersPage.tsx` |
| 修改 | `web/src/App.tsx` — admin 路由 |
| 修改 | `web/src/api.ts` — admin API 客户端 |

### 阶段 B

| 操作 | 文件 |
|------|------|
| 修改 | `web/src/pages/StrategiesPage.tsx` — 多级阈值表 |
| 新建 | `web/src/pages/StrategyDetailPage.tsx` — 收益走势图 |
| 修改 | `web/src/pages/LeaderboardPage.tsx` — Sparkline |
| 新增 | `recharts` 依赖 |

## 7. 实施边界

### 本轮实现（阶段 A + C）

- WebSocket 排行榜实时推送
- 持仓监控页面（读 DB）
- 用户个人设置页（email + feishu + exchange_accounts CRUD）
- admin_users + admin_action_logs 表 + 后端 API
- 管理后台前端 6 页面
- API 端口合并到 FastAPI
- users 表加 email + feishu_webhook_url
- 告警通知支持 user 级（基础）

### 次轮实现（阶段 B）

- 多级开清仓阈值 UI
- 策略收益走势图
- 币种迷你价格走势

## 8. 不涉及

- 策略匹配逻辑改动（Dispatcher 已有的 `_iter_matching_strategies` 不变）
- 新交易所接入
- 交易执行链路改动
- 数据库迁移自动化（需手动执行 ALTER TABLE / CREATE TABLE）

---

*与主设计文档 [2026-05-24-cross-exchange-arbitrage-design.md](2026-05-24-cross-exchange-arbitrage-design.md) 第 17.2 节 "运维后台与策略可视化" 对应。*
*与用户控制台文档 [2026-05-28-user-console-funding-rate-arbitrage-design.md](2026-05-28-user-console-funding-rate-arbitrage-design.md) 第 7.2 节 "次轮增强" 对应。*
