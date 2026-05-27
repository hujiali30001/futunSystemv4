# 前端完善 + 管理员功能 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 FuRunSystemV4 上新增 WebSocket 排行榜实时推送、持仓监控页、用户设置页（5 所 API Key + 通知渠道）、管理后台（独立登录 + 额度/开关/公告 CRUD + 审计日志）+ 端口合并。

**Architecture:** 所有新增能力均在 FastAPI（:8000）内实现。管理 API 从独立 aiohttp 进程（:8787/:8788）合并到 FastAPI `/api/admin/...` 下，共用管理员 JWT 鉴权。前端用 React Router 同项目内扩展路由，管理页面用独立布局组件。

**Tech Stack:** Python 3.10+ / FastAPI / SQLAlchemy / Redis / React 18 + TypeScript + Tailwind CSS

---

## File Structure Map

```
NEW  app/api/ws.py              — WebSocket leaderboard endpoint
NEW  app/api/positions.py       — 持仓 REST API
NEW  app/api/settings.py        — 用户设置 API (email/feishu/exchange_keys)
NEW  app/api/admin/__init__.py  — admin router 聚合
NEW  app/api/admin/auth.py      — admin 登录 + get_current_admin 依赖
NEW  app/api/admin/limits.py    — 额度规则 CRUD API
NEW  app/api/admin/switches.py  — 平台开关 CRUD API
NEW  app/api/admin/announcements.py — 公告 CRUD API
NEW  app/api/admin/audit.py     — 审计日志查询 API
NEW  app/api/admin/users.py     — 用户列表 API
MOD  app/api/__init__.py        — 注册新路由 + admin prefix
MOD  app/api/deps.py            — 新增 get_current_admin 依赖
MOD  models.py                  — users + email/feishu_webhook_url; AdminUser; AdminActionLog
MOD  app/runtime/runtime_events.py — 新增 user_id 字段
MOD  app/runtime/alerting.py    — AlertRouter.dispatch 支持查 user 通知渠道

NEW  web/src/pages/PositionsPage.tsx   — 持仓监控
NEW  web/src/pages/SettingsPage.tsx    — 个人设置 (email/飞书/5所Key)
NEW  web/src/hooks/useWebSocket.ts     — WS hook
NEW  web/src/pages/admin/AdminLoginPage.tsx    — 管理员登录
NEW  web/src/pages/admin/AdminLayout.tsx       — 管理后台侧边栏布局
NEW  web/src/pages/admin/LimitsPage.tsx         — 额度规则管理
NEW  web/src/pages/admin/SwitchesPage.tsx       — 平台开关控制
NEW  web/src/pages/admin/AnnouncementsPage.tsx  — 公告管理
NEW  web/src/pages/admin/AuditPage.tsx          — 审计日志
NEW  web/src/pages/admin/UsersPage.tsx          — 用户管理
MOD  web/src/App.tsx              — 新增路由
MOD  web/src/components/Header.tsx — 新增导航项
MOD  web/src/pages/LeaderboardPage.tsx — WS 连接改造
MOD  web/src/api.ts              — 新增 API 函数 + 类型
```

---

## Task 1: 数据模型扩展

**Files:**
- Modify: `models.py:36-40` (users 加字段)
- Modify: `models.py:176` (文件末尾新增 AdminUser + AdminActionLog)

### Step 1: users 表加 email + feishu_webhook_url

```python
# 在 User 类的 is_trading_enabled 之后插入:
email: Mapped[str | None] = mapped_column(String(255), nullable=True)
feishu_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
```

### Step 2: 新增 AdminUser 表

```python
class AdminUser(TimestampMixin, Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="ops_admin")
```

### Step 3: 新增 AdminActionLog 表

```python
class AdminActionLog(TimestampMixin, Base):
    __tablename__ = "admin_action_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    before_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    admin_user: Mapped["AdminUser"] = relationship()
```

### Step 4: 运行 lint + 类型检查

```bash
python -m pytest tests/ --tb=short -q
```

Expected: all 346 tests pass (no test assertions broken by adding nullable columns).

### Step 5: Commit

```bash
git add models.py tests/
git commit -m "feat: add users.email, users.feishu_webhook_url, admin_users, admin_action_logs tables"
```

---

## Task 2: 管理员鉴权依赖

**Files:**
- Modify: `app/api/deps.py:58` (末尾新增 get_current_admin)

### Step 1: 新增 get_current_admin 依赖

```python
# 在 deps.py 末尾添加

ADMIN_SECRET_KEY = os.getenv("ADMIN_JWT_SECRET_KEY", SECRET_KEY)

def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> dict:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, ADMIN_SECRET_KEY, algorithms=[ALGORITHM])
        admin_id: int = payload.get("admin_id")
        if admin_id is None:
            raise HTTPException(status_code=401, detail="Invalid admin token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid admin token")
    return {
        "admin_id": admin_id,
        "username": payload.get("username", ""),
        "role": payload.get("role", ""),
    }

def require_role(*roles: str):
    def checker(admin: dict = Depends(get_current_admin)) -> dict:
        if admin["role"] not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return admin
    return checker
```

### Step 2: Run tests

```bash
python -m pytest tests/ --tb=short -q
```

### Step 3: Commit

```bash
git add app/api/deps.py
git commit -m "feat: add get_current_admin + require_role auth dependencies"
```

---

## Task 3: 管理员登录 API

**Files:**
- Create: `app/api/admin/__init__.py`
- Create: `app/api/admin/auth.py`

### Step 1: Create admin package init

`app/api/admin/__init__.py`:
```python
from fastapi import APIRouter

router = APIRouter()
```

### Step 2: Create admin auth

`app/api/admin/auth.py`:
```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import jwt
from datetime import datetime, timedelta
import os

from app.api.deps import get_db, get_current_admin, require_role
from models import AdminUser

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
SECRET = os.getenv("ADMIN_JWT_SECRET_KEY", os.getenv("JWT_SECRET_KEY", "furun-dev-secret"))


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


@router.post("/login", response_model=AdminLoginResponse)
def admin_login(body: AdminLoginRequest, db: Session = Depends(get_db)):
    admin = db.query(AdminUser).filter(AdminUser.username == body.username).first()
    if not admin or not pwd_context.verify(body.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    payload = {
        "admin_id": admin.id,
        "username": admin.username,
        "role": admin.role,
        "exp": datetime.utcnow() + timedelta(hours=24),
    }
    token = jwt.encode(payload, SECRET, algorithm="HS256")
    return {"access_token": token, "role": admin.role}


@router.get("/me")
def admin_me(admin: dict = Depends(get_current_admin)):
    return admin
```

