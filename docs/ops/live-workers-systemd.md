# Live Workers Systemd Deployment

## Files

- `deploy/systemd/furun-spot-scanner.service`
- `deploy/systemd/furun-spot-consumer.service`
- `deploy/systemd/furun-spot-dispatcher.service`
- `deploy/systemd/furun-spot-executor.service`
- `deploy/systemd/furun-route-admin.service`
- `deploy/systemd/furun-control-admin.service`
- `deploy/systemd/.env.worker.example`

## Alert Config

- `ALERTS_ENABLED=1` keeps structured logging on and enables alert routing.
- `ALERT_FEISHU_ENABLED=1` and `ALERT_FEISHU_WEBHOOK` enable Feishu notifications with Chinese text.
- `ALERT_EMAIL_ENABLED=1` plus `ALERT_EMAIL_SMTP_*` and `ALERT_EMAIL_TO` enable QQ email delivery with Chinese subject/body for `CRITICAL` events.
- `consumer.message.processed` no longer sends external notifications.
- `ALERT_SUCCESS_SPREAD_BPS_THRESHOLD` controls the threshold Feishu uses for `opportunity.detected`; only values strictly above it send success notifications, and the default tuned value is `20`.
- `ALERT_DEDUPE_WINDOW_SECONDS` controls Feishu dedupe for repeated `ERROR` events; the default tuned value is `300`.
- Exchange credentials still come from `OKX_*`, `BITGET_*`, and `GATE_*`; a missing required key should raise `worker.start_failed` and fan out to Feishu plus QQ email.
- Spot scanner whitelist and depth controls now come from `SPOT_SYMBOLS`, `ORDERBOOK_DEPTH_LIMIT`, and `TARGET_QUOTE_AMOUNT`; `SPOT_SYMBOL` remains the single-symbol fallback when `SPOT_SYMBOLS` is empty.
- Node roles now come from `WORKER_ROLE`, `WORKER_REGION`, `NODE_ID`, `DISPATCH_USER_IDS`, `USER_NODE_ROUTES`, `DISPATCH_SOURCE_STREAM`, and `EXECUTOR_STREAM_KEY`.
- `dispatcher` 启动时会把 `USER_NODE_ROUTES` 自动同步到 Redis `route:user_node:{user_id}`，不再依赖手工逐条执行 `redis-cli set`.
- Route admin 配置来自 `ROUTE_ADMIN_ENABLED`、`ROUTE_ADMIN_BIND_HOST`、`ROUTE_ADMIN_PORT`、`ROUTE_ADMIN_TOKEN`，建议默认只监听 `127.0.0.1`。
- Control admin 配置来自 `CONTROL_ADMIN_ENABLED`、`CONTROL_ADMIN_BIND_HOST`、`CONTROL_ADMIN_PORT`、`CONTROL_ADMIN_TOKEN`，建议默认只监听 `127.0.0.1`。

## Topology

- 主服务器运行 `furun-spot-scanner.service` 与 `furun-spot-dispatcher.service`
- 主服务器可同时运行 `furun-route-admin.service` 与 `furun-control-admin.service`
- 专用执行节点运行 `furun-spot-executor.service`
- 迁移期如需保留旧链路，可暂时继续运行 `furun-spot-consumer.service`，但目标形态是不再让专用执行节点消费公共 `stream:spot_opps`

## Windows Sync

从 Windows 开发机同步到远端时，不要把多个不同目录的文件一次性上传到
`/home/ubuntu/furunsystemv4/current/`，否则会丢失 `app/runtime`、
`deploy/systemd`、`docs/ops` 的目录结构。先准备 SSH key，再按目录分别同步。

```powershell
$keyDir = "d:\old\FuRunSystemV4\.tmp-ssh"
New-Item -ItemType Directory -Force -Path $keyDir | Out-Null
$keyPath = Join-Path $keyDir "futunsystemv3_deploy_ed25519"
Copy-Item -Force "d:\old\FuRunSystemV4\.keys\futunsystemv3_deploy_ed25519" $keyPath
& "C:\Windows\System32\icacls.exe" $keyPath /inheritance:r | Out-Null
& "C:\Windows\System32\icacls.exe" $keyPath /grant:r "${env:USERNAME}:(R)" | Out-Null
& "C:\Windows\System32\OpenSSH\ssh.exe" -o StrictHostKeyChecking=no -i $keyPath ubuntu@43.165.166.57 `
  "mkdir -p /home/ubuntu/furunsystemv4/current/app/runtime /home/ubuntu/furunsystemv4/current/app/exchanges /home/ubuntu/furunsystemv4/current/app/market /home/ubuntu/furunsystemv4/current/deploy/systemd /home/ubuntu/furunsystemv4/current/docs/ops"
```

同步 `app/runtime`：

```powershell
& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\app\runtime\worker_config.py" `
  "d:\old\FuRunSystemV4\app\runtime\redis_flow.py" `
  "d:\old\FuRunSystemV4\app\runtime\live_spot_flow.py" `
  "d:\old\FuRunSystemV4\app\runtime\live_workers.py" `
  "d:\old\FuRunSystemV4\app\runtime\control_admin_service.py" `
  "d:\old\FuRunSystemV4\app\runtime\route_admin_cli.py" `
  "d:\old\FuRunSystemV4\app\runtime\worker_service.py" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/runtime/
```

`adapters.py`、`opportunity.py` 和 control-plane 相关文件需要保持目录结构，单独上传：

```powershell
& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\app\exchanges\adapters.py" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/exchanges/

& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\app\market\opportunity.py" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/market/

& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\app\admin\control_plane.py" `
  "d:\old\FuRunSystemV4\app\admin\control_store.py" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/app/admin/
```

同步部署样例与文档：

```powershell
& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\deploy\systemd\.env.worker.example" `
  "d:\old\FuRunSystemV4\deploy\systemd\furun-control-admin.service" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/deploy/systemd/

& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/docs/ops/
```

## Remote Setup

1. Copy the example env file and fill in real credentials:

```bash
cd /home/ubuntu/furunsystemv4/current
cp deploy/systemd/.env.worker.example .env.worker
nano .env.worker
```

也可以在本地直接根据 `local-secrets` 生成 `.env.worker` 后上传到远端。需要读取：

- `local-secrets/飞书webhook地址.txt`
- `local-secrets/qq邮箱.txt`
- `local-secrets/五大交易所模拟盘apikey.txt`

本地生成建议至少包含以下键：

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
SPOT_SYMBOL=BTC/USDT
SPOT_SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT
SPOT_EXCHANGES=okx,bitget,gate
ORDERBOOK_DEPTH_LIMIT=5
TARGET_QUOTE_AMOUNT=100.0
SCANNER_POLL_INTERVAL_SECONDS=1.0
CONSUMER_BLOCK_MS=1000
CONTROL_ADMIN_ENABLED=1
CONTROL_ADMIN_BIND_HOST=127.0.0.1
CONTROL_ADMIN_PORT=8788
CONTROL_ADMIN_TOKEN=<control admin bearer token>
ALERTS_ENABLED=1
ALERT_FEISHU_ENABLED=1
ALERT_FEISHU_WEBHOOK=<飞书 webhook>
ALERT_EMAIL_ENABLED=1
ALERT_EMAIL_SMTP_HOST=smtp.qq.com
ALERT_EMAIL_SMTP_PORT=465
ALERT_EMAIL_USERNAME=<QQ 邮箱账号>
ALERT_EMAIL_PASSWORD=<QQ 邮箱 SMTP 授权码>
ALERT_EMAIL_TO=<接收邮箱，多个逗号分隔>
ALERT_SUCCESS_SPREAD_BPS_THRESHOLD=20
ALERT_DEDUPE_WINDOW_SECONDS=300
OKX_API_KEY=<模拟盘 key>
OKX_SECRET=<模拟盘 secret>
OKX_PASSWORD=<如有则填写>
BITGET_API_KEY=<模拟盘 key>
BITGET_SECRET=<模拟盘 secret>
BITGET_PASSWORD=<如有则填写>
GATE_API_KEY=<模拟盘 key>
GATE_SECRET=<模拟盘 secret>
GATE_PASSWORD=<如有则填写>
```

如果希望直接从现有 `local-secrets` 文本生成联调用 `.env.worker`，可以在 Windows 上执行：

```powershell
$keyDir = "d:\old\FuRunSystemV4\.tmp-ssh"
New-Item -ItemType Directory -Force -Path $keyDir | Out-Null
$envPath = Join-Path $keyDir "furun.alerts.env.worker"

$feishuWebhook = (Get-Content "d:\old\FuRunSystemV4\local-secrets\飞书webhook地址.txt" -Raw).Trim()
$qqSecretText = Get-Content "d:\old\FuRunSystemV4\local-secrets\qq邮箱.txt" -Raw
$exchangeText = Get-Content "d:\old\FuRunSystemV4\local-secrets\五大交易所模拟盘apikey.txt" -Raw

$qqUser = [regex]::Match($qqSecretText, "账户名\s*([^\s]+@[^\s]+)").Groups[1].Value
$qqPass = [regex]::Match($qqSecretText, "授权码\s*([A-Za-z0-9]+)").Groups[1].Value
$okxKey = [regex]::Match($exchangeText, 'apikey\s*=\s*"([^"]+)"').Groups[1].Value
$okxSecret = [regex]::Match($exchangeText, 'secretkey\s*=\s*"([^"]+)"').Groups[1].Value
$okxPassword = [regex]::Match($exchangeText, "密码\s+([^\r\n]+)").Groups[1].Value.Trim()
$bitgetKey = [regex]::Match($exchangeText, "API Key\\s*:\\s*([^\r\n]+)").Groups[1].Value.Trim()
$bitgetSecret = [regex]::Match($exchangeText, "API Secret\\s*:\\s*([^\r\n]+)").Groups[1].Value.Trim()
$bitgetPassword = [regex]::Match($exchangeText, "Passphrase\\s*:\\s*([^\r\n]+)").Groups[1].Value.Trim()
$gateKey = [regex]::Match($exchangeText, "gate[\s\S]*?Key\s+([A-Za-z0-9]+)").Groups[1].Value.Trim()
$gateSecret = [regex]::Match($exchangeText, "gate[\s\S]*?Secret\s+([A-Za-z0-9]+)").Groups[1].Value.Trim()

$envContent = @"
REDIS_URL=redis://127.0.0.1:6379/0
ENV_MODE=testnet
WORKER_ROLE=scanner
WORKER_REGION=main
NODE_ID=main
DISPATCH_USER_IDS=42,99
USER_NODE_ROUTES=42:node-a,99:main
DISPATCH_SOURCE_STREAM=stream:spot_opps
EXECUTOR_STREAM_KEY=stream:spot_exec_tasks:main
SPOT_SYMBOL=BTC/USDT
SPOT_SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT
SPOT_EXCHANGES=okx,bitget,gate
ORDERBOOK_DEPTH_LIMIT=5
TARGET_QUOTE_AMOUNT=100.0
SCANNER_POLL_INTERVAL_SECONDS=1.0
CONSUMER_BLOCK_MS=1000
ALERTS_ENABLED=1
ALERT_FEISHU_ENABLED=1
ALERT_FEISHU_WEBHOOK=$feishuWebhook
ALERT_EMAIL_ENABLED=1
ALERT_EMAIL_SMTP_HOST=smtp.qq.com
ALERT_EMAIL_SMTP_PORT=465
ALERT_EMAIL_USERNAME=$qqUser
ALERT_EMAIL_PASSWORD=$qqPass
ALERT_EMAIL_TO=$qqUser
ALERT_SUCCESS_SPREAD_BPS_THRESHOLD=20
ALERT_DEDUPE_WINDOW_SECONDS=300
OKX_API_KEY=$okxKey
OKX_SECRET=$okxSecret
OKX_PASSWORD=$okxPassword
BITGET_API_KEY=$bitgetKey
BITGET_SECRET=$bitgetSecret
BITGET_PASSWORD=$bitgetPassword
GATE_API_KEY=$gateKey
GATE_SECRET=$gateSecret
GATE_PASSWORD=
"@

[System.IO.File]::WriteAllText($envPath, $envContent, [System.Text.UTF8Encoding]::new($false))
$envPath
```

上传示例：

```powershell
& "C:\Windows\System32\OpenSSH\scp.exe" -o StrictHostKeyChecking=no -i $keyPath `
  "d:\old\FuRunSystemV4\.tmp-ssh\furun.alerts.env.worker" `
  ubuntu@43.165.166.57:/home/ubuntu/furunsystemv4/current/.env.worker
```

2. Install the unit files:

```bash
cd /home/ubuntu/furunsystemv4/current
chmod 600 .env.worker
sudo cp deploy/systemd/furun-spot-scanner.service /etc/systemd/system/
sudo cp deploy/systemd/furun-spot-consumer.service /etc/systemd/system/
sudo cp deploy/systemd/furun-spot-dispatcher.service /etc/systemd/system/
sudo cp deploy/systemd/furun-spot-executor.service /etc/systemd/system/
sudo cp deploy/systemd/furun-route-admin.service /etc/systemd/system/
sudo cp deploy/systemd/furun-control-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
```

3. 在主服务器启用 `scanner + dispatcher`：

