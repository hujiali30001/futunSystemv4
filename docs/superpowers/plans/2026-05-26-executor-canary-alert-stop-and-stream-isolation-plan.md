# Executor Canary Alert Stop And Stream Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the repeated Feishu `executor.task.failed` alerts for `repair-systemd-canary-1`, clean the main-server canary residue, and isolate future systemd canaries onto dedicated Redis streams.

**Architecture:** Keep the fix narrowly scoped to remote validation assets plus ops documentation. First add a focused local regression around the remote canary script configuration so future validations cannot point at the real `main` streams by mistake; then update the `.tmp-ssh` validation script to clean historical `repair-systemd-canary-1` entries from the production streams, run the canary only on dedicated `repair-canary` streams, and verify cleanup plus alert silence; finally record the fix and operational notes in the systemd ops document.

**Tech Stack:** Python 3.10+, pytest, PowerShell, OpenSSH `ssh/scp`, Redis Streams, systemd, remote Linux venv

---

## File Structure

- Create: `d:\old\FuRunSystemV4\tests\test_remote_canary_stream_isolation.py`
  - Lock the canary script to dedicated `repair-canary` stream names and verify it no longer references the real `main` executor/repair streams for injection
- Modify: `d:\old\FuRunSystemV4\.tmp-ssh\sync_and_validate_repair_systemd_dual_service.py`
  - Add targeted remote cleanup for `repair-systemd-canary-1` in `arbitrage_tasks`, `stream:spot_exec_tasks:main`, and `stream:repair_tasks:main`
  - Temporarily override `.env.worker` so the canary uses `stream:spot_exec_tasks:repair-canary` and `stream:repair_tasks:repair-canary`
  - Verify post-cleanup silence and keep canary cleanup idempotent
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
  - Add a repair-systemd canary alert remediation note documenting the residue cleanup and stream isolation rule

## Task 1: Lock The Canary Script To Dedicated Streams With A Failing Test First

**Files:**
- Create: `d:\old\FuRunSystemV4\tests\test_remote_canary_stream_isolation.py`
- Modify: `d:\old\FuRunSystemV4\.tmp-ssh\sync_and_validate_repair_systemd_dual_service.py`

- [ ] **Step 1: Write the failing regression test**

Create `tests/test_remote_canary_stream_isolation.py` with this content:

```python
from pathlib import Path


def test_repair_systemd_canary_uses_dedicated_streams_and_not_main_stream_injection():
    script_path = Path(
        r"d:\old\FuRunSystemV4\.tmp-ssh\sync_and_validate_repair_systemd_dual_service.py"
    )
    script_text = script_path.read_text(encoding="utf-8")

    assert "stream:spot_exec_tasks:repair-canary" in script_text
    assert "stream:repair_tasks:repair-canary" in script_text
    assert "EXECUTOR_STREAM_KEY=stream:spot_exec_tasks:repair-canary" in script_text
    assert "REPAIR_STREAM_KEY=stream:repair_tasks:repair-canary" in script_text
    assert "redis-cli XADD stream:spot_exec_tasks:repair-canary '*'" in script_text
    assert "redis-cli XADD stream:spot_exec_tasks:main '*'" not in script_text
```

- [ ] **Step 2: Run the test and watch it fail**

Run:

```bash
pytest tests/test_remote_canary_stream_isolation.py -q
```

Expected: FAIL because the current script still injects into `stream:spot_exec_tasks:main` and does not yet define the dedicated `repair-canary` streams.

- [ ] **Step 3: Implement the minimal script changes to make the test pass**

Update `d:\old\FuRunSystemV4\.tmp-ssh\sync_and_validate_repair_systemd_dual_service.py` so these constants and env overrides exist:

```python
CANARY_EXECUTOR_STREAM = "stream:spot_exec_tasks:repair-canary"
CANARY_REPAIR_STREAM = "stream:repair_tasks:repair-canary"
```

And in the `.env.worker` rewrite block:

```python
lines = [line for line in lines if not line.startswith("EXECUTOR_STREAM_KEY=")]
lines = [line for line in lines if not line.startswith("REPAIR_STREAM_KEY=")]
lines.append("EXECUTOR_STREAM_KEY=stream:spot_exec_tasks:repair-canary")
lines.append("REPAIR_STREAM_KEY=stream:repair_tasks:repair-canary")
```

And in the canary injection block:

```python
redis-cli XADD stream:spot_exec_tasks:repair-canary '*' \
task_uuid {CANARY_TASK_UUID} \
user_id 42 \
source_message_id {CANARY_SOURCE_ID} \
symbol BTC/USDT \
buy_exchange bitget \
sell_exchange gate \
buy_account_id {CANARY_BUY_ACCOUNT_ID} \
sell_account_id {CANARY_SELL_ACCOUNT_ID} \
target_quote_amount 15.0
```

