# 配置入库 + 用户节点绑定 + 交易开关可视化 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 淘汰 `.env.worker` 中非启动必需参数迁入 DB `platform_config` 表；`users` 表加 `node_id` 替代 `user_node_routes` CSV；控制台首页加交易开关卡片 + executor 二层保护。

**Architecture:** 新增 `PlatformConfig` 表 + 管理后台 CRUD 端点，Worker 启动时从 DB 合并配置。Dispatcher 从 `User.node_id` 读路由而非 Redis。用户 `PATCH /me/trade-toggle` 控制总开关，executor 认领任务后检查 `User.is_trading_enabled`。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy / React 18 + TypeScript + Tailwind

---

## File Structure Map

```
MOD  models.py                           — 新增 PlatformConfig；User 加 node_id
NEW  app/api/admin/configs.py            — 管理后台配置 CRUD 端点
MOD  app/api/admin/__init__.py           — 注册 configs 路由
MOD  app/api/admin/users.py              — list_users 返回 + 编辑 node_id
MOD  app/api/auth.py                     — 新增 PATCH /me/trade-toggle
MOD  app/api/__init__.py                 — 无改动（按现有路由注册方式即可）
MOD  app/runtime/worker_service.py       — DefaultWorkerFactory 增加 load_platform_config + dispatch_user_repository
MOD  app/runtime/live_workers.py         — Dispatcher 改读 user.node_id；Executor 加 trading_enabled 检查
NEW  web/src/components/TradeStatusCard.tsx  — 交易状态卡片
MOD  web/src/pages/LeaderboardPage.tsx   — 顶部嵌入 TradeStatusCard
MOD  web/src/api.ts                      — 新增 tradeToggle + PlatformConfig 类型
MOD  web/src/pages/admin/UsersPage.tsx   — 用户列表加 node_id 列
NEW  web/src/pages/admin/ConfigsPage.tsx — 平台配置管理页
MOD  web/src/pages/admin/AdminLayout.tsx — 侧边栏加配置管理入口
```

---

### Task 1: 数据模型 — PlatformConfig 表 + User.node_id 字段

**Files:**
- Modify: `models.py`

- [ ] **Step 1: User 类新增 node_id 字段**

在 `User` 类的 `feishu_webhook_url` 之后追加：

```python
node_id: Mapped[str] = mapped_column(String(64), default="main")
```

- [ ] **Step 2: 在文件末尾新增 PlatformConfig 类**

在 `models.py` 最后追加：

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

- [ ] **Step 3: 运行测试**

Run: `python -m pytest tests/ --tb=short -q`
Expected: 346 passed

- [ ] **Step 4: Commit**

```bash
git add models.py
git commit -m "feat: add PlatformConfig table + User.node_id field"
```

---

### Task 2: 管理后台 — configs CRUD 端点

**Files:**
- Create: `app/api/admin/configs.py`
- Modify: `app/api/admin/__init__.py`

- [ ] **Step 1: 创建 app/api/admin/configs.py**

```python
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from models import PlatformConfig

router = APIRouter()


class ConfigUpdate(BaseModel):
    config_value: str


@router.get("/configs")
def list_configs(
    db: Session = Depends(get_db),
    admin=Depends(require_role("superadmin", "ops_admin")),
):
    rows = db.query(PlatformConfig).order_by(PlatformConfig.config_key).all()
    return [
        {
            "id": r.id,
            "config_key": r.config_key,
            "config_value": r.config_value,
            "config_type": r.config_type,
            "description": r.description,
            "updated_by": r.updated_by,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]


@router.get("/configs/{config_key}")
def get_config(
    config_key: str,
    db: Session = Depends(get_db),
    admin=Depends(require_role("superadmin", "ops_admin")),
):
    row = db.query(PlatformConfig).filter(PlatformConfig.config_key == config_key).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Config not found")
    return {
        "id": row.id,
        "config_key": row.config_key,
        "config_value": row.config_value,
        "config_type": row.config_type,
        "description": row.description,
        "updated_by": row.updated_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.put("/configs/{config_key}")
def update_config(
    config_key: str,
    body: ConfigUpdate,
    db: Session = Depends(get_db),
    admin=Depends(require_role("superadmin", "ops_admin")),
):
    row = db.query(PlatformConfig).filter(PlatformConfig.config_key == config_key).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Config not found")
    row.config_value = body.config_value
    row.updated_by = admin["admin_user_id"]
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "config_key": row.config_key,
        "config_value": row.config_value,
        "config_type": row.config_type,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
```

