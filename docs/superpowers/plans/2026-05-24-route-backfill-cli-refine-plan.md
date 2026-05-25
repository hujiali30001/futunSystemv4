# Route Backfill CLI Refine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让历史路由索引回填 CLI 脱离 `aiohttp` 的隐式依赖，并把回填统计结果调整为更清晰、适合运维判断的字段。

**Architecture:** 保持 `UserNodeRouteStore.backfill_route_index()` 为核心逻辑，但把统计字段从 `indexed` 重构为 `found/newly_indexed/already_indexed/skipped/dry_run`。CLI 不再 import `route_admin_service`，而是在自身内部直接构造 Redis 客户端，从而去掉对 HTTP 服务依赖的耦合。

**Tech Stack:** Python 3.10+, asyncio, redis.asyncio, pydantic-settings, pytest, pytest-asyncio

---

## 文件结构与职责

- `app/runtime/redis_flow.py`
  - 调整 `backfill_route_index()` 的统计口径，区分“新补入”和“原本已存在”
- `app/runtime/route_admin_cli.py`
  - 去掉对 `route_admin_service` 的 import，直接创建 Redis 客户端
- `docs/ops/live-workers-systemd.md`
  - 更新 backfill 命令输出字段说明
- `tests/test_redis_opportunity_flow.py`
  - 覆盖新的回填统计字段
- `tests/test_route_admin_cli.py`
  - 覆盖新的 CLI 输出格式，并锁定 CLI 不再依赖 `route_admin_service`

### Task 1: 调整回填统计口径并收紧 Redis store 语义

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
- Test: `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`

- [ ] **Step 1: 先写失败测试，锁定新的统计字段**

```python
import pytest

from app.runtime.redis_flow import UserNodeRouteStore


@pytest.mark.asyncio
async def test_backfill_route_index_returns_newly_indexed_and_already_indexed():
    redis_client = FakeRedis()
    store = UserNodeRouteStore(redis_client)
    redis_client.values.update(
        {
            "route:user_node:42": "node-a",
            "route:user_node:99": "main",
            "route:user_node:empty": "",
            UserNodeRouteStore.ROUTE_INDEX_KEY: "ignored",
        }
    )
    redis_client.set_members.add("42")

    result = await store.backfill_route_index()

    assert result == {
        "found": 2,
        "newly_indexed": 1,
        "already_indexed": 1,
        "skipped": 1,
        "dry_run": False,
    }
    assert redis_client.set_members == {"42", "99"}


@pytest.mark.asyncio
async def test_backfill_route_index_dry_run_reports_without_writing():
    redis_client = FakeRedis()
    store = UserNodeRouteStore(redis_client)
    redis_client.values.update(
        {
            "route:user_node:42": "node-a",
            "route:user_node:99": "main",
            "route:user_node:empty": "",
        }
    )
    redis_client.set_members.add("42")

    result = await store.backfill_route_index(dry_run=True)

    assert result == {
        "found": 2,
        "newly_indexed": 1,
        "already_indexed": 1,
        "skipped": 1,
        "dry_run": True,
    }
    assert redis_client.set_members == {"42"}
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_redis_opportunity_flow.py -v`
Expected: FAIL，提示返回结果中仍是 `indexed/scanned`，或无法区分 `newly_indexed` 与 `already_indexed`

- [ ] **Step 3: 实现最小统计口径调整**

```python
async def backfill_route_index(self, dry_run: bool = False) -> dict[str, object]:
    found = 0
    newly_indexed = 0
    already_indexed = 0
    skipped = 0

    current_index = {str(value) for value in await self.redis_client.smembers(self.ROUTE_INDEX_KEY)}

    async for user_id in self.iter_route_user_ids():
        node_id = await self.get_user_node(user_id)
        if not node_id:
            skipped += 1
            continue

        found += 1
        if user_id in current_index:
            already_indexed += 1
            continue

        if not dry_run:
            await self.redis_client.sadd(self.ROUTE_INDEX_KEY, user_id)
            current_index.add(user_id)
        newly_indexed += 1

    return {
        "found": found,
        "newly_indexed": newly_indexed,
        "already_indexed": already_indexed,
        "skipped": skipped,
        "dry_run": dry_run,
    }
```

- [ ] **Step 4: 重新运行 Redis 回填测试**

Run: `python -m pytest tests/test_redis_opportunity_flow.py -v`
Expected: PASS，新的统计字段和 dry-run 行为全部通过

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/redis_flow.py tests/test_redis_opportunity_flow.py
git commit -m "refactor: clarify route index backfill counters"
```

### Task 2: 让 CLI 脱离 `aiohttp` 隐式依赖

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\route_admin_cli.py`
- Test: `d:\old\FuRunSystemV4\tests\test_route_admin_cli.py`

- [ ] **Step 1: 先写失败测试，锁定新的 CLI 输出字段**

