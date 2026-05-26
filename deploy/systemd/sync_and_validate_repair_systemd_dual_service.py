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
REMOTE_VENV_PYTHON = f"{REMOTE_ROOT}/.venv/bin/python"
REMOTE_SITECUSTOMIZE = f"{REMOTE_ROOT}/sitecustomize.py"
REMOTE_ENV = f"{REMOTE_ROOT}/.env.worker"
REMOTE_ENV_BACKUP = f"{REMOTE_ROOT}/.env.worker.repair-systemd-canary.bak"
OUTPUT_PATH = TMP_DIR / "repair_systemd_dual_service_output.json"
CANARY_TASK_UUID = "repair-systemd-canary-1"
CANARY_SOURCE_ID = "repair-systemd-src-1"
CANARY_BUY_ACCOUNT_ID = 37
CANARY_SELL_ACCOUNT_ID = 39
CANARY_EXECUTOR_STREAM = "stream:spot_exec_tasks:repair-canary"
CANARY_REPAIR_STREAM = "stream:repair_tasks:repair-canary"
FILES_TO_SYNC = [
    "app/runtime/worker_service.py",
    "app/runtime/worker_config.py",
    "app/runtime/live_workers.py",
    "app/runtime/redis_flow.py",
    "app/runtime/trade_execution_service.py",
    "app/runtime/repair_execution_service.py",
    "app/runtime/runtime_events.py",
    "app/db/task_repository.py",
]
EXECUTOR_UNIT_NAME = "furun-spot-executor.service"
EXECUTOR_UNIT_PATH = f"/etc/systemd/system/{EXECUTOR_UNIT_NAME}"
EXECUTOR_UNIT_CONTENT = """[Unit]
Description=FuRun spot executor worker
After=network.target redis.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/furunsystemv4/current
Environment=PYTHONPATH=/home/ubuntu/furunsystemv4/current
EnvironmentFile=/home/ubuntu/furunsystemv4/current/.env.worker
ExecStart=/home/ubuntu/furunsystemv4/current/.venv/bin/python -m app.runtime.worker_service --role executor
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""
REPAIR_UNIT_NAME = "furun-spot-repair.service"
REPAIR_UNIT_PATH = f"/etc/systemd/system/{REPAIR_UNIT_NAME}"
REPAIR_UNIT_CONTENT = """[Unit]
Description=FuRun spot repair worker
After=network.target redis.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/furunsystemv4/current
Environment=PYTHONPATH=/home/ubuntu/furunsystemv4/current
EnvironmentFile=/home/ubuntu/furunsystemv4/current/.env.worker
ExecStart=/home/ubuntu/furunsystemv4/current/.venv/bin/python -m app.runtime.worker_service --role repair
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""


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