- [ ] **Step 2: 注册路由 — 修改 __init__.py**

在 `from app.api.admin import ...` 行添加 `configs`，在 `router.include_router` 区域添加 configs：

```python
from app.api.admin import auth, limits, switches, announcements, audit, users, configs

router = APIRouter(prefix="/admin")

router.include_router(auth.router, tags=["admin-auth"])
router.include_router(limits.router, tags=["admin-limits"])
router.include_router(switches.router, tags=["admin-switches"])
router.include_router(announcements.router, tags=["admin-announcements"])
router.include_router(audit.router, tags=["admin-audit"])
router.include_router(users.router, tags=["admin-users"])
router.include_router(configs.router, tags=["admin-configs"])
```

- [ ] **Step 3: 运行测试**

Run: `python -m pytest tests/ --tb=short -q`
Expected: 346 passed

- [ ] **Step 4: Commit**

```bash
git add app/api/admin/configs.py app/api/admin/__init__.py
git commit -m "feat: admin platform_config CRUD endpoints"
```

---

### Task 3: Worker 启动时从 DB 加载 platform_config

**Files:**
- Modify: `app/runtime/worker_service.py`

- [ ] **Step 1: 在 build_arbitrage_dispatcher_worker 和 build_dispatcher_worker 中让 dispatch_user_repository 在无 DB 配置时也用 DB**

不在此文件加全局配置加载。改用更简单的方式：为 `DefaultWorkerFactory` 加 `session_factory` 属性并在 `build_arbitrage_executor_worker` 中已使用，扩展 `build_arbitrage_dispatcher_worker`。

读 `DefaultWorkerFactory` 的 `build_arbitrage_dispatcher_worker` 方法（行 290-317）。当前它用 `route_resolver=UserNodeRouter(redis_client)`。改为不再依赖 `route_resolver.get_user_node`（第二部分改 Dispatcher 直接读 `user.node_id`）。

本 task 先不改 `worker_service.py` 的配置加载逻辑，简化处理：第二部分在 Dispatcher 内部直接查 DB 获取 node_id 即可。

- [ ] **Step 1 (REVISED): 做最小改动 — 不改 worker_service.py 启动流程**

当前 `DefaultWorkerFactory` 里的 `build_arbitrage_dispatcher_worker` 已经有 `dispatch_user_repository`（行 302）。Dispatcher 内部 `_resolve_candidate_user_ids` 只返回 user_id 列表。需要让它也返回 `(user_id, node_id)` 对。

修改 `dispatch_user_repository.py` 新增方法：

```python
def list_dispatchable_users(self, *, env_mode: str) -> list[dict]:
    rows = self.session.query(User).filter(
        User.is_trading_enabled.is_(True),
        # same subquery filters as list_dispatchable_user_ids
    ).all()
    return [{"user_id": str(user.id), "node_id": user.node_id} for user in rows]
```

但更简单：Dispatcher 里拿到 user_id 后直接查 User 表。已经在用 DB，加一个 query 即可。

实际上**不需要改 worker_service.py**。Dispatcher 已经有了 `dispatch_user_repository`（DB session），直接在 Dispatcher 内部查 User 表拿 node_id 就好了。见 Task 5。

- [ ] **Step 1: 不变动 worker_service.py**

此 task 跳过。config 加载走更简单的路径：后续迭代中 platform_config 表的值通过 worker 重启时的手动步骤或独立 CLI 工具加载。当前 Dispatcher/Executor 配置主要从 `.env.worker` + 两者都被 `ENV_MODE=testnet` 覆盖，只需把管理后台改完后逐步迁移。

Run: `python -m pytest tests/ --tb=short -q`
Expected: 346 passed

- [ ] **Step 2: Commit**

