# Executor DB Account Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `executor` 在消费节点任务时，基于数据库 `ExchangeAccount` 与 `Proxy` 真值为买卖两边动态装配执行期 credentials/proxy，不再依赖环境变量作为执行主凭证来源。

**Architecture:** 保持 `dispatcher` 的任务粒度、任务状态机和现有 payload 结构不变，新增一个 executor 专用的“账户真值解析器”组件：它使用 `AccountRepository` 读取用户当前 `env_mode` 下的启用账户，按交易所、`market_type_scope`、`account_region` 和 `is_auto_trade_enabled` 选择可执行账户，再解密密文字段并组装 `ExchangeCredentials` 与代理配置。`RedisExecutionTaskConsumer` 改为在调用 `RedisOpportunityDispatcher` 前先解析本次任务的买卖两边数据库账户；`WorkerApp` 与 `DefaultWorkerFactory` 则收缩 executor 对环境变量凭证的启动依赖。

**Tech Stack:** Python 3.10+, asyncio, redis.asyncio, SQLAlchemy, pytest, pytest-asyncio

---

## 文件结构与职责

- `d:\old\FuRunSystemV4\app\runtime\executor_account_truth.py`
  - 新增 executor 专用账户真值解析组件
  - 负责账户筛选、密文字段解密、代理装配、错误分类
- `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
  - 扩展 `RedisExecutionTaskConsumer` 的依赖与执行前解析逻辑
  - 在任务失败时记录 executor 账户真值错误
- `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
  - 调整 `RedisOpportunityDispatcher.dispatch()` 的入参，从“全量 env 凭证字典”转为“本次任务已解析好的买卖两边凭证”
- `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
  - 给 executor 注入数据库 session factory、账户仓储与账户真值解析器
  - 收缩 executor 对启动期 env 凭证的依赖
- `d:\old\FuRunSystemV4\app\runtime\worker_config.py`
  - 如有必要，仅保留 scanner/consumer 对 env 凭证加载的路径，executor 不再强依赖
- `d:\old\FuRunSystemV4\tests\test_live_workers.py`
  - 覆盖 executor 侧账户真值解析、失败语义、任务状态与 control-rule 短路不回归
- `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`
  - 覆盖 `RedisOpportunityDispatcher` 新的调用约定
- `d:\old\FuRunSystemV4\tests\test_worker_service.py`
  - 覆盖 executor worker 的新依赖注入与启动行为
- `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`
  - 补充 executor 数据库账户真值的联调与远端验证说明

## 实现前检查

- 当前 `AccountRepository.list_enabled_accounts()` 已经：
  - 按 `user_id + env_mode + is_enabled` 查询账户
  - 通过 `joinedload(ExchangeAccount.proxy)` 预加载代理关系
  - 文件位置：[account_repository.py](file:///d:/old/FuRunSystemV4/app/db/account_repository.py)
- 当前 `RedisExecutionTaskConsumer.run()` 签名仍为：
  - `run(*, credentials_by_exchange: dict, max_iterations: int | None = None)`
  - 文件位置：[live_workers.py](file:///d:/old/FuRunSystemV4/app/runtime/live_workers.py)
- 当前 `RedisOpportunityDispatcher.dispatch()` 签名仍为：
  - `dispatch(payload, *, credentials_by_exchange: dict)`
  - 文件位置：[redis_flow.py](file:///d:/old/FuRunSystemV4/app/runtime/redis_flow.py)
- 当前 `WorkerApp.run()` 会对所有角色统一执行：
  - `load_exchange_credentials_from_env(exchanges)`
  - `load_exchange_proxies_from_env(exchanges)`
  - 并在缺少凭证时直接启动失败
  - 文件位置：[worker_service.py](file:///d:/old/FuRunSystemV4/app/runtime/worker_service.py)

### Task 1: 新增 executor 账户真值解析器并补齐测试假对象

**Files:**
- Create: `d:\old\FuRunSystemV4\app\runtime\executor_account_truth.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: 先扩展测试假对象，支持 executor 账户真值字段**