def run_no_check(cmd: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(cmd, text=True, capture_output=True, check=False)
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


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


def sync_remote_files(relative_paths: list[str]) -> list[str]:
    synced: list[str] = []
    remote_bash(
        f"""
set -euo pipefail
mkdir -p {REMOTE_ROOT}/app/runtime {REMOTE_ROOT}/app/db
"""
    )
    for relative_path in relative_paths:
        local_path = PROJECT_ROOT / relative_path
        remote_relative_path = relative_path.replace("\\", "/")
        remote_path = f"{REMOTE_ROOT}/{remote_relative_path}"
        scp_to_remote(local_path, remote_path)
        synced.append(remote_relative_path)
    return synced


def quote_for_single_quotes(value: str) -> str:
    return value.replace("'", "'\"'\"'")


def remote_bash(script: str) -> str:
    safe = quote_for_single_quotes(script)
    return ssh(f"bash -lc '{safe}'")


def remote_bash_no_check(script: str) -> tuple[int, str, str]:
    safe = quote_for_single_quotes(script)
    return run_no_check(
        [
            SSH_EXE,
            "-i",
            str(KEY_PATH),
            "-o",
            "StrictHostKeyChecking=accept-new",
            REMOTE_HOST,
            f"bash -lc '{safe}'",
        ]
    )


def wait_for_active_service(unit_name: str, *, attempts: int = 15, sleep_seconds: int = 2) -> str:
    returncode, stdout, stderr = remote_bash_no_check(
        f"""
set -euo pipefail
state=""
for _ in $(seq 1 {attempts}); do
  state="$(sudo systemctl is-active {unit_name} || true)"
  if [ "$state" = "active" ]; then
    echo "$state"
    exit 0
  fi
  sleep {sleep_seconds}
done
echo "$state"
"""
    )
    state = stdout.strip()
    if state != "active":
        raise RuntimeError(
            f"{unit_name} did not become active\n"
            f"returncode: {returncode}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )
    return state


def preseed_remote_canary_task() -> dict[str, object]:
    output = remote_bash(
        f"""
set -euo pipefail
cd {REMOTE_ROOT}
PYTHONPATH={REMOTE_ROOT} {REMOTE_VENV_PYTHON} - <<'PY'
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete

from app.db.session import build_session_factory
from models import ArbitrageTask

env = {{}}
for raw in Path("{REMOTE_ENV}").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    env[key.strip()] = value.strip()

database_url = env["DATABASE_URL"]
session_factory = build_session_factory(database_url)
with session_factory() as session:
    session.execute(
        delete(ArbitrageTask).where(ArbitrageTask.task_uuid == "{CANARY_TASK_UUID}")
    )
    task = ArbitrageTask(
        task_uuid="{CANARY_TASK_UUID}",
        user_id=42,
        strategy_config_id=None,
        buy_account_id={CANARY_BUY_ACCOUNT_ID},
        sell_account_id={CANARY_SELL_ACCOUNT_ID},
        opportunity_id="repair-systemd-opportunity-{CANARY_TASK_UUID}",
        env_mode="testnet",
        task_type="open",
        symbol="BTC/USDT",
        spot_exchange="bitget",
        derivative_exchange="gate",
        target_notional=15.0,
        expected_spread_bps=25.0,
        expected_funding_bps=0.0,
        status="DISPATCHED",
        status_reason=None,
        idempotency_key="idem-{CANARY_TASK_UUID}",
        home_region="main",
        worker_node_id="main",
        dispatched_at=datetime.now(timezone.utc),
    )
    session.add(task)
    session.commit()
    print(
        json.dumps(
            {{
                "task_uuid": task.task_uuid,
                "status": task.status,
                "worker_node_id": task.worker_node_id,
            }},
            ensure_ascii=False,
        )
    )
PY
"""
    )
    return json.loads(output)


def wait_for_remote_canary_task(timeout_seconds: int = 30) -> dict[str, object]:
    output = remote_bash(
        f"""
set -euo pipefail
cd {REMOTE_ROOT}
PYTHONPATH={REMOTE_ROOT} {REMOTE_VENV_PYTHON} - <<'PY'
import json
import time
from pathlib import Path

from sqlalchemy import select

from app.db.session import build_session_factory
from models import ArbitrageTask

env = {{}}
for raw in Path("{REMOTE_ENV}").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    env[key.strip()] = value.strip()

database_url = env["DATABASE_URL"]
session_factory = build_session_factory(database_url)
deadline = time.time() + {timeout_seconds}
result = None
with session_factory() as session:
    while time.time() < deadline:
        task = session.scalar(
            select(ArbitrageTask).where(ArbitrageTask.task_uuid == "{CANARY_TASK_UUID}")
        )
        if task is not None:
            session.refresh(task)
            result = {{
                "task_uuid": task.task_uuid,
                "status": task.status,
                "status_reason": task.status_reason,
                "execution_status": task.execution_status,
                "repair_action": task.repair_action,
                "repair_reason": task.repair_reason,
                "worker_node_id": task.worker_node_id,
                "filled_exchanges_json": task.filled_exchanges_json,
                "failed_exchanges_json": task.failed_exchanges_json,
            }}
            if task.status in {{"FAILED", "SUCCEEDED", "BLOCKED"}}:
                break
        time.sleep(1)
    if result is None:
        result = {{"task_uuid": "{CANARY_TASK_UUID}", "status": "missing"}}
print(json.dumps(result, ensure_ascii=False))
PY
"""
    )
    return json.loads(output)


def delete_remote_canary_task() -> None:
    remote_bash(
        f"""
set +e
cd {REMOTE_ROOT}
PYTHONPATH={REMOTE_ROOT} {REMOTE_VENV_PYTHON} - <<'PY'
from pathlib import Path

from sqlalchemy import delete

from app.db.session import build_session_factory
from models import ArbitrageTask

env = {{}}
for raw in Path("{REMOTE_ENV}").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    env[key.strip()] = value.strip()

database_url = env.get("DATABASE_URL")
if database_url:
    session_factory = build_session_factory(database_url)
    with session_factory() as session:
        session.execute(
            delete(ArbitrageTask).where(ArbitrageTask.task_uuid == "{CANARY_TASK_UUID}")
        )
        session.commit()
PY
"""
    )


def ensure_remote_unit(
    *,
    unit_name: str,
    unit_path: str,
    unit_content: str,
) -> str:
    return remote_bash(
        f"""
set -euo pipefail
cat > /tmp/{unit_name} <<'UNIT'
{unit_content}UNIT
sudo cp /tmp/{unit_name} {unit_path}
sudo chmod 644 {unit_path}
sudo systemctl daemon-reload
sudo systemctl enable {unit_name} >/dev/null 2>&1 || true
echo synced
"""
    )


def main() -> int:
    local_sitecustomize = TMP_DIR / "repair_systemd_dual_service_sitecustomize.py"
    result: dict[str, object] = {}

    try:
        synced_files = sync_remote_files(FILES_TO_SYNC)
        scp_to_remote(local_sitecustomize, REMOTE_SITECUSTOMIZE)
        executor_service_install_status = ensure_remote_unit(
            unit_name=EXECUTOR_UNIT_NAME,
            unit_path=EXECUTOR_UNIT_PATH,
            unit_content=EXECUTOR_UNIT_CONTENT,
        )
        repair_service_install_status = ensure_remote_unit(
            unit_name=REPAIR_UNIT_NAME,
            unit_path=REPAIR_UNIT_PATH,
            unit_content=REPAIR_UNIT_CONTENT,
        )
        preseed_task = preseed_remote_canary_task()

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
lines = [line for line in lines if not line.startswith("EXECUTOR_STREAM_KEY=")]
lines = [line for line in lines if not line.startswith("REPAIR_STREAM_KEY=")]
lines.append("FURUN_CANARY_MODE=repair_systemd_dual_service")
lines.append("FURUN_CANARY_REPAIR_RESULT=success")
lines.append("EXECUTOR_STREAM_KEY=stream:spot_exec_tasks:repair-canary")
lines.append("REPAIR_STREAM_KEY=stream:repair_tasks:repair-canary")
env_path.write_text("\\n".join(lines) + "\\n", encoding="utf-8")
PY
sudo systemctl restart furun-spot-executor.service
sudo systemctl restart furun-spot-repair.service
sleep 3
"""
        )

        active_executor = wait_for_active_service(
            "furun-spot-executor.service"
        )
        active_repair = wait_for_active_service("furun-spot-repair.service")
        baseline_exec_len = remote_bash("redis-cli XLEN stream:spot_exec_tasks:main")
        baseline_repair_len = remote_bash("redis-cli XLEN stream:repair_tasks:main")

        remote_bash(
            f"""
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
sleep 5
"""
        )
        final_task = wait_for_remote_canary_task()

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
            "synced_files": synced_files,
            "executor_service_install_status": executor_service_install_status,
            "repair_service_install_status": repair_service_install_status,
            "preseed_task": preseed_task,
            "active_executor": active_executor,
            "active_repair": active_repair,
            "baseline_exec_len": baseline_exec_len,
            "baseline_repair_len": baseline_repair_len,
            "final_exec_len": final_exec_len,
            "final_repair_len": final_repair_len,
            "final_task": final_task,
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
        delete_remote_canary_task()
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