```bash
sudo systemctl enable furun-spot-scanner.service
sudo systemctl enable furun-spot-dispatcher.service
sudo systemctl restart furun-spot-scanner.service
sudo systemctl restart furun-spot-dispatcher.service
```

4. 在专用执行节点启用 `executor`：

```bash
sudo systemctl disable furun-spot-scanner.service || true
sudo systemctl stop furun-spot-scanner.service || true
sudo systemctl enable furun-spot-executor.service
sudo systemctl restart furun-spot-executor.service
```

5. 重启前先核对角色与白名单参数：

```bash
cd /home/ubuntu/furunsystemv4/current
grep -E '^(WORKER_ROLE|NODE_ID|USER_NODE_ROUTES|DISPATCH_SOURCE_STREAM|EXECUTOR_STREAM_KEY|SPOT_SYMBOLS|ORDERBOOK_DEPTH_LIMIT|TARGET_QUOTE_AMOUNT)=' .env.worker
```

验收点：

- `WORKER_ROLE` 与当前节点目标服务一致
- `NODE_ID` 与当前节点一致，例如 `main` 或 `node-a`
- `USER_NODE_ROUTES` 已覆盖需要绑定到执行节点的用户，例如 `42:node-a,99:main`
- `DISPATCH_SOURCE_STREAM=stream:spot_opps`
- `EXECUTOR_STREAM_KEY=stream:spot_exec_tasks:<node_id>`
- `SPOT_SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT`
- `ORDERBOOK_DEPTH_LIMIT=5`
- `TARGET_QUOTE_AMOUNT=100.0`
- `CONTROL_ADMIN_ENABLED=1`
- `CONTROL_ADMIN_BIND_HOST=127.0.0.1`
- `CONTROL_ADMIN_PORT=8788`
- `CONTROL_ADMIN_TOKEN` 已设置为非空 Bearer Token

6. Validate runtime status, structured logs, and Redis progress:

```bash
sudo systemctl is-active furun-spot-scanner.service
sudo systemctl is-active furun-spot-dispatcher.service
sudo systemctl is-active furun-spot-executor.service
sudo systemctl is-active furun-control-admin.service
sudo journalctl -u furun-spot-scanner.service -n 50 --no-pager | grep '"event_type"'
sudo journalctl -u furun-spot-dispatcher.service -n 50 --no-pager | grep '"event_type"'
sudo journalctl -u furun-spot-executor.service -n 50 --no-pager | grep '"event_type"'
sudo journalctl -u furun-control-admin.service -n 30 --no-pager | grep 'control.admin'
redis-cli ZCARD arb:zset:spot
redis-cli XLEN stream:spot_opps
redis-cli XLEN stream:spot_exec_tasks:main
redis-cli XREVRANGE stream:spot_exec_tasks:main + - COUNT 5
redis-cli XREVRANGE stream:spot_opps + - COUNT 10
```

验收点：

- `furun-spot-scanner.service`、`furun-spot-dispatcher.service`、`furun-spot-executor.service` 都返回 `active`
- `furun-control-admin.service` 返回 `active`
- `journalctl` 中可见单行 JSON 事件，至少包含 `event_type`、`level`、`service`、`message`
- `redis-cli ZCARD arb:zset:spot` 与 `redis-cli XLEN stream:spot_opps` 均大于 0
- `redis-cli XLEN stream:spot_exec_tasks:<node_id>` 大于 0，且最新 task entry 包含 `user_id` 与 `source_message_id`
- 最新 `stream:spot_opps` entries 包含 `effective_buy_price`、`effective_sell_price`、`target_quote_amount`、`buy_depth_levels_used`、`sell_depth_levels_used`
- 近期 scanner 活动或最新 Redis entries 中至少出现两个白名单 symbol，例如 `BTC/USDT` 与 `ETH/USDT`
- 飞书成功通知文案为中文，且只会来自高于阈值的 `opportunity.detected`

### Database Task Model

启用数据库任务真值后，可在远端直接核对数据库状态与节点任务流是否一致：

```bash
cd /home/ubuntu/furunsystemv4/current
sqlite3 furun.db "select task_uuid, user_id, status, status_reason, worker_node_id from arbitrage_tasks order by id desc limit 10;"
redis-cli XREVRANGE stream:spot_exec_tasks:main + - COUNT 5
```

关注数据库任务状态：

- `CREATED`
- `DISPATCHED`
- `EXECUTING`
- `SUCCEEDED`
- `FAILED`
- `BLOCKED`

关注节点任务流字段：

- `task_uuid`
- `user_id`
- `source_message_id`

验收点：

- `arbitrage_tasks` 最新记录可看到 `task_uuid`、`user_id`、`status`、`worker_node_id`
- 已写入节点任务流的任务应至少进入 `DISPATCHED`
- 执行节点开始处理后应可推进到 `EXECUTING`
- 正常完成应推进到 `SUCCEEDED`，命中控制规则时应可看到 `BLOCKED` 和对应 `status_reason`
- 最新 `stream:spot_exec_tasks:<node_id>` entry 中可看到 `task_uuid`、`user_id`、`source_message_id`

### Executor DB Account Truth Validation

`executor` 现在会在真正进入交易所执行前，先按数据库账户真值解析买卖两边的
`ExchangeAccount`。如果账户缺失、区域不匹配、自动交易关闭、`market_type_scope`
不满足，或密文字段解密失败，应直接在 executor 侧失败退出，而不是继续进入
spot service。

1. 先确认执行节点启用数据库与正确环境：

```bash
cd /home/ubuntu/furunsystemv4/current
grep -E '^(DATABASE_URL|ENV_MODE|WORKER_ROLE|WORKER_REGION|NODE_ID|EXECUTOR_STREAM_KEY)=' .env.worker
sudo systemctl restart furun-spot-executor.service
sudo systemctl is-active furun-spot-executor.service
```

2. 为 canary 用户准备一条会在 executor 侧解析失败的账户真值：
   - 例如只保留 `bitget` 账户、不提供 `gate`
   - 或把目标交易所账户的 `is_auto_trade_enabled` 设为 `0`
   - 或把目标交易所账户的 `account_region` 改成与当前 executor 不兼容
   - 或制造密文不可解的账户字段，验证解密失败路径
3. 向当前节点任务流写入一条需要 `bitget/gate` 的执行任务：

```bash
redis-cli XADD stream:spot_exec_tasks:main '*' task_uuid canary-task-1 user_id 42 source_message_id canary-src-1 symbol BTC/USDT buy_exchange bitget sell_exchange gate target_quote_amount 15.0
```

4. 查看数据库任务状态与 executor 日志：

```bash
sqlite3 furun.db "select task_uuid, user_id, status, status_reason, worker_node_id from arbitrage_tasks where task_uuid='canary-task-1';"
sudo journalctl -u furun-spot-executor.service -n 120 --no-pager | grep 'executor.task'
```

