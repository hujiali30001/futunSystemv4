# Executor Execution Result Event Remote Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate on the main server that `RedisExecutionTaskConsumer` emits `executor.execution_result` for rich execution results and does not emit it on preflight failure, then record the result in ops documentation.

**Architecture:** Reuse the established `.tmp-ssh` helper pattern instead of touching systemd or real exchanges. First write a focused remote helper that instantiates `RedisExecutionTaskConsumer` with fake Redis, fake spot service, fake event router, and fake repository; then add a local sync script that uploads the minimal runtime files and executes the helper in the server venv; finally capture the server canary output in `live-workers-systemd.md`.

**Tech Stack:** Python 3.10+, pytest-style fake dependencies, PowerShell, OpenSSH `ssh/scp`, remote Linux venv, existing executor runtime events

---

## File Structure

- Create: `d:\old\FuRunSystemV4\.tmp-ssh\executor_execution_result_event_remote_helper.py`
  - Build three server-side canary scenarios: `rich_hedged`, `rich_partial`, `preflight`
  - Instantiate `RedisExecutionTaskConsumer` with fake runtime dependencies
  - Print JSON that summarizes `execution_result_events`, `processed_events`, and `failed_events`
- Create: `d:\old\FuRunSystemV4\.tmp-ssh\sync_and_validate_executor_execution_result_event.py`
  - Upload the helper plus minimal runtime files to the main server
  - Execute the helper with the project venv and print pretty JSON locally
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
  - Add a dedicated `Executor Execution Result Event Validation` section with the server canary results

## Task 1: Build The Remote Helper With Local Red-Green Checks

**Files:**
- Create: `d:\old\FuRunSystemV4\.tmp-ssh\executor_execution_result_event_remote_helper.py`

- [ ] **Step 1: Write the helper with fake runtime dependencies and three scenarios**

Create this file:

```python
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass

from app.runtime.live_workers import RedisExecutionTaskConsumer, RedisOpportunityDispatcher


class FakeRedis:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    async def xread(self, streams: dict[str, str], block: int, count: int = 1):
        stream_key = next(iter(streams.keys()))
        _ = block, count
        return [(stream_key, [("1-0", self.payload)])]

    async def xack(self, stream_key: str, group_name: str, message_id: str) -> int:
        _ = stream_key, group_name, message_id
        return 1


class FakeEventRouter:
    def __init__(self) -> None:
        self.events = []

    async def dispatch(self, event) -> None:
        self.events.append(event)


class FakeTaskRepository:
    def __init__(self) -> None:
        self.executing = []
        self.execution_results = []
        self.failures = []

    def mark_executing(self, task_uuid: str):
        self.executing.append(task_uuid)
        return {"task_uuid": task_uuid}

    def mark_execution_result(
        self,
        task_uuid: str,
        *,
        lifecycle_status: str,
        execution_status: str,
        filled_exchanges: list[str],
        failed_exchanges: list[str],
        repair_action: str,
        repair_reason: str,
    ):
        self.execution_results.append(
            {
                "task_uuid": task_uuid,
                "lifecycle_status": lifecycle_status,
                "execution_status": execution_status,
                "filled_exchanges": list(filled_exchanges),
                "failed_exchanges": list(failed_exchanges),
                "repair_action": repair_action,
                "repair_reason": repair_reason,
            }
        )
        return self.execution_results[-1]

    def mark_failed(self, task_uuid: str, reason: str):
        self.failures.append({"task_uuid": task_uuid, "reason": reason})
        return self.failures[-1]


@dataclass(slots=True)
class FakeExecutionResult:
    ok: bool
    execution_status: str | None
    filled_exchanges: list[str] | None = None
    failed_exchanges: list[str] | None = None
    buy_leg_status: str | None = None
    sell_leg_status: str | None = None
    buy_leg_error_code: str | None = None
    sell_leg_error_code: str | None = None
    buy_leg_error_detail: str | None = None
    sell_leg_error_detail: str | None = None
    failed_stage: str | None = None


class FakeSpotService:
    def __init__(self, result: FakeExecutionResult) -> None:
        self.result = result

    async def run_task(self, **kwargs):
        _ = kwargs
        return self.result


def _serialize_events(events: list[object], event_type: str) -> list[dict[str, object]]:
    serialized = []
    for event in events:
        if getattr(event, "event_type", None) != event_type:
            continue
        serialized.append(
            {
                "event_type": event.event_type,
                "level": event.level,
                "service": event.service,
                "region": event.region,
                "symbol": event.symbol,
                "exchange": event.exchange,
                "exchanges": list(event.exchanges or []),
                "message": event.message,
                "payload": dict(event.payload or {}),
            }
        )
    return serialized


async def _run_case(name: str, payload: dict[str, object], result: FakeExecutionResult) -> dict[str, object]:
    router = FakeEventRouter()
    repository = FakeTaskRepository()
    consumer = RedisExecutionTaskConsumer(
        redis_client=FakeRedis(payload),
        dispatcher=RedisOpportunityDispatcher(FakeSpotService(result)),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        event_router=router,
        region="node-a",
    )
    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )
    return {
        "case": name,
        "processed": processed,
        "execution_result_events": _serialize_events(router.events, "executor.execution_result"),
        "processed_events": _serialize_events(router.events, "executor.task.processed"),
        "failed_events": _serialize_events(router.events, "executor.task.failed"),
        "repository_execution_results": list(repository.execution_results),
        "repository_failures": list(repository.failures),
    }


async def main() -> None:
    rich_hedged_payload = {
        "task_uuid": "task-hedged",
        "user_id": "42",
        "symbol": "BTC/USDT",
        "buy_exchange": "okx",
        "sell_exchange": "gate",
        "target_quote_amount": "40.0",
        "source_message_id": "src-hedged",
    }
    rich_partial_payload = {
        "task_uuid": "task-partial",
        "user_id": "42",
        "symbol": "BTC/USDT",
        "buy_exchange": "okx",
        "sell_exchange": "gate",
        "target_quote_amount": "40.0",
        "source_message_id": "src-partial",
    }
    preflight_payload = {
        "task_uuid": "task-preflight",
        "user_id": "42",
        "symbol": "BTC/USDT",
        "buy_exchange": "okx",
        "sell_exchange": "okx",
        "target_quote_amount": "40.0",
        "source_message_id": "src-preflight",
    }

    rich_hedged = await _run_case(
        "rich_hedged",
        rich_hedged_payload,
        FakeExecutionResult(
            ok=True,
            execution_status="OPEN_HEDGED",
            filled_exchanges=["okx", "gate"],
            failed_exchanges=[],
            buy_leg_status="final_fetched",
            sell_leg_status="final_fetched",
        ),
    )
    rich_partial = await _run_case(
        "rich_partial",
        rich_partial_payload,
        FakeExecutionResult(
            ok=False,
            execution_status="OPEN_PARTIAL",
            filled_exchanges=["okx"],
            failed_exchanges=["gate"],
            buy_leg_status="created",
            sell_leg_status="create_failed",
            sell_leg_error_code="sell_create_failed",
            sell_leg_error_detail="create order failed",
            failed_stage="create_sell",
        ),
    )
    preflight = await _run_case(
        "preflight",
        preflight_payload,
        FakeExecutionResult(ok=False, execution_status=None),
    )

    assert rich_hedged["processed"] == 1, rich_hedged
    assert len(rich_hedged["execution_result_events"]) == 1, rich_hedged
    assert rich_hedged["execution_result_events"][0]["payload"]["execution_status"] == "OPEN_HEDGED", rich_hedged
    assert rich_hedged["execution_result_events"][0]["payload"]["buy_leg_status"] == "final_fetched", rich_hedged
    assert rich_hedged["execution_result_events"][0]["payload"]["sell_leg_status"] == "final_fetched", rich_hedged

    assert rich_partial["processed"] == 1, rich_partial
    assert len(rich_partial["execution_result_events"]) == 1, rich_partial
    assert rich_partial["execution_result_events"][0]["payload"]["execution_status"] == "OPEN_PARTIAL", rich_partial
    assert rich_partial["execution_result_events"][0]["payload"]["sell_leg_error_code"] == "sell_create_failed", rich_partial
    assert rich_partial["execution_result_events"][0]["payload"]["failed_stage"] == "create_sell", rich_partial

    assert preflight["processed"] == 0, preflight
    assert preflight["execution_result_events"] == [], preflight

    print(
        json.dumps(
            {
                "rich_hedged": rich_hedged,
                "rich_partial": rich_partial,
                "preflight": preflight,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run syntax check on the helper**

Run:

```bash
python -m py_compile .tmp-ssh/executor_execution_result_event_remote_helper.py
```

Expected: PASS with no output.

- [ ] **Step 3: Run the helper locally with project import path to verify green**

Run:

```bash
$env:PYTHONPATH='.'; python .tmp-ssh/executor_execution_result_event_remote_helper.py
```

Expected: PASS and print one JSON object containing `rich_hedged`, `rich_partial`, and `preflight`.

- [ ] **Step 4: Commit the helper script**

```bash
git status --short --ignored .tmp-ssh
```

Expected: show the new helper as ignored under `.tmp-ssh`; do not commit because the directory is intentionally ignored.

## Task 2: Add The Local Sync Script And Execute The Server Canary

**Files:**
- Create: `d:\old\FuRunSystemV4\.tmp-ssh\sync_and_validate_executor_execution_result_event.py`

- [ ] **Step 1: Write the sync-and-run script**

Create this file:

```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(r"d:\old\FuRunSystemV4")
TMP_DIR = PROJECT_ROOT / ".tmp-ssh"
KEY_PATH = TMP_DIR / "futunsystemv3_deploy_ed25519_controladmin"
SSH_EXE = r"C:\Windows\System32\OpenSSH\ssh.exe"
SCP_EXE = r"C:\Windows\System32\OpenSSH\scp.exe"
MAIN_HOST = "ubuntu@43.165.166.57"
REMOTE_ROOT = "/home/ubuntu/furunsystemv4/current"
REMOTE_HELPER = "/tmp/executor_execution_result_event_remote_helper.py"