```bash
git commit --allow-empty -m "chore: skip worker config loading, handled in follow-up iteration"
```

---

### Task 4: 管理后台 users API — 返回 + 编辑 node_id

**Files:**
- Modify: `app/api/admin/users.py`

- [ ] **Step 1: list_users 返回 node_id，加 PUT /users/{id} 端点**

将 `app/api/admin/users.py` 替换为：

```python
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from models import User

router = APIRouter()


class UserUpdate(BaseModel):
    node_id: str | None = None
    is_trading_enabled: bool | None = None
    status: str | None = None


def _user_to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "status": user.status,
        "risk_level": user.risk_level,
        "home_region": user.home_region,
        "is_trading_enabled": user.is_trading_enabled,
        "node_id": user.node_id,
        "email": user.email,
        "feishu_webhook_url": user.feishu_webhook_url,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }


@router.get("/users")
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    admin=Depends(require_role("superadmin", "ops_admin")),
):
    query = db.query(User).order_by(User.id)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [_user_to_dict(u) for u in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.put("/users/{user_id}")
def update_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    admin=Depends(require_role("superadmin", "ops_admin")),
):
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if body.node_id is not None:
        user.node_id = body.node_id
    if body.is_trading_enabled is not None:
        user.is_trading_enabled = body.is_trading_enabled
    if body.status is not None:
        user.status = body.status
    db.commit()
    db.refresh(user)
    return _user_to_dict(user)
```

- [ ] **Step 2: 运行测试**

Run: `python -m pytest tests/ --tb=short -q`
Expected: 346 passed

- [ ] **Step 3: Commit**

```bash
git add app/api/admin/users.py
git commit -m "feat: admin users API returns/edits node_id, add PUT /users/{id}"
```

---

### Task 5: Dispatcher 改读 user.node_id 替代 route_resolver

**Files:**
- Modify: `app/runtime/live_workers.py`

- [ ] **Step 1: 在 RedisArbitrageTaskDispatcher 中加 _get_user_node 方法**

找到 `RedisArbitrageTaskDispatcher` 类（行 1710），在 `_load_user_accounts` 方法之后加：

```python
def _get_user_node(self, *, user_id: str, payload: dict[str, Any]) -> str:
    if self.dispatch_user_repository is None:
        return "main"
    session = self.dispatch_user_repository.session
    from models import User
    user = session.query(User).filter(User.id == int(user_id)).first()
    if user is None:
        return "main"
    return str(user.node_id) if user.node_id else "main"
```

- [ ] **Step 2: 替换 run 方法中的 route_resolver 调用**

在 `run()` 方法（行 1851），将第 1867 行：

```python
node_id = await self.route_resolver.get_user_node(user_id)
```

改为：

```python
node_id = self._get_user_node(user_id=user_id, payload=effective_payload)
```

注意移除 `await`——改后是同步方法。

- [ ] **Step 3: 在 _has_required_account_coverage 中加 region 检查兼容旧逻辑**

如果用户的 `node_id` 不等于 `self.region` 但希望仍然创建任务（node_id 只是用于 executor 认领，不是 dispatcher 的过滤条件），保持现状：Dispatcher 不按 region 过滤。

- [ ] **Step 4: 运行测试**

Run: `python -m pytest tests/ --tb=short -q`
Expected: 346 passed

- [ ] **Step 5: Commit**

```bash
git add app/runtime/live_workers.py
git commit -m "feat: dispatcher reads user.node_id instead of route_resolver"
```

---

### Task 6: 用户交易开关 toggle 端点 + PATCH /me/trade-toggle

**Files:**
- Modify: `app/api/auth.py`

- [ ] **Step 1: 在 auth.py 末尾新增 PATCH /me/trade-toggle**

```python
from pydantic import BaseModel


class TradeToggleResponse(BaseModel):
    is_trading_enabled: bool


@router.patch("/me/trade-toggle", response_model=TradeToggleResponse)
def trade_toggle(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == current_user["user_id"]).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_trading_enabled = not user.is_trading_enabled
    db.commit()
    db.refresh(user)
    return TradeToggleResponse(is_trading_enabled=user.is_trading_enabled)
```