- [ ] **Step 4: Re-run the focused regression**

Run:

```bash
pytest tests/test_remote_canary_stream_isolation.py -q
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Commit the test-first stream isolation change**

```bash
git add tests/test_remote_canary_stream_isolation.py .tmp-ssh/sync_and_validate_repair_systemd_dual_service.py
git commit -m "test: lock repair canary to dedicated streams"
```

Expected: if `.tmp-ssh` remains ignored and therefore not commit-able, commit only the new test file and keep the `.tmp-ssh` script local; mention that explicitly in the handoff.

## Task 2: Clean Historical Canary Residue And Verify Alert Silence

**Files:**
- Modify: `d:\old\FuRunSystemV4\.tmp-ssh\sync_and_validate_repair_systemd_dual_service.py`

- [ ] **Step 1: Add targeted remote cleanup helpers**

Extend `d:\old\FuRunSystemV4\.tmp-ssh\sync_and_validate_repair_systemd_dual_service.py` with helpers that:

1. Delete `arbitrage_tasks.task_uuid == repair-systemd-canary-1`
2. Enumerate recent entries from:
   - `stream:spot_exec_tasks:main`
   - `stream:repair_tasks:main`
   - `stream:spot_exec_tasks:repair-canary`
   - `stream:repair_tasks:repair-canary`
3. Filter entries whose field payload contains:
   - `task_uuid = repair-systemd-canary-1`
4. Run `redis-cli XDEL <stream> <message-id...>` for matching IDs

Add helpers like:

```python
def collect_remote_stream_entries(stream_key: str, count: int = 50) -> str:
    return remote_bash(
        f"redis-cli XREVRANGE {stream_key} + - COUNT {count}"
    )
```

```python
def delete_remote_stream_entries(stream_key: str, message_ids: list[str]) -> str:
    if not message_ids:
        return "0"
    ids = " ".join(message_ids)
    return remote_bash(f"redis-cli XDEL {stream_key} {ids}")
```

```python
def extract_matching_message_ids(xrevrange_output: str, *, task_uuid: str) -> list[str]:
    lines = [line.strip() for line in xrevrange_output.splitlines() if line.strip()]
    matches: list[str] = []
    current_id: str | None = None
    current_fields: list[str] = []
    for line in lines:
        if "-" in line and line.split("-", 1)[0].isdigit():
            if current_id is not None and task_uuid in current_fields:
                matches.append(current_id)
            current_id = line
            current_fields = []
            continue
        current_fields.append(line)
    if current_id is not None and task_uuid in current_fields:
        matches.append(current_id)
    return matches
```

- [ ] **Step 2: Add pre-run cleanup and post-run verification**

Before enabling canary mode, run cleanup against:

```python
MAIN_EXECUTOR_STREAM = "stream:spot_exec_tasks:main"
MAIN_REPAIR_STREAM = "stream:repair_tasks:main"
CANARY_EXECUTOR_STREAM = "stream:spot_exec_tasks:repair-canary"
CANARY_REPAIR_STREAM = "stream:repair_tasks:repair-canary"
```

And record a structured result block such as:

```python
cleanup_result = {
    "main_executor_removed_ids": main_executor_removed_ids,
    "main_repair_removed_ids": main_repair_removed_ids,
    "canary_executor_removed_ids": canary_executor_removed_ids,
    "canary_repair_removed_ids": canary_repair_removed_ids,
}
```

After the canary and cleanup complete, capture:

```python
post_cleanup_main_executor = collect_remote_stream_entries(MAIN_EXECUTOR_STREAM, count=20)
post_cleanup_main_repair = collect_remote_stream_entries(MAIN_REPAIR_STREAM, count=20)
post_cleanup_canary_executor = collect_remote_stream_entries(CANARY_EXECUTOR_STREAM, count=20)
post_cleanup_canary_repair = collect_remote_stream_entries(CANARY_REPAIR_STREAM, count=20)
executor_silence_logs = remote_bash(
    "sudo journalctl -u furun-spot-executor.service -n 120 --no-pager | grep 'repair-systemd-canary-1' || true"
)
```

- [ ] **Step 3: Run syntax check on the updated script**

Run:

```bash
python -m py_compile .tmp-ssh/sync_and_validate_repair_systemd_dual_service.py
```

Expected: PASS with no output.

- [ ] **Step 4: Run the remote stop-the-alert validation**

Run:

```bash
python .tmp-ssh/sync_and_validate_repair_systemd_dual_service.py
```

Expected: print JSON showing at least:

```json
{
  "cleanup_result": {
    "main_executor_removed_ids": ["..."]
  },
  "active_executor": "active",
  "active_repair": "active",
  "final_task": {
    "status": "SUCCEEDED",
    "execution_status": "OPEN_HEDGED"
  }
}
```

And verify in the JSON:

- canary injection uses `stream:spot_exec_tasks:repair-canary`
- `latest_repair_entries` comes from `stream:repair_tasks:repair-canary`
- `post_cleanup_main_executor` does not contain `repair-systemd-canary-1`
- `post_cleanup_main_repair` does not contain `repair-systemd-canary-1`
- `executor_silence_logs` does not contain a fresh `task not found: repair-systemd-canary-1`

- [ ] **Step 5: Verify cleanup and ignored state**

Run:

```bash
python -c "from pathlib import Path; path = Path(r'd:\old\FuRunSystemV4\.tmp-ssh\repair_systemd_dual_service_output.json'); print(path.exists()); text = path.read_text(encoding='utf-8'); print('stream:spot_exec_tasks:repair-canary' in text); print('task not found: repair-systemd-canary-1' in text)"
git status --short --ignored .tmp-ssh
```

Expected:

- first line prints `True`
- second line prints `True`
- third line may be `False`, or only present inside historical-before-cleanup evidence rather than fresh silence checks
- `.tmp-ssh` remains ignored and does not produce tracked workspace changes

## Task 3: Record The Alert Remediation In Ops Documentation

**Files:**
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: Add the remediation note**

Append a note after the existing `Repair Worker Systemd Dual-Service Validation` section with content like:

```md
### Executor Canary Alert Remediation