5. 若要重点检查账户真值失败原因，再过滤 reason code：

```bash
sudo journalctl -u furun-spot-executor.service -n 120 --no-pager | grep 'executor_account_'
```

验收点：

- 任务不会推进到 `SUCCEEDED`
- `arbitrage_tasks.status` 应为 `FAILED`
- `status_reason` 应优先记录 reason code，而不是长文本 detail
- 缺少可执行账户时应看到 `executor_account_not_found`
- 密文字段解密失败时应看到 `executor_account_decrypt_failed`
- 失败后不应继续进入 spot service，因此不应出现对应任务的成功执行结果
- `furun-spot-executor.service` 仍保持 `active`，单条坏任务不会拖垮整个 executor 进程

### Task Account Binding Validation

账户绑定改造完成后，`dispatcher` 只负责选定 `buy_account_id` /
`sell_account_id`，`executor` 只按绑定账户执行；一旦绑定失效，应直接失败并保留
reason code，不允许静默重选账户。

1. 先为 canary 用户准备可执行的 `bitget` 与 `gate` 账户，并确认 dispatcher
   过滤链已经通过账户覆盖、自动交易、`market_type_scope` 与区域校验。
2. 注入一条专用机会，并同时检查数据库与节点任务流：

```bash
redis-cli XADD stream:spot_opps '*' symbol BTC/USDT buy_exchange bitget sell_exchange gate spread_bps 25.0 target_quote_amount 15.0
sqlite3 furun.db "select task_uuid, user_id, buy_account_id, sell_account_id, status from arbitrage_tasks order by id desc limit 10;"
redis-cli XREVRANGE stream:spot_exec_tasks:main + - COUNT 5
```

3. 重点确认以下字段已经落地：
   - `arbitrage_tasks.buy_account_id`
   - `arbitrage_tasks.sell_account_id`
   - 节点流 payload 中的 `buy_account_id`
   - 节点流 payload 中的 `sell_account_id`
4. 让 executor 正常消费该任务，确认任务成功执行，且没有重新选择其他账户：

```bash
sudo journalctl -u furun-spot-executor.service -n 120 --no-pager | grep 'executor.task'
sqlite3 furun.db "select task_uuid, status, status_reason, worker_node_id from arbitrage_tasks order by id desc limit 10;"
```

5. 删除、禁用或改坏其中一个绑定账户后，再次注入同类任务，预期直接失败：

```bash
redis-cli XADD stream:spot_opps '*' symbol BTC/USDT buy_exchange bitget sell_exchange gate spread_bps 25.0 target_quote_amount 15.0
sqlite3 furun.db "select task_uuid, status, status_reason, buy_account_id, sell_account_id from arbitrage_tasks order by id desc limit 10;"
sudo journalctl -u furun-spot-executor.service -n 120 --no-pager | grep 'executor_account_'
```

6. 失败场景验收点：
   - 任务进入 `FAILED`
   - `status_reason` 为 `executor_account_binding_not_found` 或 `executor_account_binding_invalid`
   - executor 不会静默 fallback 到普通 `resolve_accounts()` 选账户路径
   - 不会进入 spot service，因此不会出现对应任务的成功执行结果
7. 恢复绑定账户后再次注入同类任务，预期新任务重新成功，旧失败任务保留原始
   `status_reason` 供排障使用。

主服务器实测记录（2026-05-25）：

- 远端 `dispatcher` 闭环已通过：
  - canary 任务 `606a0f6690f842c9aea09c225ac3abad`
  - `arbitrage_tasks.buy_account_id = 28`
  - `arbitrage_tasks.sell_account_id = 29`
  - 节点 payload 同时带出 `buy_account_id = "28"`、`sell_account_id = "29"`
  - 任务状态为 `DISPATCHED`
- 远端 `executor-missing` 闭环已通过：
  - 任务 `6f8db7a246164a3e97356be9ad9eb592`
  - dispatch 后删除绑定卖账户 `sell_account_id = 36`
  - 最终 `status = FAILED`
  - `status_reason = executor_account_binding_not_found`
  - `processed = 0`，且没有进入最终 dispatch
- 远端 `executor-restored` 闭环已通过：
  - 任务 `b811f76331cd45e3a1999086d6717867`
  - 删除原卖账户后恢复兼容卖账户 `sell_account_id = 39`
  - 新任务重新绑定到 `buy_account_id = 37`、`sell_account_id = 39`
  - executor 最终按绑定账户执行，`account_ids_by_exchange = {"bitget": 37, "gate": 39}`
  - 最终 `status = SUCCEEDED`

本次远端排障备注：

- 初次远端验证并非业务逻辑失败，而是主服务器 PostgreSQL 上存在遗留锁链
- 锁头为旧的 `ALTER TABLE arbitrage_tasks ADD COLUMN buy_account_id INTEGER`
- 后续对 `arbitrage_tasks` 的 introspection、`SELECT`、`DELETE` 和新的 `ALTER TABLE ... IF NOT EXISTS` 都被该锁链阻塞
- 处理方式是先探测 `pg_stat_activity / pg_locks / pg_blocking_pids(...)`，确认阻塞链后重启远端 `postgresql` 服务清锁
- 清锁后再次执行三段 canary 验证，全部通过
- 因此若后续主服务器再次出现“脚本卡在任务表第一写”的现象，优先排查 PostgreSQL 表锁，而不是先怀疑 binding 逻辑回归

### Dispatcher DB Account Discovery Validation

验证 `dispatcher` 已按数据库资格发现候选用户，且 `env_mode` 与显式
`DISPATCH_USER_IDS` 都不会绕过资格过滤时，建议按下面步骤操作。

1. 先在主服务器数据库准备两类用户：
   - 用户 `42`：`is_trading_enabled=1`，至少有一条启用的 `testnet` 账户，且有启用策略
   - 用户 `99`：有启用策略，但只有 `mainnet` 账户
2. 清空 `.env.worker` 中的 `DISPATCH_USER_IDS`，然后重启 `furun-spot-dispatcher.service`：

```bash
cd /home/ubuntu/furunsystemv4/current
grep -E '^(ENV_MODE|DISPATCH_USER_IDS)=' .env.worker
sudo systemctl restart furun-spot-dispatcher.service
sudo systemctl is-active furun-spot-dispatcher.service
```

3. 写入一条会命中用户 `42` 策略的机会，并检查只为 `42` 建任务：

```bash
redis-cli XADD stream:spot_opps '*' symbol BTC/USDT buy_exchange bitget sell_exchange gate spread_bps 25.0 target_quote_amount 15.0
sqlite3 furun.db "select task_uuid, user_id, env_mode, status from arbitrage_tasks order by id desc limit 10;"
```

