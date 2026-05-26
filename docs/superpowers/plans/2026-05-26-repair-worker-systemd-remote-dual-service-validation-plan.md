# Repair Worker Systemd Remote Dual-Service Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate on the main server that the real `furun-spot-executor.service` and `furun-spot-repair.service` can form one minimal `executor -> repair` systemd canary without touching real exchanges.

**Architecture:** Reuse the existing deployment assets and drive the canary with temporary remote-only assets under `.tmp-ssh`. First add a temporary `sitecustomize.py` shim that monkeypatches `RuntimeTradeExecutionService.run_task()` and `RuntimeRepairExecutionService.run_task()` only when a dedicated canary env flag is present; then upload that shim and run a remote validation script that backs up `.env.worker`, enables canary mode, restarts both services, injects one executor task, captures systemd/Redis evidence, restores the remote environment, and saves JSON locally; finally record the result in the ops document.

**Tech Stack:** Python 3.10+, systemd, Redis Streams, PowerShell, OpenSSH `ssh/scp`, remote Linux venv, existing `RuntimeTradeExecutionService` and `RuntimeRepairExecutionService`

---

## File Structure

- Create: `d:\old\FuRunSystemV4\.tmp-ssh\repair_systemd_dual_service_sitecustomize.py`
  - Remote-only monkeypatch shim for `RuntimeTradeExecutionService.run_task()` and `RuntimeRepairExecutionService.run_task()`
  - Activate only when `.env.worker` contains `FURUN_CANARY_MODE=repair_systemd_dual_service`
- Create: `d:\old\FuRunSystemV4\.tmp-ssh\sync_and_validate_repair_systemd_dual_service.py`
  - Upload the shim to remote `sitecustomize.py`
  - Backup/restore `.env.worker`
  - Restart `executor` and `repair`
  - Inject one canary entry into `stream:spot_exec_tasks:main`
  - Capture `systemctl`, `journalctl`, and Redis evidence
  - Save the result to `.tmp-ssh/repair_systemd_dual_service_output.json`
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
  - Add a `Repair Worker Systemd Dual-Service Validation` section with the remote result and cleanup notes

## Task 1: Build The Remote-Only Canary Shim

**Files:**
- Create: `d:\old\FuRunSystemV4\.tmp-ssh\repair_systemd_dual_service_sitecustomize.py`

- [ ] **Step 1: Write the remote-only `sitecustomize.py` shim**

Create `d:\old\FuRunSystemV4\.tmp-ssh\repair_systemd_dual_service_sitecustomize.py` with this content:

```python
from __future__ import annotations

import os


if os.getenv("FURUN_CANARY_MODE") == "repair_systemd_dual_service":
    from app.runtime.repair_execution_service import (
        RuntimeRepairExecutionService,
        RuntimeRepairResult,
    )
    from app.runtime.trade_execution_service import (
        RuntimeExecutionResult,
        RuntimeTradeExecutionService,
    )

    async def _fake_trade_run_task(
        self,
        *,
        exchanges: list[str],
        credentials_by_exchange: dict,
        execution_accounts_by_exchange: dict | None = None,
        symbol: str,
        target_quote_amount: float = 15.0,
        env_mode: str = "testnet",
        proxies_by_exchange: dict | None = None,
    ) -> RuntimeExecutionResult:
        _ = (
            self,
            credentials_by_exchange,
            execution_accounts_by_exchange,
            symbol,
            target_quote_amount,
            env_mode,
            proxies_by_exchange,
        )
        buy_exchange = str(exchanges[0])
        sell_exchange = str(exchanges[1])
        return RuntimeExecutionResult(
            ok=False,
            execution_status="OPEN_PARTIAL",
            filled_exchanges=[buy_exchange],
            failed_exchanges=[sell_exchange],
        )

    async def _fake_repair_run_task(
        self,
        *,
        task_uuid: str,
        symbol: str,
        buy_exchange: str,
        sell_exchange: str,
        target_exchanges: list[str],
        credentials_by_exchange: dict,
        target_quote_amount: float = 15.0,
        env_mode: str = "testnet",
        proxies_by_exchange: dict | None = None,
    ) -> RuntimeRepairResult:
        _ = (
            self,
            symbol,
            buy_exchange,
            sell_exchange,
            credentials_by_exchange,
            target_quote_amount,
            env_mode,
            proxies_by_exchange,
        )
        mode = os.getenv("FURUN_CANARY_REPAIR_RESULT", "success").strip().lower()
        if mode == "failure":
            return RuntimeRepairResult(
                ok=False,
                status="MANUAL_REQUIRED",
                task_uuid=task_uuid,
                target_exchanges=list(target_exchanges),
                repaired_exchanges=[],
                remaining_failed_exchanges=list(target_exchanges),
                reason="repair_canary_forced_failure",
            )
        return RuntimeRepairResult(
            ok=True,
            status="REPAIRED",
            task_uuid=task_uuid,
            target_exchanges=list(target_exchanges),
            repaired_exchanges=list(target_exchanges),
            remaining_failed_exchanges=[],
            reason=None,
        )

    RuntimeTradeExecutionService.run_task = _fake_trade_run_task
    RuntimeRepairExecutionService.run_task = _fake_repair_run_task
```