### Step 3: Commit

```bash
git add app/api/admin/
git commit -m "feat: admin login API + get_current_admin guard"
```

---

## Task 4: 管理员 CRUD API（合并 :8787/:8788）

**Files:**
- Create: `app/api/admin/limits.py`
- Create: `app/api/admin/switches.py`
- Create: `app/api/admin/announcements.py`
- Create: `app/api/admin/audit.py`
- Create: `app/api/admin/users.py`
- Modify: `app/api/__init__.py:28-30`

### Step 1: 额度规则 API

`app/api/admin/limits.py` — 从 `control_admin_service.py` 移植 CRUD 逻辑，改为 FastAPI + SQLAlchemy：

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.api.deps import get_db, get_current_admin, require_role
from models import RiskLimitRule, AdminActionLog

router = APIRouter()


class LimitCreate(BaseModel):
    scope_type: str
    scope_id: str
    limit_type: str
    limit_value: float
    enabled: bool = True
    priority: int = 100
    symbol: str | None = None
    exchange: str | None = None


class LimitUpdate(BaseModel):
    limit_value: float | None = None
    enabled: bool | None = None
    priority: int | None = None


@router.get("/limits")
def list_limits(db: Session = Depends(get_db), admin=Depends(get_current_admin)):
    return {"limits": db.query(RiskLimitRule).all()}


@router.post("/limits")
def create_limit(body: LimitCreate, db: Session = Depends(get_db),
                 admin=Depends(require_role("superadmin", "risk_admin"))):
    rule = RiskLimitRule(
        scope_type=body.scope_type, scope_id=body.scope_id,
        limit_type=body.limit_type, limit_value=body.limit_value,
        enabled=body.enabled, priority=body.priority,
        symbol=body.symbol, exchange=body.exchange,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    _log_action(db, admin["admin_id"], "create", "limit_rule", str(rule.id), None,
                {"scope_type": body.scope_type, "limit_value": body.limit_value})
    return {"ok": True, "limit": rule}


@router.put("/limits/{rule_id}")
def update_limit(rule_id: int, body: LimitUpdate, db: Session = Depends(get_db),
                 admin=Depends(require_role("superadmin", "risk_admin"))):
    rule = db.query(RiskLimitRule).filter(RiskLimitRule.id == rule_id).first()
    if not rule:
        raise HTTPException(404, "not found")
    before = {"limit_value": rule.limit_value, "enabled": rule.enabled}
    if body.limit_value is not None:
        rule.limit_value = body.limit_value
    if body.enabled is not None:
        rule.enabled = body.enabled
    if body.priority is not None:
        rule.priority = body.priority
    db.commit()
    _log_action(db, admin["admin_id"], "update", "limit_rule", str(rule_id), before,
                {"limit_value": rule.limit_value, "enabled": rule.enabled})
    return {"ok": True, "limit": rule}


@router.delete("/limits/{rule_id}")
def delete_limit(rule_id: int, db: Session = Depends(get_db),
                 admin=Depends(require_role("superadmin", "risk_admin"))):
    rule = db.query(RiskLimitRule).filter(RiskLimitRule.id == rule_id).first()
    if not rule:
        raise HTTPException(404, "not found")
    db.delete(rule)
    db.commit()
    _log_action(db, admin["admin_id"], "delete", "limit_rule", str(rule_id), None, None)
    return {"ok": True}


def _log_action(db: Session, admin_id: int, action_type: str,
                target_type: str, target_id: str | None,
                before: dict | None, after: dict | None):
    db.add(AdminActionLog(
        admin_user_id=admin_id, action_type=action_type,
        target_type=target_type, target_id=target_id,
        before_json=before, after_json=after,
    ))
    db.commit()
```

### Step 2: 平台开关 API

`app/api/admin/switches.py` — 操作 Redis control switches，结构同 control_admin_service 但用 FastAPI：

```python
from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from pydantic import BaseModel

from app.api.deps import get_redis, get_current_admin, require_role
from app.admin.control_store import ControlPlaneStore

router = APIRouter()


class SwitchUpdate(BaseModel):
    enabled: bool


@router.get("/switches")
async def list_switches(redis=Depends(get_redis), admin=Depends(get_current_admin)):
    store = ControlPlaneStore(redis)
    return {"switches": [s.__dict__ for s in await store.list_switches()]}


@router.put("/switches/{switch_id:path}")
async def put_switch(switch_id: str, body: SwitchUpdate, redis=Depends(get_redis),
                     admin=Depends(require_role("superadmin", "risk_admin"))):
    parts = switch_id.split(":", 2)
    if len(parts) != 3:
        raise HTTPException(400, "switch_id must be key:scope_type:scope_id")
    from app.admin.control_store import PlatformSwitchRecord
    store = ControlPlaneStore(redis)
    record = PlatformSwitchRecord(
        switch_key=parts[0], scope_type=parts[1], scope_id=parts[2],
        enabled=body.enabled,
    )
    await store.put_switch(record)
    return {"ok": True}


@router.delete("/switches/{switch_id:path}")
async def delete_switch(switch_id: str, redis=Depends(get_redis),
                        admin=Depends(require_role("superadmin", "risk_admin"))):
    parts = switch_id.split(":", 2)
    if len(parts) != 3:
        raise HTTPException(400, "invalid")
    store = ControlPlaneStore(redis)
    await store.delete_switch(parts[0], scope_type=parts[1], scope_id=parts[2])
    return {"ok": True}
```

### Step 3: 公告 API

`app/api/admin/announcements.py` — CRUD 操作 Redis + DB：

```python
from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.api.deps import get_db, get_redis, get_current_admin, require_role
from app.admin.control_store import ControlPlaneStore, AnnouncementRecord
from models import Announcement, AdminActionLog

router = APIRouter()


class AnnouncementCreate(BaseModel):
    title: str
    content: str
    priority: int = 100
    is_pinned: bool = False
    audience_type: str = "all"
    channels: list[str] = []
    status: str = "published"


@router.get("/announcements")
def list_announcements(db: Session = Depends(get_db), admin=Depends(get_current_admin)):
    return {"announcements": db.query(Announcement).all()}


@router.post("/announcements")
def create_announcement(body: AnnouncementCreate, db: Session = Depends(get_db),
                        admin=Depends(require_role("superadmin", "ops_admin"))):
    a = Announcement(
        title=body.title, content=body.content, priority=body.priority,
        is_pinned=body.is_pinned, audience_type=body.audience_type,
        channels_json=body.channels, status=body.status,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    _log(db, admin["admin_id"], "create", "announcement", str(a.id), None,
         {"title": body.title})
    return {"ok": True, "announcement": a}


@router.put("/announcements/{announcement_id}")
def update_announcement(announcement_id: int, body: AnnouncementCreate,
                        db: Session = Depends(get_db),
                        admin=Depends(require_role("superadmin", "ops_admin"))):
    a = db.query(Announcement).filter(Announcement.id == announcement_id).first()
    if not a:
        raise HTTPException(404, "not found")
    before = {"title": a.title, "status": a.status}
    a.title = body.title
    a.content = body.content
    a.priority = body.priority
    a.is_pinned = body.is_pinned
    a.audience_type = body.audience_type
    a.channels_json = body.channels
    a.status = body.status
    db.commit()
    _log(db, admin["admin_id"], "update", "announcement", str(announcement_id),
         before, {"title": a.title, "status": a.status})


@router.delete("/announcements/{announcement_id}")
def delete_announcement(announcement_id: int, db: Session = Depends(get_db),
                        admin=Depends(require_role("superadmin", "ops_admin"))):
    a = db.query(Announcement).filter(Announcement.id == announcement_id).first()
    if not a:
        raise HTTPException(404, "not found")
    db.delete(a)
    db.commit()
    _log(db, admin["admin_id"], "delete", "announcement", str(announcement_id), None, None)


def _log(db, admin_id, action, target_type, target_id, before, after):
    db.add(AdminActionLog(admin_user_id=admin_id, action_type=action,
                          target_type=target_type, target_id=target_id,
                          before_json=before, after_json=after))
    db.commit()
```

### Step 4: 审计日志 + 用户列表 API

`app/api/admin/audit.py`:
```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.api.deps import get_db, get_current_admin, require_role
from models import AdminActionLog, AdminUser

router = APIRouter()


@router.get("/audit")
def list_audit(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               db: Session = Depends(get_db),
               admin=Depends(require_role("superadmin"))):
    total = db.query(AdminActionLog).count()
    items = db.query(AdminActionLog).order_by(
        AdminActionLog.created_at.desc()
    ).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}
```

`app/api/admin/users.py`:
```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.api.deps import get_db, get_current_admin, require_role
from models import User

router = APIRouter()


@router.get("/users")
def list_users(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               db: Session = Depends(get_db),
               admin=Depends(require_role("superadmin", "ops_admin"))):
    total = db.query(User).count()
    items = db.query(User).order_by(User.id).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}