4. 验收数据库自动发现结果：
   - 只应看到用户 `42` 的新任务
   - 不应为只有 `mainnet` 账户的用户 `99` 创建任务
   - 新任务的 `env_mode` 应与 `.env.worker` 中当前 `ENV_MODE` 一致
5. 再把 `DISPATCH_USER_IDS=99` 写回 `.env.worker`，重启 dispatcher 后重复写入机会，确认即使显式白名单包含 `99`，只要数据库资格不满足，仍不会生成任务：

```bash
cd /home/ubuntu/furunsystemv4/current
grep -E '^(ENV_MODE|DISPATCH_USER_IDS)=' .env.worker
sudo systemctl restart furun-spot-dispatcher.service
redis-cli XADD stream:spot_opps '*' symbol BTC/USDT buy_exchange bitget sell_exchange gate spread_bps 25.0 target_quote_amount 15.0
sqlite3 furun.db "select task_uuid, user_id, env_mode, status from arbitrage_tasks order by id desc limit 10;"
```

6. 通过结构化日志确认发现层行为：

```bash
sudo journalctl -u furun-spot-dispatcher.service -n 100 --no-pager | grep 'dispatcher.user'
```

验收点：

- `dispatcher.user.discovery.succeeded` 中可见当前候选用户集合
- 当显式白名单用户不满足数据库资格时，不应产生对应新任务
- 只有满足 `ENV_MODE`、启用账户、启用策略和交易开关的用户才会进入候选集合
- 若候选用户无 Redis 路由，日志中可见 `dispatcher.user.skipped`

### Strategy Config Dispatcher Validation

策略配置接入 `dispatcher` 后，建议按下面步骤验证“多策略命中”和“无命中跳过”都符合预期。

1. 先在主服务器数据库为同一测试用户插入两条启用策略：
   - 一条限定 `BTC/USDT` + `bitget/gate`，`target_quote_amount=80`
   - 一条全符号全交易所，`open_spread_bps_threshold=20`，`target_quote_amount=35`
2. 重启 `furun-spot-dispatcher.service`，确保最新 `strategy_repository` 逻辑已生效：

```bash
cd /home/ubuntu/furunsystemv4/current
sudo systemctl restart furun-spot-dispatcher.service
sudo systemctl is-active furun-spot-dispatcher.service
```

3. 写入一条命中两条策略的机会，例如 `BTC/USDT`、`bitget/gate`、`spread_bps=25`：

```bash
redis-cli XADD stream:spot_opps '*' symbol BTC/USDT buy_exchange bitget sell_exchange gate spread_bps 25.0 target_quote_amount 15.0
```

4. 查询数据库与节点流，确认为同一 `source_message_id` 生成两条任务，且 `strategy_config_id` 分别对应两条策略：

```bash
sqlite3 furun.db "select task_uuid, user_id, strategy_config_id, opportunity_id, target_notional, status from arbitrage_tasks order by id desc limit 10;"
redis-cli XREVRANGE stream:spot_exec_tasks:main + - COUNT 10
```

5. 再写入一条不命中任何策略的机会，例如 `ETH/USDT` 且 `spread_bps=8`，确认不会创建新任务：

```bash
redis-cli XADD stream:spot_opps '*' symbol ETH/USDT buy_exchange bitget sell_exchange gate spread_bps 8.0 target_quote_amount 15.0
sqlite3 furun.db "select task_uuid, user_id, strategy_config_id, opportunity_id, target_notional, status from arbitrage_tasks order by id desc limit 10;"
redis-cli XREVRANGE stream:spot_exec_tasks:main + - COUNT 10
```

验收点：

- 命中两条策略时，`arbitrage_tasks` 新增两条记录，且 `strategy_config_id` 分别对应两条启用策略
- 最新 `stream:spot_exec_tasks:<node_id>` entries 中可看到对应的 `task_uuid`、`strategy_config_id`、`source_message_id`
- 不命中任何策略时，不应新增 `arbitrage_tasks`，也不应向 `stream:spot_exec_tasks:<node_id>` 写入新任务
- `journalctl -u furun-spot-dispatcher.service -n 100 --no-pager` 中如触发控制规则，可见策略维度的 `control.rule.blocked` 或 `control.rule.resized`

### Dispatcher Account Exchange Coverage Validation

账户仓储接入 `dispatcher` 后，建议按下面步骤验证“账户覆盖不足时跳过”和“补齐账户后恢复放行”都符合预期。

1. 先在主服务器数据库准备两个测试用户：
   - 用户 `42`：`testnet` 下有 `bitget` 和 `gate` 两条启用账户，且有启用中的 `spot_futures` 策略
   - 用户 `99`：仅有 `bitget` 启用账户，且同样有启用中的 `spot_futures` 策略
2. 清空 `.env.worker` 中的 `DISPATCH_USER_IDS`，重启 `furun-spot-dispatcher.service`：

```bash
cd /home/ubuntu/furunsystemv4/current
grep -E '^(ENV_MODE|DISPATCH_USER_IDS)=' .env.worker
sudo systemctl restart furun-spot-dispatcher.service
sudo systemctl is-active furun-spot-dispatcher.service
```

3. 写入一条需要同时覆盖 `bitget/gate` 的机会：

```bash
redis-cli XADD stream:spot_opps '*' symbol BTC/USDT buy_exchange bitget sell_exchange gate spread_bps 25.0 target_quote_amount 15.0
```

4. 查询数据库与节点任务流，确认只为用户 `42` 创建任务，不为用户 `99` 创建任务：

```bash
sqlite3 furun.db "select task_uuid, user_id, strategy_config_id, opportunity_id, target_notional, status from arbitrage_tasks order by id desc limit 10;"
redis-cli XREVRANGE stream:spot_exec_tasks:main + - COUNT 10
```

5. 查看 dispatcher 结构化日志，确认账户覆盖不足的跳过原因：

```bash
sudo journalctl -u furun-spot-dispatcher.service -n 120 --no-pager | grep 'dispatcher.user'
```

6. 重点核对以下现象：
   - 用户 `99` 出现 `dispatcher.user.skipped`
   - `payload.reason` 为 `account_exchange_coverage_missing`
   - `payload.buy_exchange` 为 `bitget`
   - `payload.sell_exchange` 为 `gate`
   - `payload.available_exchanges` 仅包含当前已配置账户交易所
   - 该次跳过不应伴随新的 `control.rule.blocked` 或 `control.rule.resized`
7. 再为用户 `99` 补齐 `gate` 启用账户，重复写入同一类机会，确认其开始进入策略匹配与任务创建链路。

### Dispatcher Account Auto Trade Validation