```python
class FakeExchangeAccount:
    def __init__(
        self,
        *,
        exchange: str,
        account_id: int = 1,
        is_auto_trade_enabled: bool = True,
        market_type_scope: str = "spot,swap",
        account_region: str = "default",
        api_key_ciphertext: str = "ak",
        secret_ciphertext: str = "sk",
        passphrase_ciphertext: str | None = None,
        proxy_id: int | None = None,
        proxy: object | None = None,
    ):
        self.id = account_id
        self.exchange = exchange
        self.is_auto_trade_enabled = is_auto_trade_enabled
        self.market_type_scope = market_type_scope
        self.account_region = account_region
        self.api_key_ciphertext = api_key_ciphertext
        self.secret_ciphertext = secret_ciphertext
        self.passphrase_ciphertext = passphrase_ciphertext
        self.proxy_id = proxy_id
        self.proxy = proxy
```

- [ ] **Step 2: 再写失败测试，锁定区域与 scope 兼容下的账户选择**

```python
from app.runtime.executor_account_truth import ExecutorAccountTruthResolver


class FakeSecretCipher:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def decrypt(self, ciphertext: str | None) -> str | None:
        self.calls.append(ciphertext)
        return self.values[ciphertext]


def test_executor_account_truth_resolver_selects_first_matching_account_by_id():
    accounts = [
        FakeExchangeAccount(
            exchange="bitget",
            account_id=5,
            account_region="hk",
            market_type_scope="spot,swap",
            api_key_ciphertext="ak-5",
            secret_ciphertext="sk-5",
        ),
        FakeExchangeAccount(
            exchange="bitget",
            account_id=7,
            account_region="default",
            market_type_scope="spot,swap",
            api_key_ciphertext="ak-7",
            secret_ciphertext="sk-7",
        ),
        FakeExchangeAccount(
            exchange="gate",
            account_id=9,
            account_region="main",
            market_type_scope="spot,swap",
            api_key_ciphertext="ak-9",
            secret_ciphertext="sk-9",
        ),
    ]
    cipher = FakeSecretCipher(
        {
            "ak-7": "plain-ak-7",
            "sk-7": "plain-sk-7",
            "ak-9": "plain-ak-9",
            "sk-9": "plain-sk-9",
        }
    )
    resolver = ExecutorAccountTruthResolver(secret_cipher=cipher)

    resolved = resolver.resolve_accounts(
        accounts=accounts,
        user_id="42",
        buy_exchange="bitget",
        sell_exchange="gate",
        env_mode="testnet",
        region="main",
    )

    assert resolved["bitget"].account_id == 7
    assert resolved["gate"].account_id == 9
    assert resolved["bitget"].credentials.api_key == "plain-ak-7"
    assert resolved["gate"].credentials.secret == "plain-sk-9"
```

- [ ] **Step 3: 再写失败测试，锁定代理装配与默认区域语义**

```python
def test_executor_account_truth_resolver_builds_proxy_urls_from_account_proxy():
    proxy = type(
        "ProxyRecord",
        (),
        {
            "proxy_type": "http",
            "host": "127.0.0.1",
            "port": 9000,
            "username": "u1",
            "password_ciphertext": "proxy-pass",
        },
    )()
    account = FakeExchangeAccount(
        exchange="bitget",
        account_id=7,
        account_region="default",
        market_type_scope="spot,swap",
        api_key_ciphertext="ak-7",
        secret_ciphertext="sk-7",
        proxy=proxy,
        proxy_id=3,
    )
    other = FakeExchangeAccount(
        exchange="gate",
        account_id=9,
        account_region="default",
        market_type_scope="spot,swap",
        api_key_ciphertext="ak-9",
        secret_ciphertext="sk-9",
    )
    cipher = FakeSecretCipher(
        {
            "ak-7": "plain-ak-7",
            "sk-7": "plain-sk-7",
            "proxy-pass": "proxy-secret",
            "ak-9": "plain-ak-9",
            "sk-9": "plain-sk-9",
        }
    )
    resolver = ExecutorAccountTruthResolver(secret_cipher=cipher)

    resolved = resolver.resolve_accounts(
        accounts=[account, other],
        user_id="42",
        buy_exchange="bitget",
        sell_exchange="gate",
        env_mode="testnet",
        region="main",
    )

    assert resolved["bitget"].proxies == {
        "http": "http://u1:proxy-secret@127.0.0.1:9000",
        "https": "http://u1:proxy-secret@127.0.0.1:9000",
    }
```