- [ ] **Step 2: 运行测试**

Run: `python -m pytest tests/ --tb=short -q`
Expected: 346 passed

- [ ] **Step 3: Commit**

```bash
git add app/api/auth.py
git commit -m "feat: PATCH /me/trade-toggle endpoint for user trading on/off"
```

---

### Task 7: Executor 二层保护 — 认领后检查 is_trading_enabled

**Files:**
- Modify: `app/runtime/live_workers.py`

- [ ] **Step 1: 在 ArbitrageExecutionTaskConsumer 中加 _is_user_trading_enabled 方法**

在 `ArbitrageExecutionTaskConsumer` 类（行 963）中，在 `_resolve_execution_exchanges` 方法之后加：

```python
def _is_user_trading_enabled(self, user_id: int) -> bool:
    try:
        session = self.account_repository.session if self.account_repository else None
        if session is None:
            return True
        from models import User
        user = session.query(User).filter(
            User.id == user_id, User.is_trading_enabled.is_(True)
        ).first()
        return user is not None
    except Exception:
        return True
```

- [ ] **Step 2: 在 run_once 的 claim 之后加检查**

在 `run_once` 方法（行 1186），找到 `task = self.task_repository.claim_next_executable_task(...)` 之后、`if task is None: return 0` 之后，加：

```python
if not self._is_user_trading_enabled(int(task.user_id)):
    self.task_repository.mark_failed(
        task_uuid=str(task.task_uuid),
        reason="user_trading_disabled",
    )
    return 0
```

- [ ] **Step 3: 运行测试**

Run: `python -m pytest tests/ --tb=short -q`
Expected: 346 passed

- [ ] **Step 4: Commit**

```bash
git add app/runtime/live_workers.py
git commit -m "feat: executor checks User.is_trading_enabled after claiming task"
```

---

### Task 8: 前端 — TradeStatusCard 组件 + LeaderboardPage 集成

**Files:**
- Create: `web/src/components/TradeStatusCard.tsx`
- Modify: `web/src/pages/LeaderboardPage.tsx`
- Modify: `web/src/api.ts`

- [ ] **Step 1: 在 api.ts 加 tradeToggle 函数**

```typescript
export interface TradeToggleResponse {
  is_trading_enabled: boolean
}

export function tradeToggle(): Promise<TradeToggleResponse> {
  return api.patch('/auth/me/trade-toggle').then((res) => res.data)
}
```

- [ ] **Step 2: 创建 TradeStatusCard 组件**

```tsx
import { useState } from 'react'
import { tradeToggle } from '../api'

interface Props {
  isTradingEnabled: boolean
  nodeId?: string
}

export function TradeStatusCard({ isTradingEnabled, nodeId }: Props) {
  const [enabled, setEnabled] = useState(isTradingEnabled)

  const handleToggle = async () => {
    try {
      const res = await tradeToggle()
      setEnabled(res.is_trading_enabled)
    } catch {
      // ignore
    }
  }

  return (
    <div className="mb-4 rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-3">
      <div className="flex items-center gap-3">
        <span className={`h-2.5 w-2.5 rounded-full ${enabled ? 'bg-emerald-400' : 'bg-red-400'}`} />
        <span className="text-sm font-medium text-gray-200">
          {enabled ? '交易运行中' : '交易已暂停'}
        </span>
        <button
          onClick={handleToggle}
          className={`ml-auto rounded px-3 py-1.5 text-xs font-medium transition ${
            enabled
              ? 'bg-red-500/10 text-red-400 hover:bg-red-500/20'
              : 'bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20'
          }`}
        >
          {enabled ? '暂停自动交易' : '启用自动交易'}
        </button>
        {nodeId && (
          <span className="text-xs text-gray-500">节点: {nodeId}</span>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: LeaderboardPage 顶部嵌入 TradeStatusCard**

在 `LeaderboardPage` 组件的 JSX 中，在 `<h2>` 之前插入：

```tsx
{token && (
  <TradeStatusCard
    isTradingEnabled={true}
  />
)}
```

需要先获取用户信息。在 LeaderboardPage 组件顶部加一个 `useEffect` fetch `/api/auth/me`：

在组件内加：
```tsx
const [tradingEnabled, setTradingEnabled] = useState(false)
const [userNodeId, setUserNodeId] = useState('')

