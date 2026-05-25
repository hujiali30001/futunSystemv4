# Route Index Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增加一个可重复执行的 CLI 命令，把 Redis 中历史遗留的 `route:user_node:{user_id}` 单键补录进 `route:user_node:index`，让 `GET /routes` 能看到老路由。

**Architecture:** 保持 Redis 单用户键 `route:user_node:{user_id}` 为路由真值，复用现有 `UserNodeRouteStore`，为其增加 `SCAN` 驱动的历史键遍历与索引回填能力。新增一个独立 CLI 入口 `route_admin_cli.py`，只负责调用回填逻辑并输出统计结果，不扩展现有 HTTP 接口。

**Tech Stack:** Python 3.10+, asyncio, redis.asyncio, pydantic-settings, pytest, pytest-asyncio

---

## 文件结构与职责

- `app/runtime/redis_flow.py`
  - 扩展 `UserNodeRouteStore`，支持 `SCAN` 历史路由键并回填索引集合
- `app/runtime/route_admin_cli.py`
  - 新增回填命令入口，支持 `backfill-index` 和 `--dry-run`
- `docs/ops/live-workers-systemd.md`
  - 补 route 索引回填命令的远端执行步骤与验证方式
- `tests/test_redis_opportunity_flow.py`
  - 覆盖 `SCAN` 跳过索引键、空值跳过、`dry_run` 和回填统计
- `tests/test_route_admin_cli.py`
  - 覆盖 CLI 参数解析、输出结果和 dry-run 行为

### Task 1: 扩展 Redis 路由存储，支持历史索引回填

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
- Test: `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`

- [ ] **Step 1: 先写失败测试，锁定 SCAN 回填语义**

```python
import pytest

from app.runtime.redis_flow import UserNodeRouteStore


class FakeRedis:
    def __init__(self):
        self.values = {
            "route:user_node:42": "node-a",
            "route:user_node:99": "main",
            "route:user_node:empty": "",
        }
        self.set_members = set()
        self.scan_batches = [
            ["route:user_node:42", "route:user_node:index"],
            ["route:user_node:99", "route:user_node:empty"],
        ]

    async def get(self, key):
        return self.values.get(key)

    async def sadd(self, key, *values):
        self.set_members.update(values)
        return len(values)

    async def scan(self, cursor=0, match=None, count=None):
        if cursor >= len(self.scan_batches):
            return 0, []
        next_cursor = cursor + 1
        if next_cursor >= len(self.scan_batches):
            next_cursor = 0
        return next_cursor, self.scan_batches[cursor]


@pytest.mark.asyncio
async def test_backfill_route_index_skips_index_key_and_backfills_user_ids():
    redis_client = FakeRedis()
    store = UserNodeRouteStore(redis_client)

    result = await store.backfill_route_index()

    assert result == {
        "scanned": 3,
        "indexed": 2,
        "skipped": 1,
        "dry_run": False,
    }
    assert redis_client.set_members == {"42", "99"}


@pytest.mark.asyncio
async def test_backfill_route_index_dry_run_does_not_write_index():
    redis_client = FakeRedis()
    store = UserNodeRouteStore(redis_client)

    result = await store.backfill_route_index(dry_run=True)

    assert result["dry_run"] is True
    assert result["indexed"] == 2
    assert redis_client.set_members == set()
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_redis_opportunity_flow.py -v`
Expected: FAIL，提示 `backfill_route_index()` 不存在，或扫描逻辑还没有跳过 `route:user_node:index`

- [ ] **Step 3: 实现最小回填能力**

```python
class UserNodeRouteStore:
    ROUTE_INDEX_KEY = "route:user_node:index"

    async def iter_route_user_ids(self):
        cursor = 0
        while True:
            cursor, keys = await self.redis_client.scan(
                cursor=cursor,
                match="route:user_node:*",
                count=100,
            )
            for key in keys:
                if key == self.ROUTE_INDEX_KEY:
                    continue
                prefix = "route:user_node:"
                if not str(key).startswith(prefix):
                    continue
                user_id = str(key)[len(prefix) :].strip()
                if user_id:
                    yield user_id
            if cursor == 0:
                break

    async def backfill_route_index(self, dry_run: bool = False) -> dict[str, object]:
        scanned = 0
        indexed = 0
        skipped = 0
        async for user_id in self.iter_route_user_ids():
            scanned += 1
            node_id = await self.get_user_node(user_id)
            if not node_id:
                skipped += 1
                continue
            if not dry_run:
                await self.redis_client.sadd(self.ROUTE_INDEX_KEY, user_id)
            indexed += 1
        return {
            "scanned": scanned,
            "indexed": indexed,
            "skipped": skipped,
            "dry_run": dry_run,
        }
```

- [ ] **Step 4: 重新运行 Redis 回填测试**