账户覆盖检查通过后，`dispatcher` 还会继续要求买卖两边账户都满足
`is_auto_trade_enabled=1`。如果任一边关闭自动交易，应记录
`dispatcher.user.skipped`，但不进入 `control_guard`，也不发
`control.rule.*` 事件。

1. 在主服务器数据库准备两个测试用户：
   - 用户 `42`：`testnet` 下同时有 `bitget` 和 `gate` 启用账户，且两边都 `is_auto_trade_enabled=1`
   - 用户 `99`：同样有 `bitget` 和 `gate` 启用账户，但其中一边 `is_auto_trade_enabled=0`
2. 确认两个用户都有启用中的 `spot_futures` 策略，然后清空 `.env.worker` 中的 `DISPATCH_USER_IDS` 并重启 `furun-spot-dispatcher.service`：

```bash
cd /home/ubuntu/furunsystemv4/current
grep -E '^(ENV_MODE|DISPATCH_USER_IDS)=' .env.worker
sudo systemctl restart furun-spot-dispatcher.service
sudo systemctl is-active furun-spot-dispatcher.service
```

3. 写入一条需要同时使用 `bitget/gate` 的机会：

```bash
redis-cli XADD stream:spot_opps '*' symbol BTC/USDT buy_exchange bitget sell_exchange gate spread_bps 25.0 target_quote_amount 15.0
```

4. 查询数据库和节点任务流，确认只为用户 `42` 创建任务：

```bash
sqlite3 furun.db "select task_uuid, user_id, strategy_config_id, opportunity_id, target_notional, status from arbitrage_tasks order by id desc limit 10;"
redis-cli XREVRANGE stream:spot_exec_tasks:main + - COUNT 10
```

5. 检查 `dispatcher` 结构化日志，确认自动交易关闭用户被跳过，且没有触发 `control.rule.*`：

```bash
sudo journalctl -u furun-spot-dispatcher.service -n 120 --no-pager | grep 'dispatcher.user'
sudo journalctl -u furun-spot-dispatcher.service -n 120 --no-pager | grep 'control.rule'
```

验收点：

- 只应看到用户 `42` 的新任务，不应为用户 `99` 创建新任务
- 用户 `99` 应出现 `dispatcher.user.skipped`
- `payload.reason` 应为 `account_auto_trade_disabled`
- `payload.auto_trade_enabled_exchanges` 只包含当前仍允许自动交易的交易所
- 自动交易关闭导致的跳过不应伴随新的 `control.rule.blocked` 或 `control.rule.resized`

6. 把用户 `99` 缺失自动交易资格的那一边账户改回 `is_auto_trade_enabled=1`，再次注入同类机会，确认其恢复进入任务链路。

### Dispatcher Market Type Scope Validation

账户覆盖与自动交易检查通过后，`dispatcher` 还会继续要求买卖两边至少各有一条
`market_type_scope` 与允许集合 `["spot", "swap"]` 有交集的账户。若任一边缺失，
应记录 `dispatcher.user.skipped`，并在进入 `control_guard` 之前短路。

1. 在主服务器数据库准备两个测试用户：
   - 用户 `42`：`testnet` 下同时有 `bitget` 和 `gate` 启用账户，两边都 `is_auto_trade_enabled=1`，且两边 `market_type_scope` 都包含 `spot` 或 `swap`
   - 用户 `99`：同样有 `bitget` 和 `gate` 启用账户，也都开启自动交易，但其中一边账户的 `market_type_scope` 置空，或改成不包含 `spot/swap` 的值
2. 确认两个用户都有启用中的 `spot_futures` 策略，然后清空 `.env.worker` 中的 `DISPATCH_USER_IDS` 并重启 `furun-spot-dispatcher.service`：

```bash
cd /home/ubuntu/furunsystemv4/current
grep -E '^(ENV_MODE|DISPATCH_USER_IDS)=' .env.worker
sudo systemctl restart furun-spot-dispatcher.service
sudo systemctl is-active furun-spot-dispatcher.service
```

3. 写入一条需要同时使用 `bitget/gate` 的机会：

```bash
redis-cli XADD stream:spot_opps '*' symbol BTC/USDT buy_exchange bitget sell_exchange gate spread_bps 25.0 target_quote_amount 15.0
```

4. 查询数据库和节点任务流，确认只为用户 `42` 创建任务：

```bash
sqlite3 furun.db "select task_uuid, user_id, strategy_config_id, opportunity_id, target_notional, status from arbitrage_tasks order by id desc limit 10;"
redis-cli XREVRANGE stream:spot_exec_tasks:main + - COUNT 10
```

5. 检查 `dispatcher` 结构化日志，确认 `market_type_scope` 缺失用户被跳过，且没有触发 `control.rule.*`：

```bash
sudo journalctl -u furun-spot-dispatcher.service -n 120 --no-pager | grep 'dispatcher.user'
sudo journalctl -u furun-spot-dispatcher.service -n 120 --no-pager | grep 'control.rule'
```

验收点：

- 只应看到用户 `42` 的新任务，不应为用户 `99` 创建新任务
- 用户 `99` 应出现 `dispatcher.user.skipped`
- `payload.reason` 应为 `account_market_type_scope_missing`
- `payload.market_type_scopes_by_exchange` 应显示当前各交易所声明的 scope
- `payload.allowed_market_types` 应为 `["spot", "swap"]`
- `market_type_scope` 缺失导致的跳过不应伴随新的 `control.rule.blocked` 或 `control.rule.resized`

6. 把用户 `99` 缺失资格的那一边账户的 `market_type_scope` 恢复为 `spot,swap`，再次注入同类机会，确认其恢复进入任务链路。

### Dispatcher Account Region Validation

账户覆盖、自动交易与 `market_type_scope` 检查通过后，`dispatcher`
还会继续要求买卖两边至少各有一条 `account_region` 与当前
`dispatcher.region` 兼容的账户。若任一边不兼容，应记录
`dispatcher.user.skipped`，并在进入 `control_guard` 之前短路。

1. 在 canary 用户的 `buy_exchange` 与 `sell_exchange` 账户都已启用、
   自动交易开启、`market_type_scope` 通过的前提下，先把其中一边账户的
   `account_region` 改成与当前 dispatcher 不兼容的值，例如 `hk`。
2. 保持另一边账户 `account_region=main` 或 `default`，然后重启
   `furun-spot-dispatcher.service`：

```bash
cd /home/ubuntu/furunsystemv4/current
sudo systemctl restart furun-spot-dispatcher.service
sudo systemctl is-active furun-spot-dispatcher.service
```

3. 注入一条专用机会，并检查数据库与节点任务流没有新增任务：