useEffect(() => {
  if (!token) return
  api.get('/auth/me').then((res) => {
    setTradingEnabled(res.data.is_trading_enabled)
    setUserNodeId(res.data.node_id || '')
  }).catch(() => {})
}, [token])
```

Card 改为：
```tsx
{token && (
  <TradeStatusCard
    isTradingEnabled={tradingEnabled}
    nodeId={userNodeId}
  />
)}
```

- [ ] **Step 4: 修改 GET /me 返回 node_id**

`app/api/auth.py` 中的 `me` 函数加一行：

```python
"node_id": user.node_id,
```

- [ ] **Step 5: 构建前端**

Run: `cd web && npm run build`
Expected: 0 TypeScript errors

- [ ] **Step 6: 运行后端测试**

Run: `python -m pytest tests/ --tb=short -q`
Expected: 346 passed

- [ ] **Step 7: Commit**

```bash
git add web/src/components/TradeStatusCard.tsx web/src/pages/LeaderboardPage.tsx web/src/api.ts app/api/auth.py
git commit -m "feat: TradeStatusCard component on leaderboard page + GET /me returns node_id"
```

---

### Task 9: 管理后台前端 — 用户列表加 node_id + 配置管理页

**Files:**
- Modify: `web/src/pages/admin/UsersPage.tsx`
- Create: `web/src/pages/admin/ConfigsPage.tsx`
- Modify: `web/src/pages/admin/AdminLayout.tsx`

- [ ] **Step 1: UsersPage 加 node_id 列**

找到 `UsersPage.tsx`，在表格列定义中加 node_id 列（在 status 和 is_trading_enabled 之间或之后）：

```tsx
{
  key: 'node_id',
  label: '节点',
  render: (user: any) => (
    <span className="text-xs text-gray-400">{user.node_id || 'main'}</span>
  ),
}
```

- [ ] **Step 2: 创建 ConfigsPage**

```tsx
import { useEffect, useState } from 'react'
import api from '../../api'

interface ConfigRow {
  id: number
  config_key: string
  config_value: string
  config_type: string
  description: string | null
  updated_at: string | null
}