```python
import json

import pytest

from app.runtime.route_admin_cli import build_parser, run_backfill_index


class FakeRouteStore:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def backfill_route_index(self, dry_run: bool = False):
        self.calls.append(dry_run)
        return self.result | {"dry_run": dry_run}


def test_build_parser_accepts_backfill_index_and_dry_run():
    parser = build_parser()

    args = parser.parse_args(["backfill-index", "--dry-run"])

    assert args.command == "backfill-index"
    assert args.dry_run is True


@pytest.mark.asyncio
async def test_run_backfill_index_returns_new_counter_shape(capsys):
    store = FakeRouteStore(
        {
            "ok": True,
            "found": 2,
            "newly_indexed": 1,
            "already_indexed": 1,
            "skipped": 0,
        }
    )

    exit_code = await run_backfill_index(store, dry_run=True)

    assert exit_code == 0
    assert store.calls == [True]
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["dry_run"] is True
    assert output["newly_indexed"] == 1
    assert output["already_indexed"] == 1
    assert "indexed" not in output
```

- [ ] **Step 2: 运行 CLI 定向测试并确认失败**

Run: `python -m pytest tests/test_route_admin_cli.py -v`
Expected: FAIL，提示输出中仍缺少 `newly_indexed/already_indexed`，或仍依赖旧字段

- [ ] **Step 3: 实现最小 CLI 解耦**

```python
import argparse
import asyncio
import json

from redis.asyncio import Redis

from app.runtime.redis_flow import UserNodeRouteStore
from app.runtime.worker_config import get_worker_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    backfill = subparsers.add_parser("backfill-index")
    backfill.add_argument("--dry-run", action="store_true")
    return parser


def build_redis_client(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True)


async def run_backfill_index(route_store: UserNodeRouteStore, *, dry_run: bool) -> int:
    result = await route_store.backfill_route_index(dry_run=dry_run)
    result["ok"] = True
    print(json.dumps(result, ensure_ascii=False))
    return 0


async def _run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_worker_settings()
    redis_client = build_redis_client(settings.redis_url)
    try:
        route_store = UserNodeRouteStore(redis_client)
        if args.command == "backfill-index":
            return await run_backfill_index(route_store, dry_run=args.dry_run)
        return 1
    finally:
        await redis_client.aclose()
```

- [ ] **Step 4: 重新运行 CLI 定向测试**

Run: `python -m pytest tests/test_route_admin_cli.py -v`
Expected: PASS，CLI 使用新统计字段并保持 `backfill-index --dry-run` 兼容

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/route_admin_cli.py tests/test_route_admin_cli.py
git commit -m "refactor: decouple route backfill cli from aiohttp"
```

### Task 3: 更新运维文档并做本地与远端复验

**Files:**
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
- Test: `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`
- Test: `d:\old\FuRunSystemV4\tests\test_route_admin_cli.py`

- [ ] **Step 1: 更新运维文档中的统计字段说明**

```markdown
- 命令返回 JSON，包含 `ok`、`found`、`newly_indexed`、`already_indexed`、`skipped`、`dry_run`
- `dry_run` 为 `true` 时不会写索引
- 当索引已经补齐后，后续复跑的 `newly_indexed` 应为 `0`
```

- [ ] **Step 2: 运行本地相关回归**

Run: `python -m pytest tests/test_redis_opportunity_flow.py tests/test_route_admin_cli.py -v`
Expected: PASS，回填统计与 CLI 输出全部通过

- [ ] **Step 3: 在主服务器同步并复验**

Run:

```bash
cd /home/ubuntu/furunsystemv4/current
./.venv/bin/python -m app.runtime.route_admin_cli backfill-index --dry-run
./.venv/bin/python -m app.runtime.route_admin_cli backfill-index
```

Expected:
- 输出 JSON 中包含 `found/newly_indexed/already_indexed/skipped/dry_run`
- `dry-run` 不会写索引
- 当索引已经齐全时，正式复跑返回 `newly_indexed: 0`

- [ ] **Step 4: 确认 CLI 不再受 `aiohttp` import 影响**

Run:

```bash
python - <<'PY'
import app.runtime.route_admin_cli as cli
print(cli.build_parser().prog)
PY
```

Expected:
- 可以成功 import `route_admin_cli`
- 不需要先 import `route_admin_service`

- [ ] **Step 5: 完成最终提交**

```bash
git add app/runtime/redis_flow.py app/runtime/route_admin_cli.py docs/ops/live-workers-systemd.md tests/test_redis_opportunity_flow.py tests/test_route_admin_cli.py
git commit -m "refactor: refine route backfill cli counters"
```

## 自检结果

- Spec coverage:
  - CLI 解耦：`Task 2`
  - 统计口径重命名：`Task 1`
  - 文档与远端复验：`Task 3`
- Placeholder scan:
  - 未保留 `TODO`、`TBD`、"后续补"、"类似上一步" 等占位语句
- Type consistency:
  - 统一使用 `found`、`newly_indexed`、`already_indexed`、`skipped`、`dry_run`
  - 命令入口保持 `backfill-index`