- [ ] **Step 2: Run a syntax check**

Run:

```bash
python -m py_compile .tmp-ssh/repair_systemd_dual_service_sitecustomize.py
```

Expected: PASS with no output.

- [ ] **Step 3: Run a local import smoke check**

Run:

```bash
python -c "import os, importlib.util; os.environ['FURUN_CANARY_MODE']='repair_systemd_dual_service'; spec = importlib.util.spec_from_file_location('repair_sitecustomize', r'd:\old\FuRunSystemV4\.tmp-ssh\repair_systemd_dual_service_sitecustomize.py'); module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); from app.runtime.trade_execution_service import RuntimeTradeExecutionService; from app.runtime.repair_execution_service import RuntimeRepairExecutionService; print(RuntimeTradeExecutionService.run_task.__name__); print(RuntimeRepairExecutionService.run_task.__name__)"
```

Expected:

```text
_fake_trade_run_task
_fake_repair_run_task
```

- [ ] **Step 4: Confirm the local asset stays ignored**

Run:

```bash
git status --short --ignored .tmp-ssh
```

Expected: the new `.tmp-ssh/repair_systemd_dual_service_sitecustomize.py` appears as ignored and is not staged.

## Task 2: Add The Remote Sync-And-Validate Script

**Files:**
- Create: `d:\old\FuRunSystemV4\.tmp-ssh\sync_and_validate_repair_systemd_dual_service.py`

- [ ] **Step 1: Write the sync-and-validate script**

Create `d:\old\FuRunSystemV4\.tmp-ssh\sync_and_validate_repair_systemd_dual_service.py` with this content:

```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(r"d:\old\FuRunSystemV4")
TMP_DIR = PROJECT_ROOT / ".tmp-ssh"
KEY_PATH = TMP_DIR / "futunsystemv3_deploy_ed25519"
SSH_EXE = r"C:\Windows\System32\OpenSSH\ssh.exe"
SCP_EXE = r"C:\Windows\System32\OpenSSH\scp.exe"
REMOTE_HOST = "ubuntu@43.165.166.57"
REMOTE_ROOT = "/home/ubuntu/furunsystemv4/current"
REMOTE_SITECUSTOMIZE = f"{REMOTE_ROOT}/sitecustomize.py"
REMOTE_ENV = f"{REMOTE_ROOT}/.env.worker"
REMOTE_ENV_BACKUP = f"{REMOTE_ROOT}/.env.worker.repair-systemd-canary.bak"
OUTPUT_PATH = TMP_DIR / "repair_systemd_dual_service_output.json"
CANARY_TASK_UUID = "repair-systemd-canary-1"
CANARY_SOURCE_ID = "repair-systemd-src-1"


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
            REMOTE_HOST,
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
            f"{REMOTE_HOST}:{remote_path}",
        ]
    )


def quote_for_single_quotes(value: str) -> str:
    return value.replace("'", "'\"'\"'")


def remote_bash(script: str) -> str:
    safe = quote_for_single_quotes(script)
    return ssh(f"bash -lc '{safe}'")


def main() -> int:
    local_sitecustomize = TMP_DIR / "repair_systemd_dual_service_sitecustomize.py"
    result: dict[str, object] = {}

    try:
        scp_to_remote(local_sitecustomize, REMOTE_SITECUSTOMIZE)

        remote_bash(
            f"""
set -euo pipefail
cd {REMOTE_ROOT}
cp .env.worker .env.worker.repair-systemd-canary.bak
python3 - <<'PY'
from pathlib import Path
env_path = Path("{REMOTE_ENV}")
lines = env_path.read_text(encoding="utf-8").splitlines()
lines = [line for line in lines if not line.startswith("FURUN_CANARY_MODE=")]
lines = [line for line in lines if not line.startswith("FURUN_CANARY_REPAIR_RESULT=")]
lines.append("FURUN_CANARY_MODE=repair_systemd_dual_service")
lines.append("FURUN_CANARY_REPAIR_RESULT=success")
env_path.write_text("\\n".join(lines) + "\\n", encoding="utf-8")
PY
sudo systemctl restart furun-spot-executor.service
sudo systemctl restart furun-spot-repair.service
sleep 3
"""
        )

        active_executor = remote_bash(
            "sudo systemctl is-active furun-spot-executor.service"
        )
        active_repair = remote_bash(
            "sudo systemctl is-active furun-spot-repair.service"
        )
        baseline_exec_len = remote_bash("redis-cli XLEN stream:spot_exec_tasks:main")
        baseline_repair_len = remote_bash("redis-cli XLEN stream:repair_tasks:main")

        remote_bash(
            f"""
redis-cli XADD stream:spot_exec_tasks:main '*' \
task_uuid {CANARY_TASK_UUID} \
user_id 42 \
source_message_id {CANARY_SOURCE_ID} \
symbol BTC/USDT \
buy_exchange okx \
sell_exchange gate \
target_quote_amount 15.0
sleep 5
"""
        )

        latest_repair_entries = remote_bash(
            "redis-cli XREVRANGE stream:repair_tasks:main + - COUNT 5"
        )
        executor_logs = remote_bash(
            "sudo journalctl -u furun-spot-executor.service -n 120 --no-pager | grep -E 'executor\\\\.repair_planned|repair-systemd-canary-1' || true"
        )
        repair_logs = remote_bash(
            "sudo journalctl -u furun-spot-repair.service -n 120 --no-pager | grep -E 'repair\\\\.task\\\\.finished|repair-systemd-canary-1' || true"
        )
        final_exec_len = remote_bash("redis-cli XLEN stream:spot_exec_tasks:main")
        final_repair_len = remote_bash("redis-cli XLEN stream:repair_tasks:main")

        result = {
            "active_executor": active_executor,
            "active_repair": active_repair,
            "baseline_exec_len": baseline_exec_len,
            "baseline_repair_len": baseline_repair_len,
            "final_exec_len": final_exec_len,
            "final_repair_len": final_repair_len,
            "latest_repair_entries": latest_repair_entries,
            "executor_logs": executor_logs,
            "repair_logs": repair_logs,
            "canary_task_uuid": CANARY_TASK_UUID,
            "canary_source_message_id": CANARY_SOURCE_ID,
        }
        OUTPUT_PATH.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        remote_bash(
            f"""
set +e
cd {REMOTE_ROOT}
if [ -f .env.worker.repair-systemd-canary.bak ]; then
  mv .env.worker.repair-systemd-canary.bak .env.worker
fi
rm -f sitecustomize.py
sudo systemctl restart furun-spot-executor.service
sudo systemctl restart furun-spot-repair.service
"""
        )


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run a syntax check**

Run:

```bash
python -m py_compile .tmp-ssh/sync_and_validate_repair_systemd_dual_service.py
```

Expected: PASS with no output.

- [ ] **Step 3: Execute the remote validation**

Run:

```bash
python .tmp-ssh/sync_and_validate_repair_systemd_dual_service.py
```

Expected: print one JSON object containing at least these fields:

```json
{
  "active_executor": "active",
  "active_repair": "active",
  "baseline_exec_len": "0",
  "baseline_repair_len": "0",
  "final_exec_len": "1",
  "final_repair_len": "1",
  "canary_task_uuid": "repair-systemd-canary-1"
}
```

And the log/evidence fields should contain:

- `executor_logs` includes `executor.repair_planned` or `repair-systemd-canary-1`
- `repair_logs` includes `repair.task.finished` or `repair-systemd-canary-1`
- `latest_repair_entries` includes `task_uuid` plus `repair_action`

- [ ] **Step 4: Verify local JSON capture**

Run:

```bash
python -c "from pathlib import Path; path = Path(r'd:\old\FuRunSystemV4\.tmp-ssh\repair_systemd_dual_service_output.json'); print(path.exists()); print('repair-systemd-canary-1' in path.read_text(encoding='utf-8'))"
```

Expected:

```text
True
True
```

- [ ] **Step 5: Confirm the cleanup ran**

Run:

```bash
python -c "import subprocess; cmd = [r'C:\Windows\System32\OpenSSH\ssh.exe', '-i', r'd:\old\FuRunSystemV4\.tmp-ssh\futunsystemv3_deploy_ed25519', '-o', 'StrictHostKeyChecking=accept-new', 'ubuntu@43.165.166.57', \"bash -lc 'cd /home/ubuntu/furunsystemv4/current && test ! -f sitecustomize.py && ! grep -q ^FURUN_CANARY_MODE= .env.worker && ! grep -q ^FURUN_CANARY_REPAIR_RESULT= .env.worker && echo clean'\"]; print(subprocess.run(cmd, text=True, capture_output=True, check=False).stdout.strip())"
```

Expected:

```text
clean
```

## Task 3: Record The Remote Result In Ops Documentation

**Files:**
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: Add the validation section**

Append a section like this after the existing repair deployment guidance and before unrelated later validation notes:

```md
### Repair Worker Systemd Dual-Service Validation