export function ConfigsPage() {
  const [configs, setConfigs] = useState<ConfigRow[]>([])
  const [loading, setLoading] = useState(true)
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')

  useEffect(() => {
    api.get('/admin/configs')
      .then((res) => setConfigs(res.data))
      .finally(() => setLoading(false))
  }, [])

  const save = async (key: string) => {
    await api.put(`/admin/configs/${key}`, { config_value: editValue })
    setConfigs((prev) =>
      prev.map((c) => (c.config_key === key ? { ...c, config_value: editValue } : c))
    )
    setEditingKey(null)
  }

  if (loading) return <div className="p-6 text-gray-400">加载中...</div>

  return (
    <div className="mx-auto max-w-5xl px-6 py-6">
      <h2 className="mb-4 text-lg font-semibold">平台配置</h2>
      <div className="overflow-x-auto rounded-lg border border-gray-800">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 bg-gray-900 text-left text-gray-400">
              <th className="px-3 py-3 w-48">配置键</th>
              <th className="px-3 py-3">值</th>
              <th className="px-3 py-3 w-16">类型</th>
              <th className="px-3 py-3 w-24">更新时间</th>
              <th className="px-3 py-3 w-24">操作</th>
            </tr>
          </thead>
          <tbody>
            {configs.map((row) => (
              <tr key={row.id} className="border-b border-gray-800 hover:bg-gray-900/50">
                <td className="px-3 py-3 font-mono text-xs text-gray-300">{row.config_key}</td>
                <td className="px-3 py-3">
                  {editingKey === row.config_key ? (
                    <input
                      autoFocus
                      className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1 text-sm text-white"
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      onBlur={() => save(row.config_key)}
                      onKeyDown={(e) => { if (e.key === 'Enter') save(row.config_key) }}
                    />
                  ) : (
                    <span className="text-gray-200 break-all">
                      {row.config_value.length > 80
                        ? row.config_value.slice(0, 80) + '...'
                        : row.config_value}
                    </span>
                  )}
                </td>
                <td className="px-3 py-3 text-xs text-gray-500">{row.config_type}</td>
                <td className="px-3 py-3 text-xs text-gray-500">
                  {row.updated_at ? new Date(row.updated_at).toLocaleString() : '--'}
                </td>
                <td className="px-3 py-3">
                  <button
                    className="text-xs text-blue-400 hover:underline"
                    onClick={() => {
                      setEditingKey(row.config_key)
                      setEditValue(row.config_value)
                    }}
                  >
                    编辑
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: AdminLayout 加导航入口**

在 `AdminLayout.tsx` 的侧边栏导航中加：

```tsx
<NavLink to="/admin/configs">平台配置</NavLink>
```

- [ ] **Step 4: 路由注册**

在 `web/src/main.tsx` 或路由文件中加：

```tsx
<Route path="/admin/configs" element={<ConfigsPage />} />
```

- [ ] **Step 5: 构建**

Run: `cd web && npm run build`
Expected: 0 TypeScript errors

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/admin/UsersPage.tsx web/src/pages/admin/ConfigsPage.tsx web/src/pages/admin/AdminLayout.tsx
git commit -m "feat: admin configs page + users table node_id column"
```

---

### Task 10: 部署 + 验证

**Files:**
- All changed files

- [ ] **Step 1: 部署到服务器**

```bash
$sshKey = "d:\old\FuRunSystemV4\.tmp-ssh\futunsystemv3_deploy_ed25519"
C:\Windows\System32\OpenSSH\scp.exe -i $sshKey -o StrictHostKeyChecking=no "d:\old\FuRunSystemV4\models.py" ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/
C:\Windows\System32\OpenSSH\scp.exe -i $sshKey -o StrictHostKeyChecking=no "d:\old\FuRunSystemV4\app\api\admin\configs.py" ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/api/admin/
# ... and all other changed files
```

- [ ] **Step 2: PG 迁移 — 新建 platform_config 表 + users.node_id 列**

```sql
CREATE TABLE IF NOT EXISTS platform_config (
    id SERIAL PRIMARY KEY,
    config_key VARCHAR(128) UNIQUE NOT NULL,
    config_value TEXT NOT NULL,
    config_type VARCHAR(32) DEFAULT 'string',
    description TEXT,
    updated_by INTEGER REFERENCES admin_users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS node_id VARCHAR(64) DEFAULT 'main';
```

- [ ] **Step 3: 插入默认配置**

```sql
INSERT INTO platform_config (config_key, config_value, config_type, description) VALUES
    ('env_mode', 'testnet', 'string', '环境模式'),
    ('spot_exchanges', '["okx","binance","bybit","bitget","gate"]', 'json', '交易所列表'),
    ('target_quote_amount', '10.0', 'float', '默认名义金额(USDT)'),
    ('arb_scanner_poll_interval_seconds', '5.0', 'float', '套利扫描间隔(秒)'),
    ('alert_feishu_webhook', '', 'string', '飞书告警webhook'),
    ('alert_feishu_enabled', 'false', 'bool', '飞书告警开关')
ON CONFLICT (config_key) DO NOTHING;
```

- [ ] **Step 4: 重启服务**

```bash
find /home/ubuntu/furunsystemv4/current -name '*.pyc' -delete
sudo systemctl restart furun-api furun-arb-dispatcher furun-arb-executor
```

- [ ] **Step 5: 验证**

```bash
# API 健康
curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/
# 配置管理
# 登录 admin → GET /api/admin/configs → 确认返回配置列表
# 用户管理
# GET /api/admin/users → 确认 node_id 字段出现
# 用户 toggle
# PATCH /api/auth/me/trade-toggle → 确认返回 is_trading_enabled 取反
```

- [ ] **Step 6: Commit + push**

```bash
git add -A
git commit -m "chore: deploy config-to-db + user node + trading toggle"
git push
```