```

### Step 5: 更新 admin __init__ 聚合所有子路由 + 注册到主 app

`app/api/admin/__init__.py`:
```python
from fastapi import APIRouter
from app.api.admin import auth, limits, switches, announcements, audit, users

router = APIRouter(prefix="/admin")
router.include_router(auth.router, tags=["admin-auth"])
router.include_router(limits.router, tags=["admin-limits"])
router.include_router(switches.router, tags=["admin-switches"])
router.include_router(announcements.router, tags=["admin-announcements"])
router.include_router(audit.router, tags=["admin-audit"])
router.include_router(users.router, tags=["admin-users"])
```

在 `app/api/__init__.py` 第 28 行后加入:
```python
from app.api import admin
app.include_router(admin.router, prefix="/api")
```

### Step 6: Run tests

```bash
python -m pytest tests/ --tb=short -q
```

### Step 7: Commit

```bash
git add app/api/admin/ app/api/__init__.py
git commit -m "feat: admin CRUD APIs (limits/switches/announcements/audit/users) merged to FastAPI"
```

---

## Task 5: RuntimeEvent + alerting user 级支持

**Files:**
- Modify: `app/runtime/runtime_events.py:6-19`
- Modify: `app/runtime/alerting.py:218-260`

### Step 1: RuntimeEvent 加 user_id

```python
@dataclass(slots=True)
class RuntimeEvent:
    event_type: str
    level: str
    service: str
    message: str
    region: str | None = None
    symbol: str | None = None
    exchange: str | None = None
    exchanges: list[str] | None = None
    user_id: str | None = None       # NEW
    payload: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "level": self.level,
            "service": self.service,
            "region": self.region,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "exchanges": self.exchanges,
            "user_id": self.user_id,     # NEW
            "message": self.message,
            "payload": self.payload,
            "created_at": self.created_at,
        }
```

### Step 2: alerting.py — FeishuNotifier 支持动态 webhook

```python
# 在 FeishuNotifier 类中新增静态工厂方法
@staticmethod
async def for_user(user_feishu_url: str) -> "FeishuNotifier":
    return FeishuNotifier(webhook_url=user_feishu_url)
```

`AlertRouter` — 新增 `user_notifier_cache`:
```python
@dataclass(slots=True)
class AlertRouter:
    # ... existing fields ...
    _user_cache: dict[str, tuple[str, str]] = field(default_factory=dict)

    async def dispatch(self, event: RuntimeEvent) -> None:
        self.logger.record(event)
        if not self.alerts_enabled:
            return
        if event.user_id:
            # 用户级通知：查 DB → 动态构造 notifier
            await self._send_user_feishu(event)
            await self._send_user_email(event)
            return
        if event.level == "CRITICAL":
            await self._send_feishu(event)
            await self._send_email(event)
            return
        # ...rest unchanged...

    async def _send_user_feishu(self, event: RuntimeEvent) -> None:
        from models import User
        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == int(event.user_id)).first()
            if user and user.feishu_webhook_url:
                notifier = FeishuNotifier.for_user(user.feishu_webhook_url)
                await notifier.send(event)
        finally:
            db.close()