`repair worker` 的这轮远端验收走主服务器真实 systemd 双服务联调：

- `furun-spot-executor.service`
- `furun-spot-repair.service`

本次没有触发真实交易所下单，而是临时上传 `sitecustomize.py` 到远端项目根目录，并在
`.env.worker` 中短时打开：

- `FURUN_CANARY_MODE=repair_systemd_dual_service`
- `FURUN_CANARY_REPAIR_RESULT=success`

这样 `executor` 会稳定返回 `OPEN_PARTIAL`，`repair` 会稳定返回 `REPAIRED`，用于验证
真实 systemd 服务之间的 `executor -> repair` 联动；验证完成后会恢复 `.env.worker` 并删除
远端 `sitecustomize.py`。

本次 helper 路径：

- `.tmp-ssh/repair_systemd_dual_service_sitecustomize.py`
- `.tmp-ssh/sync_and_validate_repair_systemd_dual_service.py`
- `.tmp-ssh/repair_systemd_dual_service_output.json`

主服务器实测记录（2026-05-26）：

- `furun-spot-executor.service` 返回 `active`
- `furun-spot-repair.service` 返回 `active`
- 注入 canary `repair-systemd-canary-1`
- `executor_logs` 中可见 `executor.repair_planned`
- `repair_logs` 中可见 `repair.task.finished`
- `stream:repair_tasks:main` 最新 entries 中可见该 canary 的 repair message

本次验证结论：

- 主服务器真实 systemd 形态下，最小 `executor -> repair` 双服务联动已通过
- 本次使用的是临时 canary monkeypatch，不代表真实交易所路径已完成生产联调
- 清理步骤必须保留：恢复 `.env.worker`、删除远端 `sitecustomize.py`、重启两个服务
```

- [ ] **Step 2: Run the focused verification set**

Run:

```bash
python -m py_compile .tmp-ssh/repair_systemd_dual_service_sitecustomize.py .tmp-ssh/sync_and_validate_repair_systemd_dual_service.py
git status --short
```

Expected:

- `.tmp-ssh` 文件继续保持本地 ignored 资产
- `docs/ops/live-workers-systemd.md` 是唯一需要提交的 tracked 改动

- [ ] **Step 3: Commit the ops documentation update**

```bash
git add docs/ops/live-workers-systemd.md
git commit -m "docs: record repair systemd dual-service validation"
```

## Task 4: Final Handoff Verification

**Files:**
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: Check the working tree and latest commits**

Run:

```bash
git status -sb
git log -3 --oneline
```

Expected:

- 最新提交是 `docs: record repair systemd dual-service validation`
- 工作树保持干净
- `.tmp-ssh` 资产和本地 JSON 继续不进入 Git

- [ ] **Step 2: Summarize evidence for handoff**

Record these concrete items in the implementation handoff:

```text
- systemctl: executor=active, repair=active
- canary task_uuid: repair-systemd-canary-1
- executor evidence: executor.repair_planned
- repair evidence: repair.task.finished
- Redis evidence: latest stream:repair_tasks:main contains the canary repair payload
- cleanup evidence: remote sitecustomize.py removed and FURUN_CANARY_* env keys cleared
```
