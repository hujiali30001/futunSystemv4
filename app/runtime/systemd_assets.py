def render_systemd_unit(*, role: str) -> str:
    if role == "route-admin":
        description = "FuRun route admin service"
        exec_start = (
            "/home/ubuntu/furunsystemv4/current/.venv/bin/python "
            "-m app.runtime.route_admin_service"
        )
    elif role == "control-admin":
        description = "FuRun control admin service"
        exec_start = (
            "/home/ubuntu/furunsystemv4/current/.venv/bin/python "
            "-m app.runtime.control_admin_service"
        )
    else:
        description = f"FuRun spot {role} worker"
        exec_start = (
            "/home/ubuntu/furunsystemv4/current/.venv/bin/python "
            f"-m app.runtime.worker_service --role {role}"
        )
    return f"""[Unit]
Description={description}
After=network.target redis.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/furunsystemv4/current
EnvironmentFile=/home/ubuntu/furunsystemv4/current/.env.worker
ExecStart={exec_start}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
"""


def render_worker_env_example() -> str:
    return """REDIS_URL=redis://127.0.0.1:6379/0
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
# SPOT_SYMBOLS=auto  (enables full-symbol auto-discovery from load_markets)
# SPOT_SYMBOLS_AUTO_QUOTE=USDT  (quote currencies to include in auto mode, default USDT only)
SPOT_EXCHANGES=okx,binance,bybit,bitget,gate
ORDERBOOK_DEPTH_LIMIT=5
TARGET_QUOTE_AMOUNT=100.0
SCANNER_POLL_INTERVAL_SECONDS=1.0
CONSUMER_BLOCK_MS=1000
# Database
DATABASE_ENABLED=0
DATABASE_URL=sqlite:///./furun.db
# Route admin
ROUTE_ADMIN_ENABLED=0
ROUTE_ADMIN_BIND_HOST=127.0.0.1
ROUTE_ADMIN_PORT=8787
ROUTE_ADMIN_TOKEN=
# Control admin
CONTROL_ADMIN_ENABLED=0
CONTROL_ADMIN_BIND_HOST=127.0.0.1
CONTROL_ADMIN_PORT=8788
CONTROL_ADMIN_TOKEN=
# Alert routing
ALERTS_ENABLED=1
ALERT_FEISHU_ENABLED=1
ALERT_FEISHU_WEBHOOK=
ALERT_EMAIL_ENABLED=1
ALERT_EMAIL_SMTP_HOST=smtp.qq.com
ALERT_EMAIL_SMTP_PORT=465
ALERT_EMAIL_USERNAME=
ALERT_EMAIL_PASSWORD=
ALERT_EMAIL_TO=
ALERT_SUCCESS_SPREAD_BPS_THRESHOLD=20
ALERT_DEDUPE_WINDOW_SECONDS=300
# Exchange credentials
OKX_API_KEY=
OKX_SECRET=
OKX_PASSWORD=
OKX_PROXY_TYPE=http
OKX_PROXY_HOST=
OKX_PROXY_PORT=
OKX_PROXY_USERNAME=
OKX_PROXY_PASSWORD=
BINANCE_API_KEY=
BINANCE_SECRET=
BINANCE_PASSWORD=
BINANCE_PROXY_TYPE=http
BINANCE_PROXY_HOST=
BINANCE_PROXY_PORT=
BINANCE_PROXY_USERNAME=
BINANCE_PROXY_PASSWORD=
BYBIT_API_KEY=
BYBIT_SECRET=
BYBIT_PASSWORD=
BYBIT_PROXY_TYPE=http
BYBIT_PROXY_HOST=
BYBIT_PROXY_PORT=
BYBIT_PROXY_USERNAME=
BYBIT_PROXY_PASSWORD=
BITGET_API_KEY=
BITGET_SECRET=
BITGET_PASSWORD=
BITGET_PROXY_TYPE=http
BITGET_PROXY_HOST=
BITGET_PROXY_PORT=
BITGET_PROXY_USERNAME=
BITGET_PROXY_PASSWORD=
GATE_API_KEY=
GATE_SECRET=
GATE_PASSWORD=
GATE_PROXY_TYPE=http
GATE_PROXY_HOST=
GATE_PROXY_PORT=
GATE_PROXY_USERNAME=
GATE_PROXY_PASSWORD=
"""