```bash
redis-cli XADD stream:spot_opps '*' symbol BTC/USDT buy_exchange bitget sell_exchange gate spread_bps 25.0 target_quote_amount 15.0
sqlite3 furun.db "select task_uuid, user_id, strategy_config_id, opportunity_id, target_notional, status from arbitrage_tasks order by id desc limit 10;"
redis-cli XREVRANGE stream:spot_exec_tasks:main + - COUNT 10
```

4. 检查 dispatcher 结构化日志，确认区域不兼容用户被跳过，且没有触发
   `control.rule.*`：

```bash
sudo journalctl -u furun-spot-dispatcher.service -n 120 --no-pager | grep 'dispatcher.user'
sudo journalctl -u furun-spot-dispatcher.service -n 120 --no-pager | grep 'control.rule'
```

验收点：

- 不应创建新的 `DISPATCHED` 任务
- 不应写入新的节点执行 payload
- `dispatcher.user.skipped` 的 `payload.reason` 为 `account_region_mismatch`
- `payload.dispatcher_region` 为当前 dispatcher 区域
- `payload.account_regions_by_exchange` 显示各交易所当前声明的区域
- `account_region` 不兼容导致的跳过不应伴随新的 `control.rule.blocked` 或 `control.rule.resized`

5. 将缺失一侧的 `account_region` 恢复为 `default` 或当前 dispatcher 对应区域后，
   再次注入同类机会，预期恢复创建任务与写节点流。

### Control Rule Events

控制链命中后，可在主服务器和执行节点直接查看结构化日志：

```bash
sudo journalctl -u furun-spot-dispatcher.service -n 50 --no-pager | grep 'control.rule'
sudo journalctl -u furun-spot-executor.service -n 50 --no-pager | grep 'control.rule'
```

常见事件：

- `control.rule.blocked`
- `control.rule.resized`

关注字段：

- `service`
- `symbol`
- `exchange`
- `payload.user_id`
- `payload.source_message_id`
- `payload.requested_notional`
- `payload.approved_notional`
- `payload.reason`

7. 远端联调 success 通知：

```bash
cd /home/ubuntu/furunsystemv4/current
chmod 600 .env.worker
sudo systemctl restart furun-spot-scanner.service
sudo systemctl restart furun-spot-dispatcher.service
sudo systemctl restart furun-spot-executor.service
sudo systemctl restart furun-control-admin.service
sleep 5
sudo journalctl -u furun-spot-scanner.service -n 30 --no-pager | grep '"event_type"'
sudo journalctl -u furun-spot-dispatcher.service -n 30 --no-pager | grep '"event_type"'
sudo journalctl -u furun-spot-executor.service -n 30 --no-pager | grep '"event_type"'
sudo journalctl -u furun-control-admin.service -n 30 --no-pager | grep 'control.admin'
redis-cli ZCARD arb:zset:spot
redis-cli XLEN stream:spot_opps
redis-cli XLEN stream:spot_exec_tasks:main
redis-cli XREVRANGE stream:spot_exec_tasks:main + - COUNT 5
redis-cli XREVRANGE stream:spot_opps + - COUNT 10
```

如果飞书暂时没有 success 通知，优先检查：

- `.env.worker` 中 `ALERT_SUCCESS_SPREAD_BPS_THRESHOLD` 是否高于当前实际价差；默认调优值为 `20`
- `journalctl` 中是否已出现 `opportunity.detected`
- 远端 Redis 指标是否在增长

本次联调要求同时确认：

- `journalctl` 仍然保留英文 JSON 字段
- `control-admin` 日志中可见 `control.admin.*` 事件
- 飞书 success 通知为中文
- success 通知数量相比 `0/60` 配置更少
- 最新 stream entries 中可看到深度定价字段与 `target_quote_amount`
- 近期 scanner activity 或 Redis 中至少出现两个白名单 symbol

## Scanner Recovery Notes

- 如果近期 `furun-spot-scanner.service` 日志出现 `SpotOpportunity object has no attribute 'get'`，先同步最新的 `app/market/opportunity.py`、`app/runtime/live_spot_flow.py`、`app/runtime/live_workers.py` 到远端对应目录。
- `LiveSpotFlowService.run_once()` 的成功返回值现在是 `SpotOpportunity` dataclass；只在运行时边界显式转换 payload，不再把扫描结果当成字典读取。
- `ContinuousSpotScanner` 的 success 事件 payload 现在来自 dataclass 属性访问，而不是 `result.get(...)`。
- 恢复时建议先记录 `redis-cli ZCARD arb:zset:spot`、`redis-cli XLEN stream:spot_opps` 与 `redis-cli XLEN stream:spot_exec_tasks:<node_id>` 的当前值，再重启 `furun-spot-scanner.service`、`furun-spot-dispatcher.service` 与 `furun-spot-executor.service`，等待约 5 秒后再次读取并确认指标继续增长。
- 验证日志时，除了确认两个服务都为 `active`，还要检查最近 50 行 scanner 日志中不再出现 `SpotOpportunity object has no attribute 'get'`。

8. 人工制造 `CRITICAL` 路径并验证飞书 + QQ 邮件：

```bash
cd /home/ubuntu/furunsystemv4/current
cp .env.worker .env.worker.backup
python3 - <<'PY'
from pathlib import Path
env_path = Path(".env.worker")
lines = env_path.read_text(encoding="utf-8").splitlines()
env_path.write_text(
    "\n".join(line for line in lines if not line.startswith("OKX_API_KEY=")) + "\n",
    encoding="utf-8",
)
PY
sudo systemctl restart furun-spot-scanner.service
sleep 5
sudo journalctl -u furun-spot-scanner.service -n 50 --no-pager | grep 'worker.start_failed'
mv .env.worker.backup .env.worker
chmod 600 .env.worker
sudo systemctl restart furun-spot-scanner.service
sleep 5
sudo systemctl is-active furun-spot-scanner.service
```

验收点：

- 飞书收到中文标题 `服务启动失败`
- QQ 邮箱收到中文标题 `[严重告警] 服务启动失败`
- `furun-spot-scanner.service` 由于配置了 `Restart=always`，故障演练期间可能仍短暂显示为 `active`；以 `journalctl` 中的 `worker.start_failed` JSON 为准
- 恢复 `.env.worker` 后服务重新回到 `active`

9. 单独重启某一侧服务时可使用：

```bash
sudo systemctl restart furun-spot-scanner.service
sudo systemctl restart furun-spot-dispatcher.service
sudo systemctl restart furun-spot-executor.service
sudo systemctl restart furun-control-admin.service
```

## Route Admin Ops

1. 在主服务器启用 `route-admin`：

```bash
cd /home/ubuntu/furunsystemv4/current
grep -E '^(ROUTE_ADMIN_ENABLED|ROUTE_ADMIN_BIND_HOST|ROUTE_ADMIN_PORT|ROUTE_ADMIN_TOKEN)=' .env.worker
sudo systemctl enable furun-route-admin.service
sudo systemctl restart furun-route-admin.service
sudo systemctl is-active furun-route-admin.service
```