FILES_TO_SYNC = [
    "app/runtime/live_workers.py",
    "app/runtime/runtime_events.py",
]


def run(cmd: list[str]) -> str:
    completed = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            "command failed: "
            + " ".join(cmd)
            + "\nstdout:\n"
            + completed.stdout
            + "\nstderr:\n"
            + completed.stderr
        )
    return completed.stdout.strip()


def ssh(command: str) -> str:
    return run(
        [
            SSH_EXE,
            "-i",
            str(KEY_PATH),
            "-o",
            "StrictHostKeyChecking=accept-new",
            MAIN_HOST,
            command,
        ]
    )


def scp_to_remote(local_path: Path, remote_path: str) -> None:
    run(
        [
            SCP_EXE,
            "-i",
            str(KEY_PATH),
            "-o",
            "StrictHostKeyChecking=accept-new",
            str(local_path),
            f"{MAIN_HOST}:{remote_path}",
        ]
    )


def main() -> int:
    helper_path = TMP_DIR / "executor_execution_result_event_remote_helper.py"

    for relative_path in FILES_TO_SYNC:
        local_path = PROJECT_ROOT / relative_path
        remote_relative_path = relative_path.replace("\\", "/")
        remote_path = f"{REMOTE_ROOT}/{remote_relative_path}"
        scp_to_remote(local_path, remote_path)

    scp_to_remote(helper_path, REMOTE_HELPER)

    validation_output = ssh(
        "cd /home/ubuntu/furunsystemv4/current "
        "&& PYTHONPATH=/home/ubuntu/furunsystemv4/current "
        "/home/ubuntu/furunsystemv4/current/.venv/bin/python "
        f"{REMOTE_HELPER}"
    )
    print(json.dumps(json.loads(validation_output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run syntax check on the sync script**

Run:

```bash
python -m py_compile .tmp-ssh/sync_and_validate_executor_execution_result_event.py
```

Expected: PASS with no output.

- [ ] **Step 3: Execute the server validation**

Run:

```bash
python .tmp-ssh/sync_and_validate_executor_execution_result_event.py
```

Expected: PASS and print pretty JSON showing:

```json
{
  "rich_hedged": {
    "processed": 1
  },
  "rich_partial": {
    "processed": 1
  },
  "preflight": {
    "processed": 0
  }
}
```

The exact payloads should also show one `executor.execution_result` event in `rich_hedged` and `rich_partial`, and zero in `preflight`.

- [ ] **Step 4: Record the exact server output snippet for documentation**

Run:

```bash
python .tmp-ssh/sync_and_validate_executor_execution_result_event.py > .tmp-ssh/executor_execution_result_event_remote_output.json
```

Expected: create a local capture file that can be referenced while updating the ops doc.

## Task 3: Update Ops Documentation And Final Verification

**Files:**
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: Add the new ops section**

Append a section like this near the other validation sections:

```md
### Executor Execution Result Event Validation

`executor.execution_result` 远端闭环采用主服务器 helper 模式验证，不依赖
`furun-spot-executor.service` 当前运行状态，也不会触发真实交易所下单。

本次 helper 路径：

- `.tmp-ssh/executor_execution_result_event_remote_helper.py`
- `.tmp-ssh/sync_and_validate_executor_execution_result_event.py`

主服务器实测记录（2026-05-25）：

- `rich_hedged` 已通过：
  - `processed = 1`
  - 出现 1 条 `executor.execution_result`
  - `execution_status = OPEN_HEDGED`
  - `filled_exchanges = ["okx", "gate"]`
  - `buy_leg_status = final_fetched`
  - `sell_leg_status = final_fetched`
- `rich_partial` 已通过：
  - `processed = 1`
  - 出现 1 条 `executor.execution_result`
  - `execution_status = OPEN_PARTIAL`
  - `failed_exchanges = ["gate"]`
  - `sell_leg_error_code = sell_create_failed`
  - `failed_stage = create_sell`
- `preflight` 非误发已通过：
  - `processed = 0`
  - `execution_result_events = []`

本次验证结论：

- 主服务器代码与虚拟环境下，`executor.execution_result` 事件闭环已通过
- 本次验证只覆盖 helper canary，不代表 systemd 日志链已完整演练
```

- [ ] **Step 2: Run a minimal syntax-and-status check after the doc update**

Run:

```bash
python -m py_compile .tmp-ssh/executor_execution_result_event_remote_helper.py .tmp-ssh/sync_and_validate_executor_execution_result_event.py
git status --short
```

Expected: the two `.tmp-ssh` files remain local ignored assets, and only `docs/ops/live-workers-systemd.md` appears as a tracked modification.

- [ ] **Step 3: Commit the ops documentation update**

```bash
git add docs/ops/live-workers-systemd.md
git commit -m "docs: record executor execution result event validation"
```

- [ ] **Step 4: Confirm the working tree state for handoff**

Run:

```bash
git status -sb
git log -2 --oneline
```

Expected: show the new doc commit at the top and keep `.tmp-ssh` assets untracked/ignored locally.