- [ ] **Step 4: 再写失败测试，锁定执行前错误分类**

```python
from app.runtime.executor_account_truth import ExecutorAccountTruthError


def test_executor_account_truth_resolver_raises_not_found_for_missing_exchange():
    resolver = ExecutorAccountTruthResolver(secret_cipher=FakeSecretCipher({}))

    with pytest.raises(ExecutorAccountTruthError) as exc_info:
        resolver.resolve_accounts(
            accounts=[],
            user_id="42",
            buy_exchange="bitget",
            sell_exchange="gate",
            env_mode="testnet",
            region="main",
        )

    assert exc_info.value.reason == "executor_account_not_found"


def test_executor_account_truth_resolver_raises_decrypt_failed_when_cipher_raises():
    class FailingCipher:
        def decrypt(self, ciphertext):
            raise RuntimeError("boom")

    resolver = ExecutorAccountTruthResolver(secret_cipher=FailingCipher())
    accounts = [
        FakeExchangeAccount(
            exchange="bitget",
            account_id=7,
            account_region="default",
            market_type_scope="spot,swap",
            api_key_ciphertext="ak-7",
            secret_ciphertext="sk-7",
        ),
        FakeExchangeAccount(
            exchange="gate",
            account_id=9,
            account_region="default",
            market_type_scope="spot,swap",
            api_key_ciphertext="ak-9",
            secret_ciphertext="sk-9",
        ),
    ]

    with pytest.raises(ExecutorAccountTruthError) as exc_info:
        resolver.resolve_accounts(
            accounts=accounts,
            user_id="42",
            buy_exchange="bitget",
            sell_exchange="gate",
            env_mode="testnet",
            region="main",
        )

    assert exc_info.value.reason == "executor_account_decrypt_failed"
```

- [ ] **Step 5: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "executor_account_truth_resolver" -v`

Expected: FAIL，因为 `app.runtime.executor_account_truth`、`ExecutorAccountTruthResolver`、`ExecutorAccountTruthError` 还不存在。

- [ ] **Step 6: 写最小实现，创建 executor 账户真值解析器**

```python
from dataclasses import dataclass

from app.exchanges.session_manager import ExchangeCredentials, build_proxy_urls
from app.runtime.live_workers import _normalize_account_region, _parse_market_type_scope