Run: `python -m pytest tests/test_redis_opportunity_flow.py -v`
Expected: PASS，回填逻辑、跳过索引键和 dry-run 行为全部通过

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/redis_flow.py tests/test_redis_opportunity_flow.py
git commit -m "feat: add route index backfill support"
```

### Task 2: 新增 route-admin CLI 命令

**Files:**
- Create: `d:\old\FuRunSystemV4\app\runtime\route_admin_cli.py`
- Test: `d:\old\FuRunSystemV4\tests\test_route_admin_cli.py`

- [ ] **Step 1: 先写失败测试，锁定 CLI 参数和输出**

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
async def test_run_backfill_index_returns_json_summary(capsys):
    store = FakeRouteStore(
        {"ok": True, "scanned": 3, "indexed": 2, "skipped": 1}
    )

    exit_code = await run_backfill_index(store, dry_run=True)

    assert exit_code == 0
    assert store.calls == [True]
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["dry_run"] is True
```

- [ ] **Step 2: 运行 CLI 定向测试并确认失败**

Run: `python -m pytest tests/test_route_admin_cli.py -v`
Expected: FAIL，提示 `route_admin_cli.py` 或 `build_parser()` / `run_backfill_index()` 不存在

- [ ] **Step 3: 实现最小 CLI 命令入口**

```python
import argparse
import asyncio
import json

from app.runtime.redis_flow import UserNodeRouteStore
from app.runtime.route_admin_service import default_redis_factory
from app.runtime.worker_config import get_worker_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    backfill = subparsers.add_parser("backfill-index")
    backfill.add_argument("--dry-run", action="store_true")
    return parser


async def run_backfill_index(route_store: UserNodeRouteStore, *, dry_run: bool) -> int:
    result = await route_store.backfill_route_index(dry_run=dry_run)
    result["ok"] = True
    print(json.dumps(result, ensure_ascii=False))
    return 0


async def _run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_worker_settings()
    redis_client = default_redis_factory(settings.redis_url)
    try:
        route_store = UserNodeRouteStore(redis_client)
        if args.command == "backfill-index":
            return await run_backfill_index(route_store, dry_run=args.dry_run)
        return 1
    finally:
        await redis_client.aclose()


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(asyncio.run(_run(argv)))
```

- [ ] **Step 4: 重新运行 CLI 定向测试**

Run: `python -m pytest tests/test_route_admin_cli.py -v`
Expected: PASS，CLI 参数解析和 JSON 输出通过

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/route_admin_cli.py tests/test_route_admin_cli.py
git commit -m "feat: add route index backfill cli"
```

### Task 3: 补运维文档并做本地与远端验证

**Files:**
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
- Test: `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`
- Test: `d:\old\FuRunSystemV4\tests\test_route_admin_cli.py`

- [ ] **Step 1: 更新运维文档，补回填命令说明**

```markdown
## Route Index Backfill

如果历史上存在只写了 `route:user_node:{user_id}` 但没有进入
`route:user_node:index` 的老路由，可以执行：

```bash
cd /home/ubuntu/furunsystemv4/current
python -m app.runtime.route_admin_cli backfill-index --dry-run
python -m app.runtime.route_admin_cli backfill-index
redis-cli SMEMBERS route:user_node:index
curl -s -H "Authorization: Bearer $ROUTE_ADMIN_TOKEN" http://127.0.0.1:8787/routes
```

验收点：

- `--dry-run` 只输出统计结果，不写索引
- 正式执行后历史路由出现在 `route:user_node:index`
- `GET /routes` 能看到历史老路由
```

- [ ] **Step 2: 运行本地总回归**

Run: `python -m pytest tests/test_redis_opportunity_flow.py tests/test_route_admin_cli.py -v`
Expected: PASS，回填逻辑与 CLI 测试全部通过

- [ ] **Step 3: 在主服务器执行 dry-run 和正式回填**

Run:

```bash
cd /home/ubuntu/furunsystemv4/current
python -m app.runtime.route_admin_cli backfill-index --dry-run
python -m app.runtime.route_admin_cli backfill-index
redis-cli SMEMBERS route:user_node:index
curl -s -H "Authorization: Bearer $ROUTE_ADMIN_TOKEN" http://127.0.0.1:8787/routes
```

Expected:
- dry-run 输出 JSON 统计
- 正式执行后输出 `ok: true`
- `route:user_node:index` 包含历史路由用户
- `GET /routes` 能看到补回的用户

- [ ] **Step 4: 再次运行回填命令验证幂等性**

Run:

```bash
python -m app.runtime.route_admin_cli backfill-index
```

Expected:
- 不报错
- 统计结果中的 `indexed` 不会无限增长

- [ ] **Step 5: 完成最终提交**

```bash
git add app/runtime/redis_flow.py app/runtime/route_admin_cli.py docs/ops/live-workers-systemd.md tests/test_redis_opportunity_flow.py tests/test_route_admin_cli.py
git commit -m "feat: add route index backfill command"
```

## 自检结果

- Spec coverage:
  - CLI 命令形态：`Task 2`
  - `SCAN` 回填逻辑与 dry-run：`Task 1`
  - 运维文档与远端执行：`Task 3`
- Placeholder scan:
  - 未保留 `TODO`、`TBD`、"后续补"、"类似上一步" 等占位语句
- Type consistency:
  - 统一使用 `route:user_node:{user_id}` 与 `route:user_node:index`
  - CLI 子命令统一使用 `backfill-index`
