# Repair Worker Systemd Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the minimal systemd deployment assets so the repair worker can be installed and run like the existing executor worker, with matching env guidance and ops documentation.

**Architecture:** Keep the change strictly in deployment assets and documentation. First add a new `furun-spot-repair.service` by mirroring the executor service and only changing the role; then update `.env.worker.example` and `live-workers-systemd.md` so repair uses the same config model and deployment steps as the rest of the runtime; finally run focused static checks and confirm the working tree before handoff.

**Tech Stack:** systemd unit files, existing `.env.worker` settings model, Markdown ops documentation

---

## File Structure

- Create: `d:\old\FuRunSystemV4\deploy\systemd\furun-spot-repair.service`
  - Mirror the executor service and run `python -m app.runtime.worker_service --role repair`
- Modify: `d:\old\FuRunSystemV4\deploy\systemd\.env.worker.example`
  - Add repair role usage guidance while keeping the shared env model
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
  - Add repair service to the file list, topology, install/restart commands, and minimal acceptance checks

## Task 1: Add The Repair Systemd Service Asset

**Files:**
- Create: `d:\old\FuRunSystemV4\deploy\systemd\furun-spot-repair.service`

- [ ] **Step 1: Write the new repair service file**

Create `deploy/systemd/furun-spot-repair.service` with these contents:

```ini
[Unit]
Description=FuRun spot repair worker
After=network.target redis.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/furunsystemv4/current
EnvironmentFile=/home/ubuntu/furunsystemv4/current/.env.worker
ExecStart=/home/ubuntu/furunsystemv4/current/.venv/bin/python -m app.runtime.worker_service --role repair
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Run a focused file-content check**

Run:

```bash
python -c "from pathlib import Path; text = Path(r'd:\old\FuRunSystemV4\deploy\systemd\furun-spot-repair.service').read_text(encoding='utf-8'); print('worker_service --role repair' in text); print('EnvironmentFile=/home/ubuntu/furunsystemv4/current/.env.worker' in text)"
```

Expected: print `True` twice.

- [ ] **Step 3: Compare it against the executor service shape**

Run:

```bash
python -c "from pathlib import Path; repair = Path(r'd:\old\FuRunSystemV4\deploy\systemd\furun-spot-repair.service').read_text(encoding='utf-8').splitlines(); executor = Path(r'd:\old\FuRunSystemV4\deploy\systemd\furun-spot-executor.service').read_text(encoding='utf-8').splitlines(); print(len(repair)); print(len(executor))"
```

Expected: both files should have matching section structure and comparable line counts.

- [ ] **Step 4: Commit the new service asset**

```bash
git add deploy/systemd/furun-spot-repair.service
git commit -m "deploy: add repair worker systemd service"
```

## Task 2: Update Shared Env Guidance And Ops Documentation

**Files:**
- Modify: `d:\old\FuRunSystemV4\deploy\systemd\.env.worker.example`
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: Update the shared env example**

Add a short repair usage comment near the worker role and stream settings in `deploy/systemd/.env.worker.example`:

```dotenv
WORKER_ROLE=scanner
# Set WORKER_ROLE=repair on repair nodes or repair services.
WORKER_REGION=main
NODE_ID=main
DISPATCH_USER_IDS=42,99
USER_NODE_ROUTES=42:node-a,99:main
DISPATCH_SOURCE_STREAM=stream:spot_opps
EXECUTOR_STREAM_KEY=stream:spot_exec_tasks:main
REPAIR_STREAM_KEY=stream:repair_tasks:main
```

Keep the rest of the file unchanged.

- [ ] **Step 2: Update the ops document file list and topology**

Modify `docs/ops/live-workers-systemd.md` so these sections include repair:

```md
## Files

- `deploy/systemd/furun-spot-scanner.service`
- `deploy/systemd/furun-spot-consumer.service`
- `deploy/systemd/furun-spot-dispatcher.service`
- `deploy/systemd/furun-spot-executor.service`
- `deploy/systemd/furun-spot-repair.service`
- `deploy/systemd/furun-route-admin.service`
- `deploy/systemd/furun-control-admin.service`
- `deploy/systemd/.env.worker.example`
```

```md
## Topology