如果飞书持续出现：

- `executor.task.failed`
- `error = task not found: repair-systemd-canary-1`

优先判断这是不是远端 systemd canary 留下的历史消息被 `executor` 重启后重读，而不是正常业务任务失败。

本次排障结论：

- `executor` 当前消费模型启动时从 `last_id = "0-0"` 开始
- 如果真实 `stream:spot_exec_tasks:main` 残留旧的 `repair-systemd-canary-1`，服务重启后会再次处理
- 若数据库任务真值已删，最终会落成 `task not found: repair-systemd-canary-1`

本次修复动作：

- 清理 `arbitrage_tasks` 中的 `repair-systemd-canary-1`
- 清理真实 `stream:spot_exec_tasks:main` 中该 canary 的历史消息
- 清理真实 `stream:repair_tasks:main` 中该 canary 的历史消息
- 后续远端 systemd canary 一律改用：
  - `stream:spot_exec_tasks:repair-canary`
  - `stream:repair_tasks:repair-canary`

验收点：

- 重启 `furun-spot-executor.service` 后，不再新增 `task not found: repair-systemd-canary-1`
- canary 只写入 `repair-canary` 专用 stream
- cleanup 后，`main` stream 与 `repair-canary` stream 都不残留该 canary
```

- [ ] **Step 2: Run the focused verification set**

Run:

```bash
pytest tests/test_remote_canary_stream_isolation.py -q
git status --short
```

Expected:

- the new test still passes
- only `docs/ops/live-workers-systemd.md` and the new tracked test file remain as tracked changes

- [ ] **Step 3: Commit the documentation and test handoff state**

```bash
git add tests/test_remote_canary_stream_isolation.py docs/ops/live-workers-systemd.md
git commit -m "docs: record executor canary alert remediation"
```

Expected: the tracked commit includes the new focused regression test plus the ops document update; `.tmp-ssh` assets remain local ignored tooling.

## Task 4: Final Verification Before Push

**Files:**
- Create: `d:\old\FuRunSystemV4\tests\test_remote_canary_stream_isolation.py`
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: Check working tree and recent commits**

Run:

```bash
git status -sb
git log -4 --oneline
```

Expected:

- working tree is clean
- latest commits include:
  - `docs: record executor canary alert remediation`
  - `test: lock repair canary to dedicated streams` or an explicit note that only the tracked test file was committed while `.tmp-ssh` stayed local

- [ ] **Step 2: Prepare the push readiness summary**

Record these concrete facts in the handoff:

```text
- main stream residue for repair-systemd-canary-1 was deleted
- future systemd canaries now use stream:spot_exec_tasks:repair-canary
- future systemd canaries now use stream:repair_tasks:repair-canary
- cleanup removes both task truth and canary stream messages
- executor restart no longer emits fresh task not found: repair-systemd-canary-1 for this canary
- .tmp-ssh remains local ignored tooling and is not pushed
```
