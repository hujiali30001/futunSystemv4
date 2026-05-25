# Repair Worker Remote Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate on the main server that the minimal repair worker path can publish a repair task from executor partial results, consume it in the repair worker, emit `repair.task.finished`, save the remote JSON locally, and record the outcome in ops documentation.

**Architecture:** Reuse the established `.tmp-ssh` helper pattern instead of touching systemd or real exchanges. First build a focused remote helper that manually chains `RedisExecutionTaskConsumer` and `RedisRepairTaskConsumer` with fake Redis, fake repository, and fake services; then add a local sync script that uploads the helper plus minimal runtime files and executes it in the server venv while saving the JSON result locally; finally capture the server canary outcome in `live-workers-systemd.md`.

**Tech Stack:** Python 3.10+, asyncio, pytest-style fake dependencies, PowerShell, OpenSSH `ssh/scp`, remote Linux venv, existing runtime consumers and repair execution contract

---

## File Structure

- Create: `d:\old\FuRunSystemV4\.tmp-ssh\repair_worker_remote_helper.py`
  - Build three server-side canary scenarios: `repair_success`, `repair_failure`, `no_repair_publish`
  - Instantiate `RedisExecutionTaskConsumer`, `RepairTaskPublisher`, and `RedisRepairTaskConsumer` with fake runtime dependencies
  - Print JSON summarizing executor processing, repair stream payloads, repair events, and final task status
- Create: `d:\old\FuRunSystemV4\.tmp-ssh\sync_and_validate_repair_worker.py`
  - Upload the helper plus minimal runtime files to the main server
  - Execute the helper with the project venv
  - Pretty-print JSON locally and save the full output to `.tmp-ssh/repair_worker_remote_output.json`
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
  - Add a dedicated `Repair Worker Validation` section with the server canary results

## Task 1: Build The Double-Consumer Remote Helper With Local Checks

**Files:**
- Create: `d:\old\FuRunSystemV4\.tmp-ssh\repair_worker_remote_helper.py`

- [ ] **Step 1: Write the helper with fake runtime dependencies and three scenarios**

Create this file:

```python
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from app.runtime.live_workers import (
    RedisExecutionTaskConsumer,
    RedisRepairTaskConsumer,
)
from app.runtime.redis_flow import RepairTaskPublisher, RedisOpportunityDispatcher


class FakeRedis:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self._executor_served = False
        self.repair_messages: list[tuple[str, dict[str, str]]] = []

    async def xread(self, streams: dict[str, str], block: int, count: int = 1):
        _ = block, count
        stream_key = next(iter(streams.keys()))
        if stream_key.startswith("stream:spot_exec_tasks:"):
            if self._executor_served:
                return []
            self._executor_served = True
            return [(stream_key, [("1-0", self.payload)])]
        if stream_key.startswith("stream:repair_tasks:"):
            if not self.repair_messages:
                return []
            message_id, payload = self.repair_messages.pop(0)
            return [(stream_key, [(message_id, payload)])]
        return []

    async def xadd(self, stream_key: str, fields: dict[str, str]) -> str:
        message_id = f"{len(self.repair_messages) + 1}-0"
        if stream_key.startswith("stream:repair_tasks:"):
            self.repair_messages.append((message_id, dict(fields)))
        return message_id

    async def xack(self, stream_key: str, group_name: str, message_id: str) -> int:
        _ = stream_key, group_name, message_id
        return 1


class FakeEventRouter:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def dispatch(self, event) -> None:
        self.events.append(event)


class FakeTaskRepository:
    def __init__(self) -> None:
        self.execution_results: list[dict[str, object]] = []
        self.repair_results: list[dict[str, object]] = []
        self.failures: list[dict[str, str]] = []
        self.status: str | None = None
        self.execution_status: str | None = None
        self.status_reason: str | None = None

    def mark_executing(self, task_uuid: str, *, worker_node_id: str):
        return {"task_uuid": task_uuid, "worker_node_id": worker_node_id}

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
        row = {
            "task_uuid": task_uuid,
            "lifecycle_status": lifecycle_status,
            "execution_status": execution_status,
            "filled_exchanges": list(filled_exchanges),
            "failed_exchanges": list(failed_exchanges),
            "repair_action": repair_action,
            "repair_reason": repair_reason,
        }
        self.execution_results.append(row)
        self.status = lifecycle_status
        self.execution_status = execution_status
        self.status_reason = None
        return row

    def mark_repair_result(
        self,
        task_uuid: str,
        *,
        lifecycle_status: str,
        execution_status: str,
        filled_exchanges: list[str],
        failed_exchanges: list[str],
        repair_action: str,
        repair_reason: str,
        status_reason: str | None = None,
    ):
        row = {
            "task_uuid": task_uuid,
            "lifecycle_status": lifecycle_status,
            "execution_status": execution_status,
            "filled_exchanges": list(filled_exchanges),
            "failed_exchanges": list(failed_exchanges),
            "repair_action": repair_action,
            "repair_reason": repair_reason,
            "status_reason": status_reason,
        }
        self.repair_results.append(row)
        self.status = lifecycle_status
        self.execution_status = execution_status
        self.status_reason = status_reason
        return row

    def mark_failed(self, task_uuid: str, *, reason: str):
        row = {"task_uuid": task_uuid, "reason": reason}
        self.failures.append(row)
        return row


@dataclass(slots=True)
class FakeExecutionResult:
    ok: bool
    execution_status: str | None
    filled_exchanges: list[str]
    failed_exchanges: list[str]


@dataclass(slots=True)
class FakeRepairResult:
    ok: bool
    status: str
    task_uuid: str
    target_exchanges: list[str]
    repaired_exchanges: list[str]
    remaining_failed_exchanges: list[str]
    reason: str | None = None


class FakeExecutionService:
    def __init__(self, result: FakeExecutionResult) -> None:
        self.result = result

    async def run_task(self, **kwargs):
        _ = kwargs
        return self.result


class FakeRepairExecutionService:
    def __init__(self, result: FakeRepairResult) -> None:
        self.result = result

    async def run_task(self, **kwargs):
        _ = kwargs
        return self.result


def _serialize_events(events: list[object], event_type: str) -> list[dict[str, object]]:
    serialized: list[dict[str, object]] = []
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


async def _run_case(
    name: str,
    payload: dict[str, object],
    execution_result: FakeExecutionResult,
    repair_result: FakeRepairResult | None,
) -> dict[str, object]:
    redis_client = FakeRedis(payload)
    router = FakeEventRouter()
    repository = FakeTaskRepository()

    executor = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(FakeExecutionService(execution_result)),
        stream_key="stream:spot_exec_tasks:main",
        task_repository=repository,
        repair_task_publisher=RepairTaskPublisher(redis_client),
        block_ms=1,
        event_router=router,
        region="main",
    )
    processed_executor = await executor.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    processed_repair = 0
    if repair_result is not None:
        repair_consumer = RedisRepairTaskConsumer(
            redis_client=redis_client,
            repair_service=FakeRepairExecutionService(repair_result),
            stream_key="stream:repair_tasks:main",
            task_repository=repository,
            block_ms=1,
            event_router=router,
            region="main",
        )
        processed_repair = await repair_consumer.run(
            credentials_by_exchange={"gate": object()},
            max_iterations=1,
        )

    return {
        "case": name,
        "processed_executor": processed_executor,
        "processed_repair": processed_repair,
        "repair_task_messages": [payload for _, payload in redis_client.repair_messages],
        "repair_planned_events": _serialize_events(router.events, "executor.repair_planned"),
        "repair_finished_events": _serialize_events(router.events, "repair.task.finished"),
        "task_status": repository.status,
        "task_execution_status": repository.execution_status,
        "task_status_reason": repository.status_reason,
    }


async def main() -> None:
    base_payload = {
        "task_uuid": "task-1",
        "user_id": "42",
        "symbol": "BTC/USDT",
        "buy_exchange": "okx",
        "sell_exchange": "gate",
        "target_quote_amount": "40.0",
        "source_message_id": "src-1",
    }

    repair_success = await _run_case(
        "repair_success",
        dict(base_payload),
        FakeExecutionResult(
            ok=False,
            execution_status="OPEN_PARTIAL",
            filled_exchanges=["okx"],
            failed_exchanges=["gate"],
        ),
        FakeRepairResult(
            ok=True,
            status="REPAIRED",
            task_uuid="task-1",
            target_exchanges=["gate"],
            repaired_exchanges=["gate"],
            remaining_failed_exchanges=[],
            reason=None,
        ),
    )
    repair_failure = await _run_case(
        "repair_failure",
        dict(base_payload),
        FakeExecutionResult(
            ok=False,
            execution_status="OPEN_PARTIAL",
            filled_exchanges=["okx"],
            failed_exchanges=["gate"],
        ),
        FakeRepairResult(
            ok=False,
            status="MANUAL_REQUIRED",
            task_uuid="task-1",
            target_exchanges=["gate"],
            repaired_exchanges=[],
            remaining_failed_exchanges=["gate"],
            reason="repair order failed",
        ),
    )
    no_repair_publish = await _run_case(
        "no_repair_publish",
        dict(base_payload),
        FakeExecutionResult(
            ok=True,
            execution_status="OPEN_HEDGED",
            filled_exchanges=["okx", "gate"],
            failed_exchanges=[],
        ),
        None,
    )

    assert repair_success["processed_executor"] == 1, repair_success
    assert repair_success["processed_repair"] == 1, repair_success
    assert len(repair_success["repair_planned_events"]) == 1, repair_success
    assert len(repair_success["repair_finished_events"]) == 1, repair_success
    assert repair_success["task_status"] == "SUCCEEDED", repair_success
    assert repair_success["task_execution_status"] == "OPEN_HEDGED", repair_success
    assert repair_success["task_status_reason"] is None, repair_success

    assert repair_failure["processed_executor"] == 1, repair_failure
    assert repair_failure["processed_repair"] == 1, repair_failure
    assert len(repair_failure["repair_finished_events"]) == 1, repair_failure
    assert repair_failure["task_status"] == "FAILED", repair_failure
    assert repair_failure["task_execution_status"] == "OPEN_PARTIAL", repair_failure
    assert repair_failure["task_status_reason"] == "manual_required", repair_failure

    assert no_repair_publish["processed_executor"] == 1, no_repair_publish
    assert no_repair_publish["processed_repair"] == 0, no_repair_publish
    assert no_repair_publish["repair_finished_events"] == [], no_repair_publish

    print(
        json.dumps(
            {
                "repair_success": repair_success,
                "repair_failure": repair_failure,
                "no_repair_publish": no_repair_publish,
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
python -m py_compile .tmp-ssh/repair_worker_remote_helper.py
```