- 主服务器运行 `furun-spot-scanner.service` 与 `furun-spot-dispatcher.service`
- 主服务器可同时运行 `furun-route-admin.service` 与 `furun-control-admin.service`
- 专用执行节点运行 `furun-spot-executor.service`
- repair 节点或同类执行节点运行 `furun-spot-repair.service`
- 迁移期如需保留旧链路，可暂时继续运行 `furun-spot-consumer.service`，但目标形态是不再让专用执行节点消费公共 `stream:spot_opps`
```

- [ ] **Step 3: Update the env example block and install commands in the ops doc**

Adjust the generated `.env.worker` example block and install commands so repair is represented:

```dotenv
REDIS_URL=redis://127.0.0.1:6379/0
ENV_MODE=testnet
WORKER_ROLE=scanner
WORKER_REGION=main
NODE_ID=main
DISPATCH_USER_IDS=42,99
USER_NODE_ROUTES=42:node-a,99:main
DISPATCH_SOURCE_STREAM=stream:spot_opps
EXECUTOR_STREAM_KEY=stream:spot_exec_tasks:main
REPAIR_STREAM_KEY=stream:repair_tasks:main
```

And in the install steps:

```bash
sudo cp deploy/systemd/furun-spot-scanner.service /etc/systemd/system/
sudo cp deploy/systemd/furun-spot-consumer.service /etc/systemd/system/
sudo cp deploy/systemd/furun-spot-dispatcher.service /etc/systemd/system/
sudo cp deploy/systemd/furun-spot-executor.service /etc/systemd/system/
sudo cp deploy/systemd/furun-spot-repair.service /etc/systemd/system/
sudo cp deploy/systemd/furun-route-admin.service /etc/systemd/system/
sudo cp deploy/systemd/furun-control-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
```

- [ ] **Step 4: Add repair-specific enable/restart and validation guidance**

Add a focused repair subsection after the executor enable step:

```md
5. 在 repair 节点启用 `repair`：

```bash
sudo systemctl enable furun-spot-repair.service
sudo systemctl restart furun-spot-repair.service
```

6. 验证 repair 运行状态：

```bash
sudo systemctl is-active furun-spot-repair.service
sudo journalctl -u furun-spot-repair.service -n 50 --no-pager | grep '"event_type"'
redis-cli XLEN stream:repair_tasks:main
```

验收点：

- `furun-spot-repair.service` 返回 `active`
- `journalctl` 中可见 repair 角色结构化事件
- `stream:repair_tasks:<node_id>` 可作为 repair 输入流被观察
```

Renumber the surrounding sections if needed so the document reads naturally.

- [ ] **Step 5: Run a focused static validation**

Run:

```bash
python -c "from pathlib import Path; env_text = Path(r'd:\old\FuRunSystemV4\deploy\systemd\.env.worker.example').read_text(encoding='utf-8'); doc_text = Path(r'd:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md').read_text(encoding='utf-8'); print('REPAIR_STREAM_KEY=stream:repair_tasks:main' in env_text); print('furun-spot-repair.service' in doc_text); print('systemctl is-active furun-spot-repair.service' in doc_text)"
```

Expected: print `True` three times.

- [ ] **Step 6: Commit the env and doc updates**

```bash
git add deploy/systemd/.env.worker.example docs/ops/live-workers-systemd.md
git commit -m "docs: add repair worker systemd deployment guidance"
```

## Task 3: Final Static Checks And Handoff State

**Files:**
- Create: `d:\old\FuRunSystemV4\deploy\systemd\furun-spot-repair.service`
- Modify: `d:\old\FuRunSystemV4\deploy\systemd\.env.worker.example`
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: Run the final static check set**

Run:

```bash
python -c "from pathlib import Path; repair = Path(r'd:\old\FuRunSystemV4\deploy\systemd\furun-spot-repair.service').read_text(encoding='utf-8'); env_text = Path(r'd:\old\FuRunSystemV4\deploy\systemd\.env.worker.example').read_text(encoding='utf-8'); doc_text = Path(r'd:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md').read_text(encoding='utf-8'); print('--role repair' in repair); print('REPAIR_STREAM_KEY=stream:repair_tasks:main' in env_text); print('furun-spot-repair.service' in doc_text)"
git diff --check
```

Expected: the Python command prints `True` three times, and `git diff --check` reports no whitespace errors.

- [ ] **Step 2: Check the working tree and recent commits**

Run:

```bash
git status --short
git log -4 --oneline
```

Expected: either a clean tree after the planned commits, or only the intended deployment/doc files if a follow-up fix is still pending.

- [ ] **Step 3: If a real follow-up fix was needed, commit it**

```bash
git add deploy/systemd/furun-spot-repair.service deploy/systemd/.env.worker.example docs/ops/live-workers-systemd.md
git commit -m "docs: finalize repair worker systemd deployment assets"
```

Expected: skip this commit if no follow-up fix was needed.