验收点：

- `ROUTE_ADMIN_ENABLED=1`
- `ROUTE_ADMIN_BIND_HOST=127.0.0.1`
- `furun-route-admin.service` 返回 `active`

2. 如需从本地访问，优先使用 SSH 隧道：

```powershell
& "C:\Windows\System32\OpenSSH\ssh.exe" -L 8787:127.0.0.1:8787 -i $keyPath ubuntu@43.165.166.57
```

3. 通过 `curl` 验证接口：

```bash
curl -s http://127.0.0.1:8787/healthz
curl -s -H "Authorization: Bearer $ROUTE_ADMIN_TOKEN" http://127.0.0.1:8787/routes
curl -s -X PUT -H "Authorization: Bearer $ROUTE_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"node_id":"node-a"}' http://127.0.0.1:8787/routes/42
curl -s -X DELETE -H "Authorization: Bearer $ROUTE_ADMIN_TOKEN" http://127.0.0.1:8787/routes/42
sudo journalctl -u furun-route-admin.service -n 30 --no-pager | grep 'route.admin'
```

验收点：

- `/healthz` 返回 `{"ok": true}`
- `PUT /routes/42` 返回 `{"ok": true, "user_id": "42", "node_id": "node-a"}`
- `DELETE /routes/42` 返回 `{"ok": true, "user_id": "42"}`
- `journalctl` 中可见 `route.admin.updated`、`route.admin.deleted` 或 `route.admin.unauthorized`

## Control Admin Ops

1. 在主服务器启用 `control-admin`：

```bash
cd /home/ubuntu/furunsystemv4/current
grep -E '^(CONTROL_ADMIN_ENABLED|CONTROL_ADMIN_BIND_HOST|CONTROL_ADMIN_PORT|CONTROL_ADMIN_TOKEN)=' .env.worker
sudo systemctl enable furun-control-admin.service
sudo systemctl restart furun-control-admin.service
sudo systemctl is-active furun-control-admin.service
```

验收点：

- `CONTROL_ADMIN_ENABLED=1`
- `CONTROL_ADMIN_BIND_HOST=127.0.0.1`
- `CONTROL_ADMIN_TOKEN` 非空
- `furun-control-admin.service` 返回 `active`

2. 如需从本地访问，优先使用 SSH 隧道：

```powershell
& "C:\Windows\System32\OpenSSH\ssh.exe" -L 8788:127.0.0.1:8788 -i $keyPath ubuntu@43.165.166.57
```

3. 通过 `curl` 验证控制面接口：

```bash
curl -s http://127.0.0.1:8788/healthz
curl -s -H "Authorization: Bearer $CONTROL_ADMIN_TOKEN" http://127.0.0.1:8788/control/limits
curl -s -X PUT -H "Authorization: Bearer $CONTROL_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"scope_type":"user","scope_id":"42","limit_type":"max_notional","limit_value":35.0,"enabled":true,"priority":100}' \
  http://127.0.0.1:8788/control/limits/user-42-cap
curl -s -X PUT -H "Authorization: Bearer $CONTROL_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"enabled":true}' \
  http://127.0.0.1:8788/control/switches/platform.reduce_only:platform:global
curl -s -X POST -H "Authorization: Bearer $CONTROL_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"announcement_id":"maint-1","title":"维护通知","content":"今晚演练","priority":100,"is_pinned":false,"audience_type":"all","audience_filter":{},"channels":["site"],"status":"active"}' \
  http://127.0.0.1:8788/announcements
sudo journalctl -u furun-control-admin.service -n 30 --no-pager | grep 'control.admin'
```

验收点：

- `/healthz` 返回 `{"ok": true}`
- `GET /control/limits` 返回 `limits` 列表
- `PUT /control/limits/user-42-cap` 返回 `{"ok": true, ...}` 且 `limit_value` 为 `35.0`
- `PUT /control/switches/platform.reduce_only:platform:global` 返回 `{"ok": true, ...}`
- `POST /announcements` 返回 `{"ok": true, ...}` 且 `announcement_id` 为 `maint-1`
- `journalctl` 中可见 `control.admin.limit.updated`、`control.admin.switch.updated`、`control.admin.announcement.created` 或 `control.admin.unauthorized`

## Route Index Backfill

如果历史上存在只写了 `route:user_node:{user_id}` 但没有进入
`route:user_node:index` 的老路由，可以在主服务器上运行回填命令。

1. 先确认代码与文档已经同步到远端：

```bash
cd /home/ubuntu/furunsystemv4/current
ls app/runtime/route_admin_cli.py
ls docs/ops/live-workers-systemd.md
```

2. 先执行 dry-run，只看统计不写索引：

```bash
cd /home/ubuntu/furunsystemv4/current
./.venv/bin/python -m app.runtime.route_admin_cli backfill-index --dry-run
```

验收点：

- 命令返回 JSON，包含 `ok`、`found`、`newly_indexed`、`already_indexed`、`skipped`、`dry_run`
- `dry_run` 为 `true`，且本次只统计不会写入 `route:user_node:index`
- 优先使用项目虚拟环境中的 Python，避免系统 Python 缺少依赖
- 不会修改已有 `route:user_node:{user_id}` 的值

3. 再执行正式回填，并检查 Redis 索引集合：

```bash
cd /home/ubuntu/furunsystemv4/current
./.venv/bin/python -m app.runtime.route_admin_cli backfill-index
redis-cli SMEMBERS route:user_node:index
```

验收点：

- 命令返回 JSON，且 `ok` 为 `true`
- JSON 中 `newly_indexed` 表示本次真正新补入索引集合的数量，`already_indexed` 表示原本已在索引中的数量
- 历史路由用户进入 `route:user_node:index`
- 回填只补索引，不覆盖已有 `node_id`

4. 通过 route-admin 接口确认历史路由已经可见：

```bash
curl -s http://127.0.0.1:8787/healthz
curl -s -H "Authorization: Bearer $ROUTE_ADMIN_TOKEN" http://127.0.0.1:8787/routes
```

验收点：

- `/healthz` 返回 `{"ok": true}`
- `/routes` 返回的 `routes` 中可以看到刚补录进索引的历史用户

5. 再执行一次正式回填，验证幂等复跑：

```bash
cd /home/ubuntu/furunsystemv4/current
./.venv/bin/python -m app.runtime.route_admin_cli backfill-index
```

验收点：

- 命令再次执行不报错
- 当索引已经补齐后，后续复跑的 `newly_indexed` 应为 `0`，`already_indexed` 应反映已存在的历史路由数量
- `route:user_node:index` 与 `/routes` 结果保持稳定