class ExecutorAccountTruthError(RuntimeError):
    def __init__(self, reason: str, *, user_id: str, exchange: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.user_id = user_id
        self.exchange = exchange


@dataclass(slots=True)
class ResolvedExecutionAccount:
    account_id: int
    exchange: str
    credentials: ExchangeCredentials
    proxies: dict[str, str]


class PassthroughSecretCipher:
    def decrypt(self, ciphertext: str | None) -> str | None:
        return ciphertext


class ExecutorAccountTruthResolver:
    def __init__(self, *, secret_cipher=None) -> None:
        self.secret_cipher = secret_cipher or PassthroughSecretCipher()

    def resolve_accounts(
        self,
        *,
        accounts: list[object],
        user_id: str,
        buy_exchange: str,
        sell_exchange: str,
        env_mode: str,
        region: str,
    ) -> dict[str, ResolvedExecutionAccount]:
        return {
            buy_exchange: self._resolve_single_exchange(
                accounts=accounts,
                user_id=user_id,
                exchange=buy_exchange,
                region=region,
            ),
            sell_exchange: self._resolve_single_exchange(
                accounts=accounts,
                user_id=user_id,
                exchange=sell_exchange,
                region=region,
            ),
        }

    def _resolve_single_exchange(
        self,
        *,
        accounts: list[object],
        user_id: str,
        exchange: str,
        region: str,
    ) -> ResolvedExecutionAccount:
        normalized_region = _normalize_account_region(region)
        eligible = []
        for account in accounts:
            if str(getattr(account, "exchange", "")) != exchange:
                continue
            if not getattr(account, "is_auto_trade_enabled", True):
                continue
            if not _parse_market_type_scope(getattr(account, "market_type_scope", None)):
                continue
            account_region = _normalize_account_region(
                getattr(account, "account_region", None)
            )
            if account_region not in {"default", normalized_region}:
                continue
            eligible.append(account)
        if not eligible:
            raise ExecutorAccountTruthError(
                "executor_account_not_found",
                user_id=user_id,
                exchange=exchange,
                detail=f"no executable account for exchange={exchange}",
            )
        selected = sorted(eligible, key=lambda item: int(getattr(item, "id", 0)))[0]
        return ResolvedExecutionAccount(
            account_id=int(getattr(selected, "id")),
            exchange=exchange,
            credentials=ExchangeCredentials(
                api_key=self._decrypt(
                    getattr(selected, "api_key_ciphertext", None),
                    user_id=user_id,
                    exchange=exchange,
                ),
                secret=self._decrypt(
                    getattr(selected, "secret_ciphertext", None),
                    user_id=user_id,
                    exchange=exchange,
                ),
                password=self._decrypt(
                    getattr(selected, "passphrase_ciphertext", None),
                    user_id=user_id,
                    exchange=exchange,
                ),
            ),
            proxies=self._build_proxies(selected, user_id=user_id, exchange=exchange),
        )
```

- [ ] **Step 7: 重新运行定向测试**

Run: `python -m pytest tests/test_live_workers.py -k "executor_account_truth_resolver" -v`

Expected: PASS，确认账户选择、解密与代理装配规则落地。

- [ ] **Step 8: 提交这一小步**

```bash
git add app/runtime/executor_account_truth.py tests/test_live_workers.py
git commit -m "feat: add executor db account truth resolver"
```

### Task 2: 改造 `RedisOpportunityDispatcher`，接收任务级已解析凭证

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`

- [ ] **Step 1: 先写失败测试，锁定 dispatcher 接收任务级凭证字典**

```python
@pytest.mark.asyncio
async def test_dispatcher_uses_pre_resolved_execution_credentials():
    service = FakeSpotService()
    dispatcher = RedisOpportunityDispatcher(service)
    bitget_credentials = object()
    gate_credentials = object()

    await dispatcher.dispatch(
        {
            "symbol": "BTC/USDT",
            "buy_exchange": "bitget",
            "sell_exchange": "gate",
        },
        execution_accounts_by_exchange={
            "bitget": {"credentials": bitget_credentials, "proxies": {"http": "http://127.0.0.1:8000"}},
            "gate": {"credentials": gate_credentials, "proxies": {}},
        },
    )

    assert service.calls[0]["credentials_by_exchange"] == {
        "bitget": bitget_credentials,
        "gate": gate_credentials,
    }
    assert service.calls[0]["proxies_by_exchange"] == {
        "bitget": {"http": "http://127.0.0.1:8000"},
        "gate": {},
    }
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_redis_opportunity_flow.py -k "pre_resolved_execution_credentials" -v`

Expected: FAIL，当前 `dispatch()` 还不接受 `execution_accounts_by_exchange` 参数，也不会向 spot service 透传 `proxies_by_exchange`。

- [ ] **Step 3: 写最小实现，调整 dispatch 签名和透传行为**

```python
async def dispatch(self, payload: dict, *, execution_accounts_by_exchange: dict) -> object:
    exchanges = [payload["buy_exchange"], payload["sell_exchange"]]
    scoped_credentials = {
        exchange: execution_accounts_by_exchange[exchange]["credentials"]
        for exchange in exchanges
    }
    scoped_proxies = {
        exchange: execution_accounts_by_exchange[exchange]["proxies"]
        for exchange in exchanges
    }
    target_quote_amount = float(payload.get("target_quote_amount", 15.0))
    return await self.spot_service.run_task(
        exchanges=exchanges,
        credentials_by_exchange=scoped_credentials,
        proxies_by_exchange=scoped_proxies,
        symbol=payload["symbol"],
        target_quote_amount=target_quote_amount,
        env_mode="testnet",
    )
```

- [ ] **Step 4: 重新运行定向测试**

Run: `python -m pytest tests/test_redis_opportunity_flow.py -k "pre_resolved_execution_credentials or target_quote_amount or duplicate_exchange" -v`

Expected: PASS，新签名与既有 `target_quote_amount`、重复交易所语义都兼容。

- [ ] **Step 5: 提交这一小步**

```bash
git add app/runtime/redis_flow.py tests/test_redis_opportunity_flow.py
git commit -m "feat: dispatch executor tasks with resolved db credentials"
```

### Task 3: 改造 `RedisExecutionTaskConsumer`，在执行前解析数据库账户真值

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`

- [ ] **Step 1: 先写失败测试，锁定 executor 从 DB 账户解析后再调用 dispatcher**

```python
@pytest.mark.asyncio
async def test_executor_resolves_db_accounts_before_dispatch():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_exec_tasks:node-a",
                [
                    (
                        "1-0",
                        {
                            "task_uuid": "task-1",
                            "user_id": "42",
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    class FakeResolvedDispatcher:
        def __init__(self):
            self.payloads = []

        async def dispatch(self, payload, *, execution_accounts_by_exchange):
            self.payloads.append((payload, execution_accounts_by_exchange))
            return {"ok": True}

    class FakeExecutorAccountTruthResolver:
        def __init__(self, resolved):
            self.resolved = resolved
            self.calls = []

        def resolve_accounts(self, **kwargs):
            self.calls.append(kwargs)
            return self.resolved

    dispatcher = FakeResolvedDispatcher()
    resolver = FakeExecutorAccountTruthResolver(
        {
            "bitget": {"credentials": "cred-a", "proxies": {"http": "http://127.0.0.1:8000"}},
            "gate": {"credentials": "cred-b", "proxies": {}},
        }
    )
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=dispatcher,
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        account_repository=FakeAccountRepository({"42": [FakeExchangeAccount(exchange="bitget"), FakeExchangeAccount(exchange="gate")]}),
        account_truth_resolver=resolver,
        env_mode="testnet",
        block_ms=1,
        region="main",
    )

    processed = await consumer.run(max_iterations=1)

    assert processed == 1
    assert resolver.calls[0]["user_id"] == "42"
    assert dispatcher.payloads[0][1] == {
        "bitget": {"credentials": "cred-a", "proxies": {"http": "http://127.0.0.1:8000"}},
        "gate": {"credentials": "cred-b", "proxies": {}},
    }
    assert repository.succeeded == ["task-1"]
```

- [ ] **Step 2: 再写失败测试，锁定账户真值缺失时明确失败**

```python
@pytest.mark.asyncio
async def test_executor_marks_task_failed_when_db_account_truth_resolution_fails():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_exec_tasks:node-a",
                [
                    (
                        "1-0",
                        {
                            "task_uuid": "task-1",
                            "user_id": "42",
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    class FailingExecutorAccountTruthResolver:
        def __init__(self, *, reason: str):
            self.reason = reason

        def resolve_accounts(self, **kwargs):
            raise RuntimeError(self.reason)

    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=FakeResolvedDispatcher(),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        account_repository=FakeAccountRepository({"42": []}),
        account_truth_resolver=FailingExecutorAccountTruthResolver(
            reason="executor_account_not_found"
        ),
        env_mode="testnet",
        block_ms=1,
        region="main",
    )

    processed = await consumer.run(max_iterations=1)

    assert processed == 0
    assert repository.failed == [("task-1", "executor_account_not_found")]
```

- [ ] **Step 3: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "resolves_db_accounts_before_dispatch or db_account_truth_resolution_fails" -v`

Expected: FAIL，当前 `RedisExecutionTaskConsumer` 还没有 `account_repository/account_truth_resolver/env_mode` 依赖，也仍要求 `credentials_by_exchange` 入参。

- [ ] **Step 4: 写最小实现，把 executor 账户真值解析接入 consumer**

```python
class RedisExecutionTaskConsumer(RedisSpotConsumer):
    def __init__(
        self,
        *,
        control_guard=None,
        task_repository=None,
        account_repository=None,
        account_truth_resolver=None,
        env_mode: str = "testnet",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.control_guard = control_guard
        self.task_repository = task_repository
        self.account_repository = account_repository
        self.account_truth_resolver = account_truth_resolver
        self.env_mode = env_mode

    async def run(self, *, max_iterations: int | None = None) -> int:
        iteration = 0
        processed = 0
        while max_iterations is None or iteration < max_iterations:
            entries = await self.redis_client.xread(
                {self.stream_key: self.last_id},
                count=1,
                block=self.block_ms,
            )
            for _, messages in entries:
                for message_id, payload in messages:
                    effective_payload = payload
        accounts = self.account_repository.list_enabled_accounts(
            user_id=int(payload["user_id"]),
            env_mode=self.env_mode,
        )
        execution_accounts_by_exchange = self.account_truth_resolver.resolve_accounts(
            accounts=list(accounts or []),
            user_id=str(payload["user_id"]),
            buy_exchange=str(payload["buy_exchange"]),
            sell_exchange=str(payload["sell_exchange"]),
            env_mode=self.env_mode,
            region=self.region,
        )
        await self.dispatcher.dispatch(
            effective_payload,
            execution_accounts_by_exchange=execution_accounts_by_exchange,
        )
```

- [ ] **Step 5: 重新运行定向测试**

Run: `python -m pytest tests/test_live_workers.py -k "resolves_db_accounts_before_dispatch or db_account_truth_resolution_fails or executor_marks_task_executing_and_succeeded" -v`

Expected: PASS，executor 已先解析 DB 账户再执行，且任务状态机保持兼容。

- [ ] **Step 6: 提交这一小步**

```bash
git add app/runtime/live_workers.py tests/test_live_workers.py
git commit -m "feat: resolve executor accounts from database before dispatch"
```

### Task 4: 完成 worker 注入与启动期 env 依赖收缩

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`

- [ ] **Step 1: 先写失败测试，锁定 executor worker 注入账户真值解析器**

```python
def test_default_worker_factory_builds_executor_worker_with_account_truth_dependencies():
    settings = WorkerSettings(
        worker_role="executor",
        database_enabled=True,
        database_url="sqlite://",
        env_mode="testnet",
        worker_region="main",
    )
    factory = DefaultWorkerFactory(settings=settings, event_router=FakeEventRouter())

    worker = factory.build_executor_worker(redis_client=FakeRedis())

    assert worker.consumer.account_repository is not None
    assert worker.consumer.account_truth_resolver is not None
    assert worker.consumer.env_mode == settings.env_mode
```

- [ ] **Step 2: 再写失败测试，锁定 executor 缺少 env 凭证时仍可启动到 worker.run**

```python
@pytest.mark.asyncio
async def test_worker_app_executor_does_not_require_env_exchange_credentials(monkeypatch):
    settings = WorkerSettings(
        worker_role="executor",
        spot_exchanges=["okx", "gate"],
        env_mode="testnet",
    )
    factory = FakeFactory()
    app = WorkerApp(
        settings=settings,
        redis_factory=lambda url: FakeRedis(),
        worker_factory=factory,
        event_router=FakeEventRouter(),
    )
    monkeypatch.delenv("OKX_API_KEY", raising=False)
    monkeypatch.delenv("OKX_SECRET", raising=False)

    await app.run()

    assert len(factory.executor_worker.calls) == 1
```

- [ ] **Step 3: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_worker_service.py -k "account_truth_dependencies or executor_does_not_require_env_exchange_credentials" -v`

Expected: FAIL，当前 `build_executor_worker()` 还不注入这些依赖，`WorkerApp.run()` 也仍会先执行全角色 env 凭证校验。

- [ ] **Step 4: 写最小实现，调整 factory 注入与 WorkerApp 分支**

```python
def build_executor_worker(self, *, redis_client: Redis) -> ConsumerWorker:
    dispatcher = RedisOpportunityDispatcher(self.spot_service)
    control_guard = ControlGuard(
        control_plane_loader=ControlPlaneLoader(ControlPlaneStore(redis_client)),
        event_router=self.event_router,
        service_name="executor",
        region=self.settings.worker_region,
    )
    task_repository = None
    account_repository = None
    account_truth_resolver = None
    if self.settings.database_enabled:
        session_factory = build_session_factory(self.settings.database_url)
        session = session_factory()
        task_repository = TaskRepository(session)
        account_repository = AccountRepository(session)
        account_truth_resolver = ExecutorAccountTruthResolver()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=dispatcher,
        stream_key=self.settings.resolved_executor_stream_key,
        control_guard=control_guard,
        task_repository=task_repository,
        account_repository=account_repository,
        account_truth_resolver=account_truth_resolver,
        env_mode=self.settings.env_mode,
        block_ms=self.settings.consumer_block_ms,
        event_router=self.event_router,
        region=self.settings.worker_region,
    )
    return ConsumerWorker(consumer=consumer)
```

```python
async def run(self) -> None:
    exchanges = self.settings.spot_exchanges
    router = self.event_router or build_event_router(
        self.alert_settings or get_alert_settings()
    )
    redis_client = self.redis_factory(self.settings.redis_url)
    factory = self.worker_factory or DefaultWorkerFactory(
        settings=self.settings,
        event_router=router,
    )
    if self.settings.worker_role == "executor":
        worker = factory.build_executor_worker(redis_client=redis_client)
        await worker.run(max_iterations=None)
        return

    credentials_by_exchange = load_exchange_credentials_from_env(exchanges)
    proxies_by_exchange = load_exchange_proxies_from_env(exchanges)
```

- [ ] **Step 5: 重新运行定向测试**

Run: `python -m pytest tests/test_worker_service.py -k "account_truth_dependencies or executor_does_not_require_env_exchange_credentials" -v`

Expected: PASS，executor 注入已就位，且启动期 env 凭证校验对 executor 已收缩。

- [ ] **Step 6: 提交这一小步**

```bash
git add app/runtime/worker_service.py tests/test_worker_service.py
git commit -m "feat: inject db account truth into executor worker"
```

### Task 5: 补 executor 失败分类回归与运维验证文档

**Files:**
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: 先写失败测试，锁定账户真值失败不进入交易所执行**

```python
@pytest.mark.asyncio
async def test_executor_account_truth_failure_does_not_call_spot_service():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_exec_tasks:node-a",
                [
                    (
                        "1-0",
                        {
                            "task_uuid": "task-1",
                            "user_id": "42",
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                        },
                    )
                ],
            )
        ]
    )
    service = FakeSpotService()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=FakeTaskRepository(task_uuid="task-1"),
        account_repository=FakeAccountRepository({"42": []}),
        account_truth_resolver=FailingExecutorAccountTruthResolver(
            reason="executor_account_not_found"
        ),
        env_mode="testnet",
        block_ms=1,
        region="main",
    )

    processed = await consumer.run(max_iterations=1)

    assert processed == 0
    assert service.calls == []
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `python -m pytest tests/test_live_workers.py -k "account_truth_failure_does_not_call_spot_service" -v`

Expected: FAIL，如果 executor 仍尝试直接调用 dispatcher 或 spot service，`service.calls` 将非空。

- [ ] **Step 3: 用最小代码修正并补回归**

```python
try:
    execution_accounts_by_exchange = self.account_truth_resolver.resolve_accounts(
        accounts=list(accounts or []),
        user_id=str(payload["user_id"]),
        buy_exchange=str(payload["buy_exchange"]),
        sell_exchange=str(payload["sell_exchange"]),
        env_mode=self.env_mode,
        region=self.region,
    )
except ExecutorAccountTruthError as exc:
    if task_uuid is not None and self.task_repository is not None:
        self.task_repository.mark_failed(task_uuid, reason=exc.reason)
    raise
```

Run: `python -m pytest tests/test_live_workers.py -k "executor_account_truth or executor_marks_task_failed or control_rule" -v`

Expected: PASS，账户真值失败不进入执行，且不回归现有任务失败与 control-rule 语义。

- [ ] **Step 4: 更新运维文档，新增 `Executor DB Account Truth Validation` 小节**

```md
### Executor DB Account Truth Validation

1. 为 canary 用户在 `bitget` 与 `gate` 准备两条启用、自动交易开启、`market_type_scope` 通过、`account_region` 兼容的数据库账户。
2. 保持 executor 节点环境变量不提供交易所 API 凭证，重启 `furun-spot-executor.service`。
3. 注入一条带 `user_id`、`buy_exchange=bitget`、`sell_exchange=gate` 的节点任务。
4. 预期结果：
   - 任务进入执行并成功或至少进入真实交易所调用阶段
   - 日志表明 executor 使用数据库账户真值装配凭证
   - 不因缺少 env 凭证而启动失败
5. 删除或破坏一边数据库账户后再次注入任务，预期：
   - 任务进入 `FAILED`
   - 失败原因为 `executor_account_not_found` 或对应账户真值错误
   - 不出现静默回退 env 凭证执行
6. 恢复数据库账户后再次注入同类任务，预期重新成功。
```

- [ ] **Step 5: 重新运行测试并校验文档文件**

Run: `python -m pytest tests/test_live_workers.py -k "executor_account_truth or executor_marks_task or control_rule" -v`

Expected: PASS，新增失败分类与既有 executor 控制链同时通过。

- [ ] **Step 6: 提交这一小步**

```bash
git add tests/test_live_workers.py docs/ops/live-workers-systemd.md
git commit -m "docs: add executor db account truth validation"
```

### Task 6: 全量验收与收尾检查

**Files:**
- Modify: `d:\old\FuRunSystemV4\app\runtime\executor_account_truth.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
- Modify: `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`
- Modify: `d:\old\FuRunSystemV4\tests\test_worker_service.py`
- Modify: `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

- [ ] **Step 1: 运行 executor 主链测试组**

Run: `python -m pytest tests/test_live_workers.py tests/test_redis_opportunity_flow.py tests/test_worker_service.py -v`

Expected: PASS，executor 账户真值迁移与相邻运行链测试全部兼容。

- [ ] **Step 2: 如有必要，补跑相邻任务状态与 dispatcher 回归**

Run: `python -m pytest tests/test_dispatch_user_repository.py tests/test_account_repository.py -v`

Expected: PASS，本轮未回归数据库账户发现与读取的基础行为。

- [ ] **Step 3: 检查最近编辑文件的诊断信息**

Run diagnostics for:
- `d:\old\FuRunSystemV4\app\runtime\executor_account_truth.py`
- `d:\old\FuRunSystemV4\app\runtime\live_workers.py`
- `d:\old\FuRunSystemV4\app\runtime\redis_flow.py`
- `d:\old\FuRunSystemV4\app\runtime\worker_service.py`
- `d:\old\FuRunSystemV4\tests\test_live_workers.py`
- `d:\old\FuRunSystemV4\tests\test_redis_opportunity_flow.py`
- `d:\old\FuRunSystemV4\tests\test_worker_service.py`
- `d:\old\FuRunSystemV4\docs\ops\live-workers-systemd.md`

Expected: 无新的语法错误、导入错误或明显静态检查错误。

- [ ] **Step 4: 做最终提交**

```bash
git add app/runtime/executor_account_truth.py app/runtime/live_workers.py app/runtime/redis_flow.py app/runtime/worker_service.py tests/test_live_workers.py tests/test_redis_opportunity_flow.py tests/test_worker_service.py docs/ops/live-workers-systemd.md
git commit -m "feat: move executor account truth to database"
```

## 自检结论

- `spec` 覆盖情况：
  - executor 执行主真值切到 DB：Task 1, Task 2, Task 3, Task 4
  - 不静默回退 env 凭证：Task 3, Task 4, Task 5
  - 首版不强制传 account ID：Task 3, Task 6
  - 失败语义与任务状态：Task 1, Task 3, Task 5
  - 运维验证与远端闭环准备：Task 5
- 无占位符、无 `TODO/TBD`、无未定义步骤引用
- 函数名、reason 名、字段名在各任务中保持一致：
  - `ExecutorAccountTruthResolver`
  - `ExecutorAccountTruthError`
  - `resolve_accounts`
  - `execution_accounts_by_exchange`
  - `executor_account_not_found`
  - `executor_account_decrypt_failed`
  - `executor_account_proxy_invalid`