```

### Step 3: Commit

```bash
git add app/runtime/runtime_events.py app/runtime/alerting.py
git commit -m "feat: per-user alert notifications via users.email + feishu_webhook_url"
```

---

## Task 6: WebSocket 排行榜端点

**Files:**
- Create: `app/api/ws.py`

### Step 1: Create WS endpoint

```python
import json
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from app.api.deps import get_redis
from app.api.opportunities import _format_volume, _funding_interval_h

router = APIRouter()


@router.websocket("/leaderboard")
async def leaderboard_ws(
    ws: WebSocket,
    direction: str = "spot_futures",
):
    await ws.accept()
    redis = await get_redis()

    async def push_snapshot():
        raw = await redis.xrevrange("stream:opportunities", "+", "-", count=12000)
        latest: dict = {}
        for msg_id, fields in raw:
            symbol = fields.get("symbol", "")
            spot = fields.get("spot_exchange", "")
            deriv = fields.get("derivative_exchange", "")
            otype = fields.get("opportunity_type", "OPEN")
            key = (symbol, spot, deriv, otype)
            if key not in latest:
                try:
                    latest[key] = {
                        "symbol": symbol,
                        "spot_exchange": spot,
                        "derivative_exchange": deriv,
                        "open_spread_bps": float(fields.get("open_spread_bps", 0)),
                        "close_spread_bps": float(fields.get("close_spread_bps", 0)),
                        "funding_rate": float(fields.get("funding_rate", 0)),
                    }
                except (ValueError, TypeError):
                    pass

        paired: dict = {}
        for (symbol, spot, deriv, otype), entry in latest.items():
            pk = (symbol, spot, deriv)
            if pk not in paired:
                paired[pk] = {}
            paired[pk][otype] = entry

        rows = []
        for (symbol, spot, deriv), entry_by_type in paired.items():
            open_entry = entry_by_type.get("OPEN")
            close_entry = entry_by_type.get("CLOSE", open_entry)
            if open_entry is None:
                continue
            open_bps = open_entry["open_spread_bps"]
            close_bps = close_entry["close_spread_bps"]
            open_pct = round(open_bps / 100, 2)
            close_pct = round(close_bps / 100, 2)
            if abs(open_pct) >= 500:
                continue

            fr = open_entry["funding_rate"]
            fr_pct = fr * 100
            fr_display = (
                f"{fr_pct:+.4f}%/h/{_funding_interval_h(fr_pct)}"
                if abs(fr_pct) < 1
                else f"{fr_pct:+.2f}%/h/{_funding_interval_h(fr_pct)}"
            )
            fr_label = "收" if fr > 0 else ("付" if fr < 0 else "")

            if direction == "futures_spot":
                open_pct, close_pct = close_pct, open_pct
                fr = -fr
                spot, deriv = deriv, spot

            rows.append({
                "symbol": symbol.replace("/USDT", ""),
                "full_symbol": symbol,
                "spot_exchange": spot,
                "derivative_exchange": deriv,
                "open_spread_pct": open_pct,
                "close_spread_pct": close_pct,
                "funding_rate_display": f"{fr_display} {fr_label}" if fr_label else fr_display,
                "sort_value": round(open_pct, 2),
            })

        rows.sort(key=lambda r: r["sort_value"], reverse=True)
        top100 = rows[:100]

        # enrich ticker data
        keys = []
        for r in top100:
            keys.append(f"md:ticker:{r['spot_exchange']}:{r['full_symbol']}")
            keys.append(f"md:ticker:{r['derivative_exchange']}:swap:{r['full_symbol']}")
        vals = await redis.mget(keys) if keys else []

        spot_prices: dict = {}
        deriv_prices: dict = {}
        spot_vols: dict = {}
        deriv_vols: dict = {}
        for i, r in enumerate(top100):
            sv = vals[i * 2] if i * 2 < len(vals) else None
            dv = vals[i * 2 + 1] if i * 2 + 1 < len(vals) else None
            for val, is_spot in [(sv, True), (dv, False)]:
                if val is None:
                    continue
                parts = str(val).split("|")
                if len(parts) < 2:
                    continue
                try:
                    vol = float(parts[0])
                    price = float(parts[1])
                except (ValueError, TypeError):
                    continue
                pk = (r["full_symbol"], r["spot_exchange"], r["derivative_exchange"])
                if is_spot:
                    spot_prices[pk] = f"{price:.5f}"
                    spot_vols[pk] = _format_volume(vol)
                else:
                    deriv_prices[pk] = f"{price:.5f}"
                    deriv_vols[pk] = _format_volume(vol)

        for r in top100:
            pk = (r["full_symbol"], r["spot_exchange"], r["derivative_exchange"])
            r["spot_price"] = spot_prices.get(pk, "")
            r["deriv_price"] = deriv_prices.get(pk, "")
            r["spot_volume"] = spot_vols.get(pk, "--")
            r["deriv_volume"] = deriv_vols.get(pk, "--")

        await ws.send_json({"type": "snapshot", "items": top100})

    try:
        await push_snapshot()
        while True:
            await asyncio.sleep(5)
            await push_snapshot()
    except (WebSocketDisconnect, RuntimeError):
        pass
```

### Step 2: Register in `app/api/__init__.py`

```python
from app.api import ws
app.include_router(ws.router, prefix="/api/ws", tags=["ws"])
```

### Step 3: Commit

```bash
git add app/api/ws.py app/api/__init__.py
git commit -m "feat: WebSocket leaderboard endpoint (WS /api/ws/leaderboard)"
```

---

## Task 7: 持仓 + 设置 API

**Files:**
- Create: `app/api/positions.py`
- Create: `app/api/settings.py`
- Modify: `app/api/__init__.py`

### Step 1: 持仓 API

`app/api/positions.py`:
```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.api.deps import get_db, get_current_user
from models import ArbitrageTask
from pydantic import BaseModel

router = APIRouter()