Expected: PASS with no output.

- [ ] **Step 3: Run the helper locally with project import path to verify green**

Run:

```bash
$env:PYTHONPATH='.'; python .tmp-ssh/repair_worker_remote_helper.py
```

Expected: PASS and print one JSON object containing `repair_success`, `repair_failure`, and `no_repair_publish`.

- [ ] **Step 4: Check the ignored local asset state**

Run:

```bash
git status --short --ignored .tmp-ssh
```

Expected: show the new helper as ignored under `.tmp-ssh`; do not commit because the directory is intentionally ignored.

## Task 2: Add The Local Sync Script And Execute The Server Canary

**Files:**
- Create: `d:\old\FuRunSystemV4\.tmp-ssh\sync_and_validate_repair_worker.py`

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
REMOTE_HELPER = "/tmp/repair_worker_remote_helper.py"
OUTPUT_PATH = TMP_DIR / "repair_worker_remote_output.json"

FILES_TO_SYNC = [
    "app/runtime/live_workers.py",
    "app/runtime/redis_flow.py",
    "app/runtime/repair_execution_service.py",
    "app/runtime/runtime_events.py",
    "app/db/task_repository.py",
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
    helper_path = TMP_DIR / "repair_worker_remote_helper.py"

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
    parsed = json.loads(validation_output)
    OUTPUT_PATH.write_text(
        json.dumps(parsed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(parsed, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run syntax check on the sync script**

Run:

```bash
python -m py_compile .tmp-ssh/sync_and_validate_repair_worker.py
```

Expected: PASS with no output.

- [ ] **Step 3: Execute the server validation**

Run:

```bash
python .tmp-ssh/sync_and_validate_repair_worker.py
```

Expected: PASS and print pretty JSON showing:

```json
{
  "repair_success": {
    "processed_executor": 1,
    "processed_repair": 1
  },
  "repair_failure": {
    "processed_executor": 1,
    "processed_repair": 1
  },
  "no_repair_publish": {
    "processed_executor": 1,
    "processed_repair": 0
  }
}
```

The exact payloads should also show:

- one `executor.repair_planned` event in `repair_success`
- one `repair.task.finished` event in `repair_success`
- one `repair.task.finished` event in `repair_failure`
- zero `repair.task.finished` events in `no_repair_publish`

- [ ] **Step 4: Verify the local JSON capture file exists**

Run:

```bash
python -c "from pathlib import Path; path = Path(r'd:\old\FuRunSystemV4\.tmp-ssh\repair_worker_remote_output.json'); print(path.exists()); print(path.read_text(encoding='utf-8')[:120] if path.exists() else '')"
```

Expected: print `True` and the beginning of the JSON capture file.

## Task 3: Update Ops Documentation And Final Verification

**Files:**
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: Add the new ops section**

Append a section like this near the other validation sections:

```md
### Repair Worker Validation

最小 `repair worker` 远端闭环采用主服务器 helper 模式验证，不依赖
`furun-spot-executor.service` 或未来 repair service 当前运行状态，也不会触发真实交易所下单。

本次 helper 路径：

- `.tmp-ssh/repair_worker_remote_helper.py`
- `.tmp-ssh/sync_and_validate_repair_worker.py`
- `.tmp-ssh/repair_worker_remote_output.json`

主服务器实测记录（2026-05-25）：

- `repair_success` 已通过：
  - `processed_executor = 1`
  - `processed_repair = 1`
  - 出现 1 条 `executor.repair_planned`
  - 出现 1 条 `repair.task.finished`
  - `task_status = SUCCEEDED`
  - `task_execution_status = OPEN_HEDGED`
- `repair_failure` 已通过：
  - `processed_executor = 1`
  - `processed_repair = 1`
  - 出现 1 条 `repair.task.finished`
  - `task_status = FAILED`
  - `task_execution_status = OPEN_PARTIAL`
  - `task_status_reason = manual_required`
- `no_repair_publish` 非误发已通过：
  - `processed_executor = 1`
  - `processed_repair = 0`
  - `repair_task_messages = []`
  - `repair_finished_events = []`

本次验证结论：

- 主服务器代码与虚拟环境下，最小 repair worker 双消费者闭环已通过
- 本次验证只覆盖 helper canary，不代表 systemd repair 链已完整演练
```

- [ ] **Step 2: Run a minimal syntax-and-status check after the doc update**

Run:

```bash
python -m py_compile .tmp-ssh/repair_worker_remote_helper.py .tmp-ssh/sync_and_validate_repair_worker.py
git status --short
```

Expected: the two `.tmp-ssh` files remain local ignored assets, the local JSON output remains local, and only `docs/ops/live-workers-systemd.md` appears as a tracked modification.

- [ ] **Step 3: Commit the ops documentation update**

```bash
git add docs/ops/live-workers-systemd.md
git commit -m "docs: record repair worker validation"
```

- [ ] **Step 4: Confirm the working tree state for handoff**

Run:

```bash
git status -sb
git log -2 --oneline
```

Expected: show the new doc commit at the top and keep `.tmp-ssh` assets ignored locally.
