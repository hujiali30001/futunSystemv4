# 用户控制台 + 资金费率套利排行榜 设计文档

## 1. 文档目标

本文档定义在现有"多用户跨交易所现期套利系统"（见 [2026-05-24-cross-exchange-arbitrage-design.md](2026-05-24-cross-exchange-arbitrage-design.md)）之上新增的前端用户控制台能力，包括：

- Web 前端排行榜（类似 pulse-lite.astro-btc.xyz）
- 用户登录/注册（JWT）
- 策略管理（多级开清仓阈值配置）
- 一键启动套利
- FastAPI REST + WebSocket API 层

## 2. 设计原则

- **前后端分离**：React + Vite 独立前端项目，FastAPI 作为 API 网关
- **复用现有核心**：Scanner、Dispatcher、Executor 不做大改，API 层读取现有数据并写入策略配置
- **渐进增强**：首版单级开清仓，多级阈值预留字段，后续迭代加 UI
- **用户隔离**：所有 API 按 JWT `user_id` 过滤数据，后端策略匹配沿用现有的 per-user 逻辑

## 3. 数据模型改动

### 3.1 `users` 表

新增字段：

```python
password_hash: Mapped[str | None]  # bcrypt hash, 可为空兼容旧数据
```

不强制所有用户设密码（管理员导入的用户可以没有密码，仅 API 创建的用户有）。

### 3.2 `strategy_configs` 表

新增字段（预留多级阈值）：

```python
open_tiers_json: Mapped[list] = mapped_column(JSON, default=list)
close_tiers_json: Mapped[list] = mapped_column(JSON, default=list)
```

字段含义：

- `open_tiers_json`: 开仓梯度列表，每项 `{"spread_bps": 100, "ratio": 0.5}`，ratio 为该梯度占总投入金额的比例
- `close_tiers_json`: 清仓梯度列表，同格式
- 空列表时回退到 `open_spread_bps_threshold` / `close_spread_bps_threshold`（兼容现有单级逻辑）

首版前端只暴露单级配置，`open_tiers_json` 写入 `[{"spread_bps": <用户输入>, "ratio": 1.0}]`。

### 3.3 不改动的表

`arbitrage_tasks`、`exchange_accounts` 等保持不变。Dispatcher 的匹配逻辑 `_iter_matching_strategies` 中 spread_bps 判断继续用 `open_spread_bps_threshold` 兜底，后续迭代再解析 `open_tiers_json`。

## 4. API 层设计 (FastAPI)

### 4.1 目录结构

```
app/api/
  __init__.py          # FastAPI app 实例 + CORS
  deps.py              # 依赖注入 (DB session, current_user)
  auth.py              # /api/auth/login, /api/auth/register
  opportunities.py     # /api/opportunities (排行榜)
  strategies.py        # /api/strategies (CRUD)
  tasks.py             # /api/tasks (任务列表/持仓)
```

### 4.2 端点列表

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | /api/auth/register | 否 | 注册，返回 JWT |
| POST | /api/auth/login | 否 | 登录，返回 JWT |
| GET | /api/auth/me | 是 | 当前用户信息 |
| GET | /api/opportunities | 否 | 排行榜数据（分页+排序） |
| GET | /api/strategies | 是 | 我的策略列表 |
| POST | /api/strategies | 是 | 创建策略 |
| PUT | /api/strategies/{id} | 是 | 更新策略 |
| DELETE | /api/strategies/{id} | 是 | 删除策略 |
| PATCH | /api/strategies/{id}/toggle | 是 | 开关策略 |
| GET | /api/tasks | 是 | 我的任务列表 |

### 4.3 认证方案

- 密码用 `bcrypt` 哈希，JWT 用 `python-jose` + HS256
- Token 有效期 24 小时
- 中间件从 `Authorization: Bearer <token>` 提取 `user_id`

### 4.4 排行榜数据来源

排行榜 API 读取现有 Scanner 已产出的数据：

- 从 Redis ZSET `arb:zset:open` / `arb:zset:close` 读取当前价差排行
- 从 Redis Stream `stream:opportunities` 读取完整机会数据
- 补充 funding rate 从 `md:funding:{exchange}:{symbol}` 读取
- API 返回格式：

```json
{
  "symbol": "BTC/USDT",
  "spot_exchange": "binance",
  "derivative_exchange": "bybit",
  "open_spread_bps": 100.5,
  "close_spread_bps": 95.2,
  "funding_rate": 0.0001,
  "funding_rate_pct": 0.01,
  "annualized_yield_pct": 43.8
}
```

## 5. 前端设计

### 5.1 技术栈

- React 18 + TypeScript + Vite
- TanStack Table (表格排序/筛选/分页)
- Recharts (收益走势图，后续迭代)
- shadcn/ui (组件库)
- Tailwind CSS (样式)

### 5.2 页面路由

| 路由 | 页面 | 认证 |
|------|------|------|
| `/login` | 登录 | 否 |
| `/register` | 注册 | 否 |
| `/` | 排行榜（首页） | 否 |
| `/strategies` | 我的策略 | 是 |
| `/strategies/new?symbol=XXX` | 新建策略 | 是 |
| `/tasks` | 任务/持仓 | 是 |

### 5.3 排行榜页面

参考 pulse-lite 的表格设计，排序/筛选列：

- 币种名称、交易所对、资金费率、开仓价差%、清仓价差%
- 已登录用户每行右侧显示【开始套利】按钮

点击【开始套利】→ 弹窗快速配置：
- 币种/交易所（预填，可改）
- 投入 USDT 金额
- 开仓价差阈值 %
- 清仓价差阈值 %
- 保存 → 创建策略并启用

### 5.4 策略管理页面

- 列表展示：名称、币对、开仓阈值、清仓阈值、投入金额、运行状态开关
- 支持编辑、删除
- 切换开关 → PATCH `/api/strategies/{id}/toggle`

## 6. 与现有系统集成

### 6.1 Dispatcher 逻辑

Dispatcher 的 `_iter_matching_strategies` 现已按 `open_spread_bps_threshold` 过滤。用户通过前端设置策略后，策略记录写入 DB，Dispatcher 下次迭代自动匹配（需确认 Dispatcher 是否有策略缓存刷新机制，如无则需加定时重载或 DB 查询实时读）。

### 6.2 Executor 逻辑

Executor 不做改动。策略的 `target_quote_amount` 和 `open_spread_bps_threshold` 已在现有逻辑中使用。多级开清仓的分散下单逻辑在后续迭代中在 Dispatcher 层实现。

### 6.3 启动方式

FastAPI 服务独立启动，不嵌入现有 Worker 进程：

```bash
uvicorn app.api:app --host 0.0.0.0 --port 8000
```

前端开发时 Vite dev server 代理 `/api` 到 FastAPI 端口。

## 7. 实施边界

### 7.1 本轮完成

- User model 加 password_hash
- StrategyConfig 加 open_tiers_json / close_tiers_json
- FastAPI 层：auth + opportunities + strategies + tasks
- React 前端：登录 + 排行榜 + 策略管理（单级阈值）
- 排行榜 API 从 Redis 读取数据

### 7.2 次轮增强

- 多级开清仓 UI + Dispatcher 逻辑
- WebSocket 实时行情推送
- 收益走势图
- 持仓监控 + 盈亏统计
- 策略历史回测

---

*与主设计文档 [2026-05-24-cross-exchange-arbitrage-design.md](2026-05-24-cross-exchange-arbitrage-design.md) 第 17.2 节 "运维后台与策略可视化" 对应。*