@router.get("/positions")
def list_positions(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    total = db.query(ArbitrageTask).filter(
        ArbitrageTask.user_id == user["user_id"],
        ArbitrageTask.status.in_(["HOLDING", "OPEN_HEDGED"]),
    ).count()
    items = db.query(ArbitrageTask).filter(
        ArbitrageTask.user_id == user["user_id"],
        ArbitrageTask.status.in_(["HOLDING", "OPEN_HEDGED"]),
    ).order_by(ArbitrageTask.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}
```

### Step 2: 设置 API

`app/api/settings.py`:
```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.api.deps import get_db, get_current_user
from models import User, ExchangeAccount

router = APIRouter()


class ProfileUpdate(BaseModel):
    email: str | None = None
    feishu_webhook_url: str | None = None


class ExchangeAccountCreate(BaseModel):
    exchange: str
    api_key: str
    secret: str
    passphrase: str | None = None
    account_label: str = "default"
    env_mode: str = "testnet"


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    u = db.query(User).filter(User.id == user["user_id"]).first()
    accounts = db.query(ExchangeAccount).filter(
        ExchangeAccount.user_id == user["user_id"]
    ).all()
    return {
        "email": u.email,
        "feishu_webhook_url": u.feishu_webhook_url,
        "exchange_accounts": accounts,
    }


@router.put("/settings/profile")
def update_profile(body: ProfileUpdate, db: Session = Depends(get_db),
                   user: dict = Depends(get_current_user)):
    u = db.query(User).filter(User.id == user["user_id"]).first()
    if body.email is not None:
        u.email = body.email
    if body.feishu_webhook_url is not None:
        u.feishu_webhook_url = body.feishu_webhook_url
    db.commit()
    return {"ok": True}


@router.post("/settings/exchange")
def create_exchange_account(body: ExchangeAccountCreate,
                            db: Session = Depends(get_db),
                            user: dict = Depends(get_current_user)):
    acct = ExchangeAccount(
        user_id=user["user_id"],
        exchange=body.exchange,
        account_label=body.account_label,
        env_mode=body.env_mode,
        api_key_ciphertext=body.api_key,
        secret_ciphertext=body.secret,
        passphrase_ciphertext=body.passphrase,
    )
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return {"ok": True, "account": acct}


@router.put("/settings/exchange/{account_id}")
def update_exchange_account(account_id: int, body: ExchangeAccountCreate,
                            db: Session = Depends(get_db),
                            user: dict = Depends(get_current_user)):
    acct = db.query(ExchangeAccount).filter(
        ExchangeAccount.id == account_id,
        ExchangeAccount.user_id == user["user_id"],
    ).first()
    if not acct:
        raise HTTPException(404, "not found")
    acct.api_key_ciphertext = body.api_key
    acct.secret_ciphertext = body.secret
    acct.passphrase_ciphertext = body.passphrase
    acct.exchange = body.exchange
    db.commit()
    return {"ok": True}


@router.delete("/settings/exchange/{account_id}")
def delete_exchange_account(account_id: int, db: Session = Depends(get_db),
                            user: dict = Depends(get_current_user)):
    acct = db.query(ExchangeAccount).filter(
        ExchangeAccount.id == account_id,
        ExchangeAccount.user_id == user["user_id"],
    ).first()
    if not acct:
        raise HTTPException(404, "not found")
    db.delete(acct)
    db.commit()
    return {"ok": True}
```

### Step 3: Register routes

In `app/api/__init__.py`:
```python
from app.api import positions, settings
app.include_router(positions.router, prefix="/api", tags=["positions"])
app.include_router(settings.router, prefix="/api", tags=["settings"])
```

### Step 4: Commit

```bash
git add app/api/positions.py app/api/settings.py app/api/__init__.py
git commit -m "feat: positions + settings REST APIs"
```

---

## Task 8: 前端 API 客户端 + 类型扩展

**Files:**
- Modify: `web/src/api.ts`

### Step 1: Add new types + functions

```typescript
// 在 leaderboard 相关类型之后新增:

export interface PositionItem {
  id: number
  task_uuid: string
  task_type: string
  symbol: string
  spot_exchange: string
  derivative_exchange: string
  target_notional: number
  expected_spread_bps: number
  expected_funding_bps: number
  status: string
  execution_status: string | null
  auto_recovery_status: string
  failure_reason: string | null
  created_at: string | null
  finished_at: string | null
}

export interface UserSettings {
  email: string | null
  feishu_webhook_url: string | null
  exchange_accounts: ExchangeAccount[]
}

export interface ExchangeAccount {
  id: number
  exchange: string
  account_label: string
  env_mode: string
  is_enabled: boolean
}

export interface AdminLoginResponse {
  access_token: string
  token_type: string
  role: string
}

export interface RiskLimitRule {
  id: number
  scope_type: string
  scope_id: string
  limit_type: string
  limit_value: number
  enabled: boolean
  priority: number
  symbol: string | null
  exchange: string | null
}

export interface PlatformSwitch {
  switch_key: string
  scope_type: string
  scope_id: string
  enabled: boolean
}

export interface AuditLogItem {
  id: number
  admin_user_id: number
  action_type: string
  target_type: string
  target_id: string
  before_json: any
  after_json: any
  reason: string | null
  created_at: string
}

// --- New API functions ---

export async function getPositions(params: { page?: number; page_size?: number }) {
  const { data } = await api.get<{ items: PositionItem[]; total: number }>('/positions', { params })
  return data
}

export async function getSettings() {
  const { data } = await api.get<UserSettings>('/settings')
  return data
}

export async function updateProfile(body: { email?: string | null; feishu_webhook_url?: string | null }) {
  await api.put('/settings/profile', body)
}

export async function createExchangeAccount(body: {
  exchange: string; api_key: string; secret: string; passphrase?: string; account_label?: string; env_mode?: string
}) {
  await api.post('/settings/exchange', body)
}

export async function updateExchangeAccount(id: number, body: {
  exchange: string; api_key: string; secret: string; passphrase?: string
}) {
  await api.put(`/settings/exchange/${id}`, body)
}

export async function deleteExchangeAccount(id: number) {
  await api.delete(`/settings/exchange/${id}`)
}
```

### Step 2: Add admin API client (separate axios instance)

```typescript
const adminApi = axios.create({ baseURL: '/api/admin' })

adminApi.interceptors.request.use((config) => {
  const token = localStorage.getItem('admin_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

adminApi.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401 && window.location.pathname.startsWith('/admin') && window.location.pathname !== '/admin/login') {
      localStorage.removeItem('admin_token')
      window.location.href = '/admin/login'
    }
    return Promise.reject(err)
  },
)

export async function adminLogin(username: string, password: string) {
  const { data } = await api.post<AdminLoginResponse>('/admin/login', { username, password })
  return data
}

export async function getAdminMe() {
  const { data } = await api.get('/admin/me')
  return data
}

export async function getLimits() {
  const { data } = await api.get<{ limits: RiskLimitRule[] }>('/admin/limits')
  return data
}

export async function createLimit(body: {
  scope_type: string; scope_id: string; limit_type: string; limit_value: number
}) {
  const { data } = await api.post('/admin/limits', body)
  return data
}

export async function updateLimit(id: number, body: { limit_value?: number; enabled?: boolean; priority?: number }) {
  const { data } = await api.put(`/admin/limits/${id}`, body)
  return data
}

export async function deleteLimit(id: number) {
  await api.delete(`/admin/limits/${id}`)
}

export async function getSwitches() {
  const { data } = await api.get<{ switches: PlatformSwitch[] }>('/admin/switches')
  return data
}

export async function putSwitch(switchId: string, enabled: boolean) {
  await api.put(`/admin/switches/${encodeURIComponent(switchId)}`, { enabled })
}

export async function deleteSwitch(switchId: string) {
  await api.delete(`/admin/switches/${encodeURIComponent(switchId)}`)
}

// ... similar for announcements, audit, users
```

### Step 3: Commit

```bash
git add web/src/api.ts
git commit -m "feat: add positions/settings/admin API client functions + types"
```

---

## Task 9: WebSocket hook

**Files:**
- Create: `web/src/hooks/useWebSocket.ts`

```typescript
import { useEffect, useRef, useCallback } from 'react'
import type { LeaderboardRow } from '../api'

export function useWebSocket(
  direction: string,
  onData: (items: LeaderboardRow[]) => void,
  enabled: boolean,
) {
  const wsRef = useRef<WebSocket | null>(null)

  const connect = useCallback(() => {
    if (!enabled) return
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${location.host}/api/ws/leaderboard?direction=${direction}`)
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'snapshot') {
          onData(msg.items)
        }
      } catch {}
    }
    wsRef.current = ws
  }, [direction, onData, enabled])

  useEffect(() => {
    connect()
    return () => {
      wsRef.current?.close()
    }
  }, [connect])

  return { reconnect: connect }
}
```

### Step 2: Commit

```bash
git add web/src/hooks/useWebSocket.ts
git commit -m "feat: useWebSocket hook for leaderboard real-time updates"
```

---

## Task 10: 前端持仓页 + 设置页

**Files:**
- Create: `web/src/pages/PositionsPage.tsx`
- Create: `web/src/pages/SettingsPage.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/Header.tsx`

### Step 1: PositionsPage.tsx

```typescript
import { useEffect, useState } from 'react'
import { getPositions, type PositionItem } from '../api'

export function PositionsPage() {
  const [items, setItems] = useState<PositionItem[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getPositions({ page_size: 100 }).then((res) => {
      setItems(res.items)
    }).finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="p-6 text-gray-500">加载中...</div>
  if (items.length === 0) return <div className="p-6 text-gray-500">暂无持仓</div>

  return (
    <div className="mx-auto max-w-7xl px-4 py-6">
      <h2 className="mb-4 text-lg font-semibold">我的持仓</h2>
      <div className="overflow-x-auto rounded-lg border border-gray-800">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 bg-gray-900 text-left text-gray-400">
              <th className="px-3 py-3">币种</th>
              <th className="px-3 py-3">交易所</th>
              <th className="px-3 py-3">方向</th>
              <th className="px-3 py-3 text-right">名义金额</th>
              <th className="px-3 py-3 text-right">开仓价差</th>
              <th className="px-3 py-3">状态</th>
              <th className="px-3 py-3">恢复状态</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.task_uuid} className="border-b border-gray-800 hover:bg-gray-900/50">
                <td className="px-3 py-3 font-medium">{item.symbol}</td>
                <td className="px-3 py-3 text-gray-400">{item.spot_exchange} / {item.derivative_exchange}</td>
                <td className="px-3 py-3">{item.task_type === 'open' ? '开仓' : '平仓'}</td>
                <td className="px-3 py-3 text-right">{item.target_notional.toFixed(0)} USDT</td>
                <td className="px-3 py-3 text-right">{(item.expected_spread_bps / 100).toFixed(2)}%</td>
                <td className="px-3 py-3">{item.status}</td>
                <td className="px-3 py-3">{item.auto_recovery_status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

### Step 2: SettingsPage.tsx

```typescript
import { useEffect, useState } from 'react'
import { getSettings, updateProfile, createExchangeAccount, updateExchangeAccount, deleteExchangeAccount, type UserSettings } from '../api'

const EXCHANGES = ['binance', 'okx', 'bybit', 'gate', 'bitget']

export function SettingsPage() {
  const [settings, setSettings] = useState<UserSettings | null>(null)
  const [email, setEmail] = useState('')
  const [feishu, setFeishu] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    getSettings().then((s) => {
      setSettings(s)
      setEmail(s.email || '')
      setFeishu(s.feishu_webhook_url || '')
    })
  }, [])

  const saveProfile = async () => {
    setSaving(true)
    await updateProfile({ email: email || null, feishu_webhook_url: feishu || null })
    setSaving(false)
  }

  if (!settings) return <div className="p-6 text-gray-500">加载中...</div>

  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <h2 className="mb-6 text-lg font-semibold">个人设置</h2>

      <div className="mb-6 rounded-lg border border-gray-800 p-4">
        <h3 className="mb-3 text-sm text-gray-400">通知渠道</h3>
        <div className="mb-3">
          <label className="mb-1 block text-xs text-gray-500">邮箱</label>
          <input value={email} onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-2 text-gray-300" placeholder="user@example.com" />
        </div>
        <div className="mb-3">
          <label className="mb-1 block text-xs text-gray-500">飞书 Webhook URL</label>
          <input value={feishu} onChange={(e) => setFeishu(e.target.value)}
            className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-2 text-gray-300" placeholder="https://open.feishu.cn/..." />
        </div>
        <button onClick={saveProfile} disabled={saving}
          className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium hover:bg-emerald-500 disabled:opacity-50">
          {saving ? '保存中...' : '保存'}
        </button>
      </div>

      <div className="rounded-lg border border-gray-800 p-4">
        <h3 className="mb-3 text-sm text-gray-400">交易所 API</h3>
        {EXCHANGES.map((ex) => {
          const acct = settings.exchange_accounts.find((a) => a.exchange === ex)
          return (
            <div key={ex} className="mb-2 flex items-center justify-between border-b border-gray-800 pb-2">
              <span className="font-mono text-sm text-gray-300">{ex}</span>
              <span className="text-xs text-gray-500">
                {acct ? `已配置 (${acct.account_label})` : '未配置'}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
```

### Step 3: App.tsx 新增路由

```typescript
import { PositionsPage } from './pages/PositionsPage'
import { SettingsPage } from './pages/SettingsPage'

// in <Routes>:
<Route path="/positions" element={<PositionsPage />} />
<Route path="/settings" element={<SettingsPage />} />
```

### Step 4: Header.tsx 新增导航

```typescript
// 在 <nav> 中 user 存在时:
<Link to="/positions" className="hover:text-white">持仓</Link>
<Link to="/settings" className="hover:text-white">设置</Link>
```

### Step 5: Build + commit

```bash
cd web && npm run build
```

```bash
git add web/src/pages/PositionsPage.tsx web/src/pages/SettingsPage.tsx web/src/App.tsx web/src/components/Header.tsx
git commit -m "feat: positions page + settings page (email/feishu/exchange keys)"
```

---

## Task 11: LeaderboardPage WS 改造

**Files:**
- Modify: `web/src/pages/LeaderboardPage.tsx`

### Step 1: 替换 load 函数 + 加 WS

保留现有搜索/筛选/置顶逻辑，只改数据加载方式：

```typescript
// 删除 load 函数和 useEffect
// 新增:
import { useWebSocket } from '../hooks/useWebSocket'

// 在 LeaderboardPage 中:
const [wsEnabled, setWsEnabled] = useState(autoRefresh)

useWebSocket(direction, (items) => {
  setRows(items)
  setLoading(false)
}, wsEnabled)

// 暂停/恢复刷新联动 WS
useEffect(() => {
  setWsEnabled(autoRefresh)
}, [autoRefresh])
```

保留现有 `filtered` / `filteredTotal` / `pageItems` / `displayPageSize` 逻辑不变。

### Step 2: Build + commit

```bash
cd web && npm run build
git add web/src/pages/LeaderboardPage.tsx
git commit -m "feat: leaderboard uses WebSocket for real-time updates"
```

---

## Task 12: 管理后台前端 — Login + Layout

**Files:**
- Create: `web/src/pages/admin/AdminLoginPage.tsx`
- Create: `web/src/pages/admin/AdminLayout.tsx`

### Step 1: AdminLoginPage.tsx

```typescript
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { adminLogin } from '../../api'

export function AdminLoginPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const navigate = useNavigate()

  const handleLogin = async () => {
    try {
      const res = await adminLogin(username, password)
      localStorage.setItem('admin_token', res.access_token)
      localStorage.setItem('admin_role', res.role)
      navigate('/admin/limits')
    } catch {
      setError('登录失败')
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-950">
      <div className="w-96 rounded-lg border border-gray-800 p-6">
        <h2 className="mb-4 text-center text-xl font-bold text-emerald-400">管理后台</h2>
        {error && <p className="mb-3 text-center text-sm text-red-400">{error}</p>}
        <input value={username} onChange={(e) => setUsername(e.target.value)}
          placeholder="用户名" onKeyDown={(e) => e.key === 'Enter' && handleLogin()}
          className="mb-3 w-full rounded bg-gray-800 border border-gray-700 px-3 py-2 text-gray-300" />
        <input value={password} onChange={(e) => setPassword(e.target.value)}
          type="password" placeholder="密码" onKeyDown={(e) => e.key === 'Enter' && handleLogin()}
          className="mb-4 w-full rounded bg-gray-800 border border-gray-700 px-3 py-2 text-gray-300" />
        <button onClick={handleLogin}
          className="w-full rounded bg-emerald-600 py-2 font-medium hover:bg-emerald-500">
          登录
        </button>
      </div>
    </div>
  )
}
```

### Step 2: AdminLayout.tsx

```typescript
import { useEffect, useState } from 'react'
import { Link, Outlet, useNavigate, useLocation } from 'react-router-dom'
import { getAdminMe } from '../../api'

const MENU = [
  { path: '/admin/limits', label: '额度规则', roles: ['superadmin', 'risk_admin'] },
  { path: '/admin/switches', label: '平台开关', roles: ['superadmin', 'risk_admin'] },
  { path: '/admin/announcements', label: '公告管理', roles: ['superadmin', 'ops_admin'] },
  { path: '/admin/audit', label: '审计日志', roles: ['superadmin'] },
  { path: '/admin/users', label: '用户管理', roles: ['superadmin', 'ops_admin'] },
]

export function AdminLayout() {
  const [role, setRole] = useState('')
  const navigate = useNavigate()
  const location = useLocation()

  useEffect(() => {
    const r = localStorage.getItem('admin_role') || ''
    setRole(r)
    getAdminMe().catch(() => {
      localStorage.removeItem('admin_token')
      navigate('/admin/login')
    })
  }, [])

  const logout = () => {
    localStorage.removeItem('admin_token')
    localStorage.removeItem('admin_role')
    navigate('/admin/login')
  }

  return (
    <div className="flex min-h-screen bg-gray-950">
      <aside className="w-48 border-r border-gray-800 p-4">
        <Link to="/admin" className="mb-6 block text-lg font-bold text-emerald-400">管理后台</Link>
        <nav className="flex flex-col gap-2">
          {MENU.filter((m) => m.roles.includes(role)).map((m) => (
            <Link key={m.path} to={m.path}
              className={`rounded px-3 py-2 text-sm ${
                location.pathname === m.path ? 'bg-emerald-600 text-white' : 'text-gray-400 hover:bg-gray-800'
              }`}>
              {m.label}
            </Link>
          ))}
        </nav>
        <button onClick={logout} className="mt-8 w-full rounded bg-gray-800 py-1.5 text-xs text-gray-400 hover:bg-gray-700">
          退出登录
        </button>
      </aside>
      <main className="flex-1 p-6">
        <Outlet />
      </main>
    </div>
  )
}
```

### Step 3: Route registration

```typescript
// App.tsx new imports:
import { AdminLoginPage } from './pages/admin/AdminLoginPage'
import { AdminLayout } from './pages/admin/AdminLayout'
import { LimitsPage } from './pages/admin/LimitsPage'
import { SwitchesPage } from './pages/admin/SwitchesPage'
import { AnnouncementsPage } from './pages/admin/AnnouncementsPage'
import { AuditPage } from './pages/admin/AuditPage'
import { AdminUsersPage } from './pages/admin/UsersPage'

// New routes (outside main div, separate BrowserRouter or nested):
<Route path="/admin/login" element={<AdminLoginPage />} />
<Route path="/admin" element={<AdminLayout />}>
  <Route path="limits" element={<LimitsPage />} />
  <Route path="switches" element={<SwitchesPage />} />
  <Route path="announcements" element={<AnnouncementsPage />} />
  <Route path="audit" element={<AuditPage />} />
  <Route path="users" element={<AdminUsersPage />} />
</Route>
```

### Step 4: Commit

```bash
git add web/src/pages/admin/
git commit -m "feat: admin login page + admin layout"
```

---

## Task 13: 管理后台页面 (Limits/Switches/Announcements/Audit/Users)

**Files:**
- Create: `web/src/pages/admin/LimitsPage.tsx`
- Create: `web/src/pages/admin/SwitchesPage.tsx`
- Create: `web/src/pages/admin/AnnouncementsPage.tsx`
- Create: `web/src/pages/admin/AuditPage.tsx`
- Create: `web/src/pages/admin/UsersPage.tsx`

All five pages follow the same pattern — data table + CRUD forms + Tailwind dark theme. Each page calls the corresponding admin API function.

### Step 1: LimitsPage.tsx — 额度规则 CRUD

Simple table listing RiskLimitRules with Add/Edit/Delete. Add form: scope_type dropdown, scope_id input, limit_type dropdown (TOTAL_NOTIONAL/SINGLE_TASK_NOTIONAL), limit_value number input.

### Step 2: SwitchesPage.tsx — 开关控制

Table listing platform switches with toggle ON/OFF and Delete. Add form: switch_key input + scope_type dropdown + scope_id input.

### Step 3: AnnouncementsPage.tsx — 公告管理

Table + Create/Edit modal. Fields: title, content (textarea), priority, is_pinned checkbox, audience_type, channels, status (draft/published/archived).

### Step 4: AuditPage.tsx — 审计日志 (只读)

Paginated table showing AdminActionLog rows sorted by created_at DESC.

### Step 5: AdminUsersPage.tsx — 用户列表 (只读)

Paginated table showing User rows: id, username, status, is_trading_enabled.

Each page self-contained with ~80-120 lines of JSX. Exact code in implementation step-by-step.

### Step 6: Build + commit

```bash
cd web && npm run build
git add web/src/pages/admin/
git commit -m "feat: admin management pages (limits/switches/announcements/audit/users)"
```

---

## Task 14: 集成部署 + 验证

### Step 1: Run all tests

```bash
python -m pytest tests/ --tb=short -q
```

Expected: 346 passed

### Step 2: Build frontend

```bash
cd web && npm run build
```

Expected: no TypeScript errors

### Step 3: Deploy to server

```bash
$sshKey = "d:\old\FuRunSystemV4\.tmp-ssh\futunsystemv3_deploy_ed25519"

# Python files
C:\Windows\System32\OpenSSH\scp.exe -i $sshKey -o StrictHostKeyChecking=no "d:\old\FuRunSystemV4\models.py" ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/
C:\Windows\System32\OpenSSH\scp.exe -i $sshKey -o StrictHostKeyChecking=no "d:\old\FuRunSystemV4\app\api\__init__.py" ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/api/
C:\Windows\System32\OpenSSH\scp.exe -i $sshKey -o StrictHostKeyChecking=no "d:\old\FuRunSystemV4\app\api\deps.py" ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/api/
C:\Windows\System32\OpenSSH\scp.exe -i $sshKey -o StrictHostKeyChecking=no "d:\old\FuRunSystemV4\app\api\ws.py" ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/api/
C:\Windows\System32\OpenSSH\scp.exe -i $sshKey -o StrictHostKeyChecking=no "d:\old\FuRunSystemV4\app\api\positions.py" ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/api/
C:\Windows\System32\OpenSSH\scp.exe -i $sshKey -o StrictHostKeyChecking=no "d:\old\FuRunSystemV4\app\api\settings.py" ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/api/
C:\Windows\System32\OpenSSH\scp.exe -i $sshKey -o StrictHostKeyChecking=no -r "d:\old\FuRunSystemV4\app\api\admin" ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/api/
C:\Windows\System32\OpenSSH\scp.exe -i $sshKey -o StrictHostKeyChecking=no "d:\old\FuRunSystemV4\app\runtime\runtime_events.py" ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/runtime/
C:\Windows\System32\OpenSSH\scp.exe -i $sshKey -o StrictHostKeyChecking=no "d:\old\FuRunSystemV4\app\runtime\alerting.py" ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/runtime/

# Frontend
C:\Windows\System32\OpenSSH\ssh.exe -i $sshKey -o StrictHostKeyChecking=no ubuntu@43.165.166.57 "rm -rf /home/ubuntu/furunsystemv4/current/web/dist/assets; mkdir -p /home/ubuntu/furunsystemv4/current/web/dist/assets"
C:\Windows\System32\OpenSSH\scp.exe -i $sshKey -o StrictHostKeyChecking=no "d:\old\FuRunSystemV4\web\dist\index.html" ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/web/dist/
C:\Windows\System32\OpenSSH\scp.exe -i $sshKey -o StrictHostKeyChecking=no -r "d:\old\FuRunSystemV4\web\dist\assets" ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/web/dist/

# Restart
C:\Windows\System32\OpenSSH\ssh.exe -i $sshKey -o StrictHostKeyChecking=no ubuntu@43.165.166.57 "find /home/ubuntu/furunsystemv4/current -name '*.pyc' -delete; sudo systemctl restart furun-api; curl -s http://localhost:8000/api/admin/limits -H 'Authorization: Bearer test' | head -20"
```

### Step 4: Final commit

```bash
git add -A
git commit -m "chore: full deploy of frontend+admin enhancement round"
git push
```
