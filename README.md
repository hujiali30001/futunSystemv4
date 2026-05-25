# FuRunSystemV4

FuRunSystemV4 是一个基于 Python 的现货跨交易所套利运行系统，围绕 `scanner -> consumer -> dispatcher -> executor` 的实时流水线工作。系统使用 Redis Streams 传递机会与执行任务，支持 PostgreSQL/SQLite 持久化任务与账户真值，并提供 `systemd` 部署资产、告警路由、控制面与路由管理能力。

## 当前状态

- 已完成多角色 live worker 基础链路。
- 已支持数据库任务模型、策略配置、账户发现与账户真值解析。
- 已完成 `task-account-binding`：
  - dispatcher 为任务写入 `buy_account_id` / `sell_account_id`
  - node payload 同步透传 binding
  - executor 严格按 binding 账户执行
  - binding 丢失时显式失败，不再静默回退
- 本地已完成主链与相邻回归验证，最近一轮验收为 `112 passed`。
- 主服务器已完成 dispatcher / executor 缺失账户 / 恢复账户三段远端闭环验证。

## 仓库结构

```text
app/
  admin/           Control plane、通知与管理接口
  db/              SQLAlchemy repository 与 session
  exchanges/       交易所 session / adapter
  runtime/         Worker、Redis 流水线、告警、systemd 相关逻辑
  trading/         执行与风控
deploy/systemd/    systemd service 模板与 worker 环境样例
docs/ops/          运维说明
docs/superpowers/  设计与执行过程文档
tests/             pytest 测试集
```

## 核心角色

- `scanner`
  - 持续扫描交易所盘口，产出套利机会。
- `consumer`
  - 消费机会流并做机会级处理。
- `dispatcher`
  - 基于数据库中的用户、策略、账户可用性和路由信息创建任务。
  - 在任务层绑定 `buy_account_id` / `sell_account_id`，并把 binding 写入 executor payload。
- `executor`
  - 消费节点执行流，按任务 binding 精确加载账户后执行。

对应的运行入口位于 `app/runtime/worker_service.py`，可通过 `--role` 选择角色。

## 技术栈

- Python 3.10+
- `sqlalchemy`
- `redis` / `redis.asyncio`
- `aiohttp`
- `pydantic` / `pydantic-settings`
- `ccxt`
- `pytest` / `pytest-asyncio`

依赖清单见 `requirements.txt`。

## 快速开始

### 1. 创建虚拟环境并安装依赖

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 准备环境变量

可先复制 `deploy/systemd/.env.worker.example` 为仓库根目录下的 `.env`，再按实际环境修改：

```bash
copy deploy\systemd\.env.worker.example .env
```

最重要的配置包括：

- `REDIS_URL`
- `ENV_MODE`
- `WORKER_ROLE`
- `WORKER_REGION`
- `NODE_ID`
- `DATABASE_ENABLED`
- `DATABASE_URL`
- `SPOT_EXCHANGES`
- 各交易所 API 凭证与代理配置

如果启用数据库任务真值链路，需要把 `DATABASE_ENABLED=1`，并提供可用的 `DATABASE_URL`。

### 3. 启动 worker

直接运行 worker 入口：

```bash
python -m app.runtime.worker_service --role scanner
python -m app.runtime.worker_service --role consumer
python -m app.runtime.worker_service --role dispatcher
python -m app.runtime.worker_service --role executor
```

`executor` 默认从 `stream:spot_exec_tasks:{NODE_ID}` 消费；如需自定义，可设置 `EXECUTOR_STREAM_KEY`。

### 4. 旧入口说明

仓库根目录仍保留 `main.py`，但它只构造一个轻量 `RuntimeApp(service_name, region)` 对象。当前实际运行与部署以 `app/runtime/worker_service.py` 为主入口。

## 测试

运行全部测试：

```bash
pytest -q
```

运行与 live worker / task binding 直接相关的重点测试：

```bash
pytest -q tests/test_live_workers.py tests/test_redis_opportunity_flow.py tests/test_task_repository.py tests/test_worker_service.py
```

如需做额外静态校验，可执行：

```bash
python -m py_compile main.py models.py
```

## 部署与运维

- `deploy/systemd/` 提供各角色 `systemd` service 模板。
- `docs/ops/live-workers-systemd.md` 记录了 live worker 的部署与最近一轮主服务器闭环验证。
- 路由与控制平面相关能力位于：
  - `app/runtime/route_admin_service.py`
  - `app/runtime/control_admin_service.py`

## 安全说明

- 不要把真实 `.env`、API Key、代理账号密码、SSH 密钥提交到仓库。
- `.tmp-ssh/`、`.keys/`、`local-secrets/` 已按本地敏感目录处理。
- 提交前建议再次检查 `git status` 与 `git diff --cached`，确认没有把本地数据库、日志或临时文件带入版本库。

## 相关文档

- 运维说明：`docs/ops/live-workers-systemd.md`
- 最新设计：`docs/superpowers/specs/2026-05-25-task-account-binding-design.md`
- 最新执行计划：`docs/superpowers/plans/2026-05-25-task-account-binding-plan.md`
