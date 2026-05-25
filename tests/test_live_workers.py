import pytest

from app.admin.control_plane import ControlDecision
from app.market.opportunity import SpotOpportunity
from app.runtime.runtime_events import RuntimeEvent
from app.runtime.executor_account_truth import (
    ExecutorAccountTruthError,
    ExecutorAccountTruthResolver,
)
from app.runtime.live_workers import (
    ContinuousSpotScanner,
    ExecutorPreflightError,
    ExecutorPreflightValidator,
    RedisNodeTaskDispatcher,
    RedisExecutionTaskConsumer,
    RedisSpotConsumer,
    _evaluate_account_exchange_coverage,
    _normalize_account_region,
    _parse_market_type_scope,
)
from app.runtime.redis_flow import (
    NodeExecutionTaskPublisher,
    RedisOpportunityDispatcher,
    UserNodeRouter,
)


class FakeFlowService:
    def __init__(self):
        self.calls = []

    async def run_once(self, **kwargs):
        self.calls.append(kwargs)
        return SpotOpportunity(
            symbol=kwargs["symbol"],
            buy_exchange="bitget",
            sell_exchange="gate",
            buy_ask=100.0,
            sell_bid=102.0,
            spread_bps=200.0,
            redis_member="bitget:gate:BTC/USDT:1",
            timestamp=123.0,
            effective_buy_price=100.5,
            effective_sell_price=101.5,
            target_quote_amount=100.0,
            buy_depth_levels_used=2,
            sell_depth_levels_used=3,
        )


class FakeDispatcher:
    def __init__(self):
        self.payloads = []
        self.calls = []

    async def dispatch(
        self,
        payload,
        *,
        credentials_by_exchange=None,
        execution_accounts_by_exchange=None,
        proxies_by_exchange=None,
    ):
        self.payloads.append((payload, credentials_by_exchange))
        self.calls.append(
            {
                "payload": payload,
                "credentials_by_exchange": credentials_by_exchange,
                "execution_accounts_by_exchange": execution_accounts_by_exchange,
                "proxies_by_exchange": proxies_by_exchange,
            }
        )
        return {"ok": True}


class FailingDispatcher(FakeDispatcher):
    async def dispatch(
        self,
        payload,
        *,
        credentials_by_exchange=None,
        execution_accounts_by_exchange=None,
        proxies_by_exchange=None,
    ):
        await super().dispatch(
            payload,
            credentials_by_exchange=credentials_by_exchange,
            execution_accounts_by_exchange=execution_accounts_by_exchange,
            proxies_by_exchange=proxies_by_exchange,
        )
        raise RuntimeError("dispatch boom")


class FakeSpotService:
    def __init__(self):
        self.calls = []
        self.result = {"ok": True}

    async def run_task(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class FakeTaskRepository:
    def __init__(self, *, task_uuid: str):
        self.task_uuid = task_uuid
        self.generated_task_uuids = [task_uuid]
        self.created = []
        self.dispatched = []
        self.executing = []
        self.succeeded = []
        self.failed = []
        self.blocked = []
        self.execution_results = []

    def create_task(self, data):
        self.created.append(data)
        task_uuid = self.generated_task_uuids.pop(0)
        return type("TaskRecord", (), {"task_uuid": task_uuid})()

    def mark_dispatched(self, task_uuid: str, *, worker_node_id: str):
        self.dispatched.append((task_uuid, worker_node_id))
        return None

    def mark_executing(self, task_uuid: str, *, worker_node_id: str):
        self.executing.append((task_uuid, worker_node_id))
        return None

    def mark_succeeded(self, task_uuid: str):
        self.succeeded.append(task_uuid)
        return None

    def mark_failed(self, task_uuid: str, *, reason: str):
        self.failed.append((task_uuid, reason))
        return None

    def mark_blocked(self, task_uuid: str, *, reason: str):
        self.blocked.append((task_uuid, reason))
        return None

    def mark_execution_result(self, task_uuid: str, **kwargs):
        self.execution_results.append((task_uuid, kwargs))
        return None


class FakeEventRouter:
    def __init__(self):
        self.events = []

    async def dispatch(self, event: RuntimeEvent):
        self.events.append(event)


def _find_event(events, event_type: str):
    return next(event for event in events if event.event_type == event_type)


class FakeControlGuard:
    def __init__(self, *, allowed: bool, approved_notional: float, reason: str | None):
        self.decision = ControlDecision(
            allowed=allowed,
            approved_notional=approved_notional,
            reason=reason,
        )
        self.calls = []

    async def evaluate(self, **kwargs):
        self.calls.append(kwargs)
        return self.decision


class SequenceControlGuard:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls = []

    async def evaluate(self, **kwargs):
        self.calls.append(kwargs)
        return self.decisions.pop(0)


class FakeStrategyConfig:
    def __init__(
        self,
        *,
        id: int,
        target_quote_amount: float,
        open_spread_bps_threshold: float = 0.0,
        symbol_scope_json=None,
        exchange_scope_json=None,
    ):
        self.id = id
        self.target_quote_amount = target_quote_amount
        self.open_spread_bps_threshold = open_spread_bps_threshold
        self.symbol_scope_json = symbol_scope_json or []
        self.exchange_scope_json = exchange_scope_json or []


class FakeStrategyConfigRepository:
    def __init__(self, strategies):
        self.strategies = list(strategies)
        self.calls = []

    def list_enabled_for_user(self, *, user_id: int, strategy_type: str = "spot_futures"):
        self.calls.append({"user_id": user_id, "strategy_type": strategy_type})
        return list(self.strategies)


class FakeDispatchUserRepository:
    def __init__(self, user_ids):
        self.user_ids = list(user_ids)
        self.calls = []

    def list_dispatchable_user_ids(self, *, env_mode: str) -> list[str]:
        self.calls.append({"env_mode": env_mode})
        return list(self.user_ids)


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


class FakeAccountRepository:
    def __init__(self, accounts_by_user_id):
        self.accounts_by_user_id = {
            str(user_id): list(accounts)
            for user_id, accounts in accounts_by_user_id.items()
        }
        self.calls = []

    def list_enabled_accounts(self, *, user_id: int, env_mode: str):
        self.calls.append({"user_id": user_id, "env_mode": env_mode})
        return list(self.accounts_by_user_id.get(str(user_id), []))


class FakeNullAccountRepository:
    def __init__(self):
        self.calls = []

    def list_enabled_accounts(self, *, user_id: int, env_mode: str):
        self.calls.append({"user_id": user_id, "env_mode": env_mode})
        return None


class FakeExecutorAccountTruthResolver:
    def __init__(self, *, resolved=None, error: Exception | None = None):
        self.resolved = resolved or {}
        self.error = error
        self.calls = []
        self.bound_calls = []

    def resolve_accounts(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.resolved

    def resolve_bound_accounts(self, **kwargs):
        self.bound_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.resolved


class BindingFailureExecutorAccountTruthError(ExecutorAccountTruthError):
    pass


class FailingBindingExecutorAccountTruthResolver:
    def __init__(self, *, reason: str):
        self.reason = reason
        self.calls = []
        self.bound_calls = []

    def resolve_accounts(self, **kwargs):
        self.calls.append(kwargs)
        raise AssertionError("binding failure should not fall back to resolve_accounts")

    def resolve_bound_accounts(self, **kwargs):
        self.bound_calls.append(kwargs)
        raise BindingFailureExecutorAccountTruthError(
            self.reason,
            user_id=str(kwargs["user_id"]),
            exchange=str(kwargs["buy_exchange"]),
            detail=f"binding failure for {kwargs['buy_account_id']}",
        )


class FakeSecretCipher:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def decrypt(self, ciphertext: str | None) -> str | None:
        self.calls.append(ciphertext)
        return self.values[ciphertext]


def test_parse_market_type_scope_normalizes_and_filters_values():
    assert _parse_market_type_scope(" spot , swap , futures , ") == {"spot", "swap"}


def test_parse_market_type_scope_treats_empty_and_none_as_empty_set():
    assert _parse_market_type_scope("") == set()
    assert _parse_market_type_scope("   ") == set()
    assert _parse_market_type_scope(None) == set()


def test_normalize_account_region_normalizes_case_and_whitespace():
    assert _normalize_account_region(" Main ") == "main"


def test_normalize_account_region_treats_empty_and_none_as_default():
    assert _normalize_account_region("") == "default"
    assert _normalize_account_region("   ") == "default"
    assert _normalize_account_region(None) == "default"


def test_fake_exchange_account_defaults_market_type_scope_to_spot_swap():
    account = FakeExchangeAccount(exchange="bitget")

    assert account.id == 1
    assert account.exchange == "bitget"
    assert account.is_auto_trade_enabled is True
    assert account.market_type_scope == "spot,swap"


def test_fake_exchange_account_defaults_account_region_to_default():
    account = FakeExchangeAccount(exchange="bitget")

    assert account.account_region == "default"
    assert account.market_type_scope == "spot,swap"
    assert account.api_key_ciphertext == "ak"
    assert account.secret_ciphertext == "sk"
    assert account.proxy_id is None
    assert account.proxy is None


def test_fake_exchange_account_accepts_custom_market_type_scope():
    account = FakeExchangeAccount(
        exchange="gate",
        is_auto_trade_enabled=False,
        market_type_scope="swap",
    )

    assert account.market_type_scope == "swap"
    assert account.is_auto_trade_enabled is False


def test_fake_exchange_account_accepts_custom_account_region():
    account = FakeExchangeAccount(
        exchange="gate",
        account_region="main",
        market_type_scope="swap",
    )

    assert account.account_region == "main"
    assert account.market_type_scope == "swap"


def test_fake_exchange_account_accepts_executor_truth_fields():
    proxy = object()
    account = FakeExchangeAccount(
        exchange="gate",
        account_id=9,
        api_key_ciphertext="ak-9",
        secret_ciphertext="sk-9",
        passphrase_ciphertext="pp-9",
        proxy_id=3,
        proxy=proxy,
    )

    assert account.id == 9
    assert account.api_key_ciphertext == "ak-9"
    assert account.secret_ciphertext == "sk-9"
    assert account.passphrase_ciphertext == "pp-9"
    assert account.proxy_id == 3
    assert account.proxy is proxy


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


def test_executor_account_truth_resolver_loads_accounts_by_binding_ids():
    accounts = [
        FakeExchangeAccount(
            exchange="bitget",
            account_id=101,
            api_key_ciphertext="ak-1",
            secret_ciphertext="sk-1",
        ),
        FakeExchangeAccount(
            exchange="gate",
            account_id=202,
            api_key_ciphertext="ak-2",
            secret_ciphertext="sk-2",
        ),
    ]
    cipher = FakeSecretCipher(
        {
            "ak-1": "plain-ak-1",
            "sk-1": "plain-sk-1",
            "ak-2": "plain-ak-2",
            "sk-2": "plain-sk-2",
        }
    )
    resolver = ExecutorAccountTruthResolver(secret_cipher=cipher)

    resolved = resolver.resolve_bound_accounts(
        accounts=accounts,
        user_id="42",
        buy_account_id="101",
        sell_account_id="202",
        buy_exchange="bitget",
        sell_exchange="gate",
        env_mode="testnet",
        region="main",
    )

    assert resolved["bitget"].account_id == 101
    assert resolved["gate"].account_id == 202


def test_executor_account_truth_resolver_raises_binding_not_found():
    resolver = ExecutorAccountTruthResolver(secret_cipher=FakeSecretCipher({}))

    with pytest.raises(ExecutorAccountTruthError) as exc_info:
        resolver.resolve_bound_accounts(
            accounts=[],
            user_id="42",
            buy_account_id="101",
            sell_account_id="202",
            buy_exchange="bitget",
            sell_exchange="gate",
            env_mode="testnet",
            region="main",
        )

    assert exc_info.value.reason == "executor_account_binding_not_found"


def test_evaluate_account_exchange_coverage_reports_market_type_scopes_by_exchange():
    payload = {"buy_exchange": "bitget", "sell_exchange": "gate"}
    accounts = [
        FakeExchangeAccount(exchange="bitget", market_type_scope="spot"),
        FakeExchangeAccount(exchange="gate", market_type_scope="swap"),
    ]

    coverage = _evaluate_account_exchange_coverage(payload=payload, accounts=accounts)

    assert coverage["available_exchanges"] == ["bitget", "gate"]
    assert coverage["auto_trade_enabled_exchanges"] == ["bitget", "gate"]
    assert coverage["market_type_scopes_by_exchange"] == {
        "bitget": ["spot"],
        "gate": ["swap"],
    }
    assert coverage["allowed_market_types"] == ["spot", "swap"]
    assert coverage["has_market_type_coverage"] is True


def test_evaluate_account_exchange_coverage_fails_when_one_side_scope_is_empty():
    payload = {"buy_exchange": "bitget", "sell_exchange": "gate"}
    accounts = [
        FakeExchangeAccount(exchange="bitget", market_type_scope="spot"),
        FakeExchangeAccount(exchange="gate", market_type_scope=""),
    ]

    coverage = _evaluate_account_exchange_coverage(payload=payload, accounts=accounts)

    assert coverage["market_type_scopes_by_exchange"] == {
        "bitget": ["spot"],
        "gate": [],
    }
    assert coverage["has_exchange_coverage"] is True
    assert coverage["has_auto_trade_coverage"] is True
    assert coverage["has_market_type_coverage"] is False


def test_evaluate_account_exchange_coverage_reports_account_regions_by_exchange():
    payload = {"buy_exchange": "bitget", "sell_exchange": "gate"}
    accounts = [
        FakeExchangeAccount(exchange="bitget", account_region="default"),
        FakeExchangeAccount(exchange="gate", account_region="main"),
    ]

    coverage = _evaluate_account_exchange_coverage(
        payload=payload,
        accounts=accounts,
        dispatcher_region="main",
    )

    assert coverage["account_regions_by_exchange"] == {
        "bitget": ["default"],
        "gate": ["main"],
    }
    assert coverage["dispatcher_region"] == "main"
    assert coverage["has_region_coverage"] is True


def test_evaluate_account_exchange_coverage_fails_when_one_side_region_is_incompatible():
    payload = {"buy_exchange": "bitget", "sell_exchange": "gate"}
    accounts = [
        FakeExchangeAccount(exchange="bitget", account_region="main"),
        FakeExchangeAccount(exchange="gate", account_region="hk"),
    ]

    coverage = _evaluate_account_exchange_coverage(
        payload=payload,
        accounts=accounts,
        dispatcher_region="main",
    )

    assert coverage["account_regions_by_exchange"] == {
        "bitget": ["main"],
        "gate": ["hk"],
    }
    assert coverage["has_market_type_coverage"] is True
    assert coverage["has_region_coverage"] is False


def test_executor_preflight_validator_rejects_missing_required_fields():
    validator = ExecutorPreflightValidator()

    with pytest.raises(ExecutorPreflightError) as exc_info:
        validator.validate(
            payload={
                "user_id": "42",
                "symbol": "BTC/USDT",
                "buy_exchange": "okx",
            },
            execution_accounts_by_exchange=None,
        )

    assert exc_info.value.reason == "executor_preflight_invalid_payload"


def test_executor_preflight_validator_rejects_same_exchange():
    validator = ExecutorPreflightValidator()

    with pytest.raises(ExecutorPreflightError) as exc_info:
        validator.validate(
            payload={
                "task_uuid": "task-1",
                "user_id": "42",
                "symbol": "BTC/USDT",
                "buy_exchange": "okx",
                "sell_exchange": "okx",
                "target_quote_amount": "40.0",
            },
            execution_accounts_by_exchange=None,
        )

    assert exc_info.value.reason == "executor_preflight_same_exchange"


def test_executor_preflight_validator_rejects_invalid_amount():
    validator = ExecutorPreflightValidator()

    with pytest.raises(ExecutorPreflightError) as exc_info:
        validator.validate(
            payload={
                "task_uuid": "task-1",
                "user_id": "42",
                "symbol": "BTC/USDT",
                "buy_exchange": "okx",
                "sell_exchange": "gate",
                "target_quote_amount": "0",
            },
            execution_accounts_by_exchange=None,
        )

    assert exc_info.value.reason == "executor_preflight_invalid_amount"


def test_executor_preflight_validator_rejects_missing_binding_resolution():
    validator = ExecutorPreflightValidator()

    with pytest.raises(ExecutorPreflightError) as exc_info:
        validator.validate(
            payload={
                "task_uuid": "task-1",
                "user_id": "42",
                "symbol": "BTC/USDT",
                "buy_exchange": "okx",
                "sell_exchange": "gate",
                "buy_account_id": "101",
                "sell_account_id": "202",
                "target_quote_amount": "40.0",
            },
            execution_accounts_by_exchange={},
        )

    assert exc_info.value.reason == "executor_preflight_account_resolution_failed"


def test_executor_preflight_validator_rejects_account_exchange_mismatch():
    validator = ExecutorPreflightValidator()
    wrong_buy_account = type(
        "ResolvedAccount",
        (),
        {"exchange": "bitget", "credentials": "cred-a", "proxies": {}},
    )()
    sell_account = type(
        "ResolvedAccount",
        (),
        {"exchange": "gate", "credentials": "cred-b", "proxies": {}},
    )()

    with pytest.raises(ExecutorPreflightError) as exc_info:
        validator.validate(
            payload={
                "task_uuid": "task-1",
                "user_id": "42",
                "symbol": "BTC/USDT",
                "buy_exchange": "okx",
                "sell_exchange": "gate",
                "buy_account_id": "101",
                "sell_account_id": "202",
                "target_quote_amount": "40.0",
            },
            execution_accounts_by_exchange={
                "okx": wrong_buy_account,
                "gate": sell_account,
            },
        )

    assert exc_info.value.reason == "executor_preflight_account_exchange_mismatch"


@pytest.mark.asyncio
async def test_executor_preflight_same_exchange_marks_task_failed_without_dispatch():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "okx",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    service = FakeSpotService()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object()},
        max_iterations=1,
    )

    assert processed == 0
    assert service.calls == []
    assert repository.executing == [("task-1", "node-a")]
    assert repository.failed == [("task-1", "executor_preflight_same_exchange")]


@pytest.mark.asyncio
async def test_executor_preflight_binding_resolution_failed_without_dispatch():
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
                            "buy_account_id": "101",
                            "sell_account_id": "202",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    service = FakeSpotService()
    resolver = FakeExecutorAccountTruthResolver(resolved={})
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget"),
                    FakeExchangeAccount(exchange="gate"),
                ]
            }
        ),
        account_truth_resolver=resolver,
        env_mode="testnet",
        block_ms=1,
        region="node-a",
    )

    processed = await consumer.run(max_iterations=1)

    assert processed == 0
    assert service.calls == []
    assert repository.executing == [("task-1", "node-a")]
    assert repository.failed == [
        ("task-1", "executor_preflight_account_resolution_failed")
    ]


class FakeRedis:
    def __init__(self, *, xread_messages=None):
        self.read_calls = 0
        self.route_values = {}
        self.xadds = []
        self.xread_messages = xread_messages

    async def xread(self, streams, count=1, block=0):
        self.read_calls += 1
        if self.xread_messages is not None:
            if self.read_calls == 1:
                return self.xread_messages
            return []
        if self.read_calls == 1:
            return [
                (
                    "stream:spot_opps",
                    [
                        (
                            "1-0",
                            {
                                "symbol": "BTC/USDT",
                                "buy_exchange": "bitget",
                                "sell_exchange": "gate",
                            },
                        )
                    ],
                )
            ]
        return []

    async def get(self, key):
        return self.route_values.get(key)

    async def xadd(self, key, fields):
        self.xadds.append((key, fields))
        return "2-0"


@pytest.mark.asyncio
async def test_continuous_scanner_scans_each_symbol_for_every_iteration():
    service = FakeFlowService()
    scanner = ContinuousSpotScanner(flow_service=service, poll_interval_seconds=0.0)

    await scanner.run(
        exchanges=["okx", "bitget", "gate"],
        credentials_by_exchange={"okx": object(), "bitget": object(), "gate": object()},
        symbols=["BTC/USDT", "ETH/USDT"],
        max_iterations=2,
    )

    assert len(service.calls) == 4
    assert service.calls[0]["symbol"] == "BTC/USDT"
    assert service.calls[1]["symbol"] == "ETH/USDT"
    assert service.calls[2]["symbol"] == "BTC/USDT"
    assert service.calls[3]["symbol"] == "ETH/USDT"


@pytest.mark.asyncio
async def test_continuous_scanner_emits_event_from_spot_opportunity_attributes():
    service = FakeFlowService()
    router = FakeEventRouter()
    scanner = ContinuousSpotScanner(
        flow_service=service,
        poll_interval_seconds=0.0,
        event_router=router,
        region="default",
    )

    await scanner.run(
        exchanges=["okx", "bitget", "gate"],
        credentials_by_exchange={"okx": object(), "bitget": object(), "gate": object()},
        symbols=["BTC/USDT"],
        max_iterations=1,
    )

    assert router.events[0].event_type == "opportunity.detected"
    assert router.events[0].payload["buy_exchange"] == "bitget"
    assert router.events[0].payload["sell_exchange"] == "gate"
    assert router.events[0].payload["spread_bps"] == 200.0


@pytest.mark.asyncio
async def test_continuous_scanner_emits_detected_event_for_each_active_symbol():
    service = FakeFlowService()
    router = FakeEventRouter()
    scanner = ContinuousSpotScanner(
        flow_service=service,
        poll_interval_seconds=0.0,
        event_router=router,
        region="default",
    )

    await scanner.run(
        exchanges=["okx", "bitget", "gate"],
        credentials_by_exchange={"okx": object(), "bitget": object(), "gate": object()},
        symbols=["BTC/USDT", "ETH/USDT"],
        max_iterations=1,
    )

    detected_symbols = [
        event.symbol
        for event in router.events
        if event.event_type == "opportunity.detected"
    ]

    assert detected_symbols == ["BTC/USDT", "ETH/USDT"]


@pytest.mark.asyncio
async def test_continuous_scanner_forwards_depth_limit_and_quote_amount():
    service = FakeFlowService()
    scanner = ContinuousSpotScanner(flow_service=service, poll_interval_seconds=0.0)

    await scanner.run(
        exchanges=["okx", "bitget", "gate"],
        credentials_by_exchange={"okx": object(), "bitget": object(), "gate": object()},
        symbols=["BTC/USDT"],
        orderbook_depth_limit=9,
        target_quote_amount=250.0,
        max_iterations=1,
    )

    assert service.calls[0]["orderbook_depth_limit"] == 9
    assert service.calls[0]["target_quote_amount"] == 250.0


@pytest.mark.asyncio
async def test_redis_consumer_reads_stream_and_dispatches_payload():
    dispatcher = FakeDispatcher()
    consumer = RedisSpotConsumer(
        redis_client=FakeRedis(),
        dispatcher=dispatcher,
        stream_key="stream:spot_opps",
        block_ms=0,
    )

    processed = await consumer.run(
        credentials_by_exchange={"bitget": object(), "gate": object()},
        max_iterations=2,
    )

    payload, credentials = dispatcher.payloads[0]
    assert processed == 1
    assert payload["buy_exchange"] == "bitget"
    assert payload["sell_exchange"] == "gate"
    assert set(credentials.keys()) == {"bitget", "gate"}


@pytest.mark.asyncio
async def test_redis_consumer_accepts_event_router_without_affecting_dispatch():
    dispatcher = FakeDispatcher()
    router = FakeEventRouter()
    consumer = RedisSpotConsumer(
        redis_client=FakeRedis(),
        dispatcher=dispatcher,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
        region="default",
    )

    processed = await consumer.run(
        credentials_by_exchange={"bitget": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert dispatcher.payloads[0][0]["symbol"] == "BTC/USDT"


@pytest.mark.asyncio
async def test_dispatcher_worker_routes_public_opportunity_into_node_stream():
    redis_client = FakeRedis()
    redis_client.route_values = {"route:user_node:42": "node-a"}
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        stream_key="stream:spot_opps",
        block_ms=0,
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert redis_client.xadds[0][0] == "stream:spot_exec_tasks:node-a"
    assert redis_client.xadds[0][1]["user_id"] == "42"
    assert redis_client.xadds[0][1]["source_message_id"] == "1-0"


@pytest.mark.asyncio
async def test_dispatcher_creates_database_task_before_publishing_node_task():
    redis_client = FakeRedis()
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeTaskRepository(task_uuid="task-1")
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert repository.created[0].user_id == 42
    assert repository.created[0].opportunity_id == "1-0"
    assert redis_client.xadds[0][1]["task_uuid"] == "task-1"
    assert repository.dispatched == [("task-1", "node-a")]


@pytest.mark.asyncio
async def test_dispatcher_uses_db_discovered_users_when_dispatch_user_ids_is_empty():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeTaskRepository(task_uuid="task-seq")
    repository.generated_task_uuids = ["task-1"]
    strategy_repository = FakeStrategyConfigRepository(
        [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
    )
    dispatch_user_repository = FakeDispatchUserRepository(["42"])
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=dispatch_user_repository,
        strategy_repository=strategy_repository,
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert dispatch_user_repository.calls == [{"env_mode": "testnet"}]
    assert repository.created[0].user_id == 42


@pytest.mark.asyncio
async def test_dispatcher_filters_explicit_dispatch_user_ids_by_database_eligibility():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {
        "route:user_node:42": "node-a",
        "route:user_node:99": "node-b",
    }
    repository = FakeTaskRepository(task_uuid="task-seq")
    repository.generated_task_uuids = ["task-1"]
    strategy_repository = FakeStrategyConfigRepository(
        [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
    )
    dispatch_user_repository = FakeDispatchUserRepository(["42"])
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42", "99"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=dispatch_user_repository,
        strategy_repository=strategy_repository,
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
    )

    await dispatcher.run(max_iterations=1)

    assert dispatch_user_repository.calls == [{"env_mode": "testnet"}]
    assert [item.user_id for item in repository.created] == [42]
    assert all(payload["user_id"] == "42" for _, payload in redis_client.xadds)


@pytest.mark.asyncio
async def test_dispatcher_requires_accounts_for_both_buy_and_sell_exchanges():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    repository = FakeTaskRepository(task_uuid="task-seq")
    repository.generated_task_uuids = ["task-1"]
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget"),
                    FakeExchangeAccount(exchange="gate"),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    assert [item.user_id for item in repository.created] == [42]
    assert len(redis_client.xadds) == 1
    assert all(
        event.payload.get("reason") != "account_exchange_coverage_missing"
        for event in router.events
    )


@pytest.mark.asyncio
async def test_dispatcher_requires_auto_trade_enabled_for_both_buy_and_sell_exchanges():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeTaskRepository(task_uuid="task-seq")
    repository.generated_task_uuids = ["task-1"]
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(
                        exchange="bitget",
                        is_auto_trade_enabled=True,
                    ),
                    FakeExchangeAccount(
                        exchange="gate",
                        is_auto_trade_enabled=True,
                    ),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
    )

    await dispatcher.run(max_iterations=1)

    assert [item.user_id for item in repository.created] == [42]
    assert len(redis_client.xadds) == 1


@pytest.mark.asyncio
async def test_dispatcher_skips_user_when_buy_exchange_auto_trade_is_disabled():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    repository = FakeTaskRepository(task_uuid="task-seq")
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(
                        exchange="bitget",
                        is_auto_trade_enabled=False,
                    ),
                    FakeExchangeAccount(
                        exchange="gate",
                        is_auto_trade_enabled=True,
                    ),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    assert repository.created == []
    assert redis_client.xadds == []
    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_auto_trade_disabled"
    assert skipped_event.payload["available_exchanges"] == ["bitget", "gate"]
    assert skipped_event.payload["auto_trade_enabled_exchanges"] == ["gate"]


@pytest.mark.asyncio
async def test_dispatcher_skips_user_when_sell_exchange_auto_trade_is_disabled():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    repository = FakeTaskRepository(task_uuid="task-seq")
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(
                        exchange="bitget",
                        is_auto_trade_enabled=True,
                    ),
                    FakeExchangeAccount(
                        exchange="gate",
                        is_auto_trade_enabled=False,
                    ),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    assert repository.created == []
    assert redis_client.xadds == []
    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_auto_trade_disabled"
    assert skipped_event.payload["available_exchanges"] == ["bitget", "gate"]
    assert skipped_event.payload["auto_trade_enabled_exchanges"] == ["bitget"]


@pytest.mark.asyncio
async def test_dispatcher_skips_user_when_buy_exchange_market_type_scope_is_missing():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    repository = FakeTaskRepository(task_uuid="task-seq")
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget", market_type_scope=""),
                    FakeExchangeAccount(exchange="gate", market_type_scope="swap"),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    assert repository.created == []
    assert redis_client.xadds == []
    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_market_type_scope_missing"
    assert skipped_event.payload["market_type_scopes_by_exchange"] == {
        "bitget": [],
        "gate": ["swap"],
    }
    assert skipped_event.payload["allowed_market_types"] == ["spot", "swap"]


@pytest.mark.asyncio
async def test_dispatcher_skips_user_when_sell_exchange_market_type_scope_is_missing():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    repository = FakeTaskRepository(task_uuid="task-seq")
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget", market_type_scope="spot"),
                    FakeExchangeAccount(exchange="gate", market_type_scope=""),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    assert repository.created == []
    assert redis_client.xadds == []
    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_market_type_scope_missing"
    assert skipped_event.payload["market_type_scopes_by_exchange"] == {
        "bitget": ["spot"],
        "gate": [],
    }
    assert skipped_event.payload["allowed_market_types"] == ["spot", "swap"]


@pytest.mark.asyncio
async def test_dispatcher_skips_user_when_account_region_does_not_match_dispatcher():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    repository = FakeTaskRepository(task_uuid="task-seq")
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget", account_region="default"),
                    FakeExchangeAccount(exchange="gate", account_region="hk"),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
        region="main",
    )

    await dispatcher.run(max_iterations=1)

    assert repository.created == []
    assert redis_client.xadds == []
    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_region_mismatch"
    assert skipped_event.payload["dispatcher_region"] == "main"
    assert skipped_event.payload["account_regions_by_exchange"] == {
        "bitget": ["default"],
        "gate": ["hk"],
    }


@pytest.mark.asyncio
async def test_dispatcher_allows_user_when_account_region_is_default():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    repository = FakeTaskRepository(task_uuid="task-seq")
    repository.generated_task_uuids = ["task-1"]
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget", account_region="default"),
                    FakeExchangeAccount(exchange="gate", account_region="main"),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
        region="main",
    )

    await dispatcher.run(max_iterations=1)

    assert [item.user_id for item in repository.created] == [42]
    assert len(redis_client.xadds) == 1
    assert all(
        event.payload.get("reason") != "account_region_mismatch"
        for event in router.events
    )


@pytest.mark.asyncio
async def test_dispatcher_allows_user_when_any_account_per_exchange_matches_dispatcher_region():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    repository = FakeTaskRepository(task_uuid="task-seq")
    repository.generated_task_uuids = ["task-1"]
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget", account_region="hk"),
                    FakeExchangeAccount(exchange="bitget", account_region="main"),
                    FakeExchangeAccount(exchange="gate", account_region="jp"),
                    FakeExchangeAccount(exchange="gate", account_region="default"),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
        region="main",
    )

    await dispatcher.run(max_iterations=1)

    assert [item.user_id for item in repository.created] == [42]
    assert len(redis_client.xadds) == 1
    assert all(
        event.payload.get("reason") != "account_region_mismatch"
        for event in router.events
    )


@pytest.mark.asyncio
async def test_dispatcher_allows_user_when_one_account_per_exchange_matches_market_type_scope():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeTaskRepository(task_uuid="task-seq")
    repository.generated_task_uuids = ["task-1"]
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget", market_type_scope=""),
                    FakeExchangeAccount(exchange="bitget", market_type_scope="spot"),
                    FakeExchangeAccount(exchange="gate", market_type_scope="swap"),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
    )

    await dispatcher.run(max_iterations=1)

    assert [item.user_id for item in repository.created] == [42]
    assert len(redis_client.xadds) == 1


@pytest.mark.asyncio
async def test_dispatcher_selects_bound_accounts_and_persists_them_on_task():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeTaskRepository(task_uuid="task-1")
    repository.generated_task_uuids = ["task-1"]
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget", account_id=101),
                    FakeExchangeAccount(exchange="gate", account_id=202),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        region="main",
    )

    await dispatcher.run(max_iterations=1)

    assert repository.created[0].buy_account_id == 101
    assert repository.created[0].sell_account_id == 202


@pytest.mark.asyncio
async def test_dispatcher_publishes_bound_accounts_to_node_stream():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeTaskRepository(task_uuid="task-1")
    repository.generated_task_uuids = ["task-1"]
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget", account_id=101),
                    FakeExchangeAccount(exchange="gate", account_id=202),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        region="main",
    )

    await dispatcher.run(max_iterations=1)

    assert redis_client.xadds[0][1]["buy_account_id"] == "101"
    assert redis_client.xadds[0][1]["sell_account_id"] == "202"


@pytest.mark.asyncio
async def test_dispatcher_selects_lowest_account_id_per_exchange_for_bound_accounts():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeTaskRepository(task_uuid="task-1")
    repository.generated_task_uuids = ["task-1"]
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget", account_id=305),
                    FakeExchangeAccount(exchange="bitget", account_id=101),
                    FakeExchangeAccount(exchange="gate", account_id=202),
                    FakeExchangeAccount(exchange="gate", account_id=404),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        region="main",
    )

    await dispatcher.run(max_iterations=1)

    assert repository.created[0].buy_account_id == 101
    assert repository.created[0].sell_account_id == 202


@pytest.mark.asyncio
async def test_dispatcher_skips_user_when_account_exchange_coverage_is_incomplete():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    repository = FakeTaskRepository(task_uuid="task-seq")
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {"42": [FakeExchangeAccount(exchange="bitget")]}
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    assert repository.created == []
    assert redis_client.xadds == []
    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_exchange_coverage_missing"
    assert skipped_event.payload["available_exchanges"] == ["bitget"]


@pytest.mark.asyncio
async def test_dispatcher_treats_null_account_results_as_missing_exchange_coverage():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    repository = FakeTaskRepository(task_uuid="task-seq")
    account_repository = FakeNullAccountRepository()
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=account_repository,
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    assert account_repository.calls == [{"user_id": 42, "env_mode": "testnet"}]
    assert repository.created == []
    assert redis_client.xadds == []
    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_exchange_coverage_missing"
    assert skipped_event.payload["available_exchanges"] == []
    assert skipped_event.payload["auto_trade_enabled_exchanges"] == []


@pytest.mark.asyncio
async def test_dispatcher_account_auto_trade_skip_does_not_emit_control_rule_events():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    guard = FakeControlGuard(
        allowed=False,
        approved_notional=0.0,
        reason="reduce_only",
    )
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(
                        exchange="bitget",
                        is_auto_trade_enabled=False,
                    ),
                    FakeExchangeAccount(
                        exchange="gate",
                        is_auto_trade_enabled=True,
                    ),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        control_guard=guard,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    assert guard.calls == []
    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_auto_trade_disabled"
    assert all(not event.event_type.startswith("control.rule.") for event in router.events)


@pytest.mark.asyncio
async def test_dispatcher_market_type_scope_skip_does_not_emit_control_rule_events():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    guard = FakeControlGuard(
        allowed=False,
        approved_notional=0.0,
        reason="reduce_only",
    )
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget", market_type_scope=""),
                    FakeExchangeAccount(exchange="gate", market_type_scope="swap"),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        control_guard=guard,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    assert guard.calls == []
    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_market_type_scope_missing"
    assert all(not event.event_type.startswith("control.rule.") for event in router.events)


@pytest.mark.asyncio
async def test_dispatcher_account_region_skip_does_not_emit_control_rule_events():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    guard = FakeControlGuard(
        allowed=False,
        approved_notional=0.0,
        reason="reduce_only",
    )
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {
                "42": [
                    FakeExchangeAccount(exchange="bitget", account_region="hk"),
                    FakeExchangeAccount(exchange="gate", account_region="main"),
                ]
            }
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        control_guard=guard,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
        region="main",
    )

    await dispatcher.run(max_iterations=1)

    assert guard.calls == []
    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_region_mismatch"
    assert all(not event.event_type.startswith("control.rule.") for event in router.events)


@pytest.mark.asyncio
async def test_dispatcher_account_exchange_coverage_skip_does_not_emit_control_rule_events():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    guard = FakeControlGuard(
        allowed=False,
        approved_notional=0.0,
        reason="reduce_only",
    )
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {"42": [FakeExchangeAccount(exchange="bitget")]}
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        control_guard=guard,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    assert guard.calls == []
    assert all(not event.event_type.startswith("control.rule.") for event in router.events)


@pytest.mark.asyncio
async def test_dispatcher_skips_user_when_only_sell_exchange_account_exists():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=FakeAccountRepository(
            {"42": [FakeExchangeAccount(exchange="gate")]}
        ),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
    )

    await dispatcher.run(max_iterations=1)

    assert redis_client.xadds == []
    skipped_event = next(
        event for event in router.events if event.event_type == "dispatcher.user.skipped"
    )
    assert skipped_event.payload["reason"] == "account_exchange_coverage_missing"
    assert skipped_event.payload["available_exchanges"] == ["gate"]


@pytest.mark.asyncio
async def test_dispatcher_keeps_explicit_dispatch_user_ids_intersection_before_account_coverage():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {
        "route:user_node:42": "node-a",
        "route:user_node:99": "node-b",
    }
    repository = FakeTaskRepository(task_uuid="task-seq")
    repository.generated_task_uuids = ["task-1"]
    account_repository = FakeAccountRepository(
        {
            "42": [
                FakeExchangeAccount(exchange="bitget"),
                FakeExchangeAccount(exchange="gate"),
            ],
            "99": [
                FakeExchangeAccount(exchange="bitget"),
                FakeExchangeAccount(exchange="gate"),
            ],
        }
    )
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42", "99"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        account_repository=account_repository,
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
    )

    await dispatcher.run(max_iterations=1)

    assert [item.user_id for item in repository.created] == [42]
    assert all(payload["user_id"] == "42" for _, payload in redis_client.xadds)
    assert account_repository.calls == [{"user_id": 42, "env_mode": "testnet"}]


@pytest.mark.asyncio
async def test_dispatcher_emits_discovery_succeeded_event_with_candidate_user_ids():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    repository = FakeTaskRepository(task_uuid="task-seq")
    repository.generated_task_uuids = ["task-1"]
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
        region="main",
    )

    await dispatcher.run(max_iterations=1)

    assert router.events[0].event_type == "dispatcher.user.discovery.succeeded"
    assert router.events[0].payload["candidate_user_ids"] == ["42"]


@pytest.mark.asyncio
async def test_dispatcher_emits_user_skipped_event_when_route_missing():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    router = FakeEventRouter()
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=[],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        dispatch_user_repository=FakeDispatchUserRepository(["42"]),
        strategy_repository=FakeStrategyConfigRepository(
            [FakeStrategyConfig(id=11, target_quote_amount=80.0)]
        ),
        stream_key="stream:spot_opps",
        block_ms=0,
        event_router=router,
        region="main",
    )

    await dispatcher.run(max_iterations=1)

    assert router.events[1].event_type == "dispatcher.user.skipped"
    assert router.events[1].payload["user_id"] == "42"
    assert router.events[1].payload["reason"] == "user_route_missing"


@pytest.mark.asyncio
async def test_dispatcher_creates_one_task_per_matching_strategy():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                            "target_quote_amount": "15.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeTaskRepository(task_uuid="task-seq")
    repository.generated_task_uuids = ["task-1", "task-2"]
    strategy_repository = FakeStrategyConfigRepository(
        [
            FakeStrategyConfig(
                id=11,
                target_quote_amount=80.0,
                open_spread_bps_threshold=10.0,
                symbol_scope_json=["BTC/USDT"],
                exchange_scope_json=["bitget", "gate"],
            ),
            FakeStrategyConfig(
                id=12,
                target_quote_amount=35.0,
                open_spread_bps_threshold=20.0,
                symbol_scope_json=[],
                exchange_scope_json=[],
            ),
        ]
    )
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        strategy_repository=strategy_repository,
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert [item.strategy_config_id for item in repository.created] == [11, 12]
    assert [item.target_notional for item in repository.created] == [80.0, 35.0]
    assert [item.idempotency_key for item in repository.created] == [
        "42:1-0:open:11",
        "42:1-0:open:12",
    ]
    assert redis_client.xadds[0][1]["strategy_config_id"] == "11"
    assert redis_client.xadds[0][1]["target_quote_amount"] == "80.0"
    assert redis_client.xadds[1][1]["strategy_config_id"] == "12"
    assert redis_client.xadds[1][1]["target_quote_amount"] == "35.0"


@pytest.mark.asyncio
async def test_dispatcher_passes_strategy_id_into_control_guard_and_blocks_per_strategy():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "25.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeTaskRepository(task_uuid="task-seq")
    repository.generated_task_uuids = ["task-1", "task-2"]
    strategy_repository = FakeStrategyConfigRepository(
        [
            FakeStrategyConfig(id=11, target_quote_amount=80.0),
            FakeStrategyConfig(id=12, target_quote_amount=50.0),
        ]
    )
    guard = SequenceControlGuard(
        [
            ControlDecision(allowed=False, approved_notional=0.0, reason="reduce_only"),
            ControlDecision(allowed=True, approved_notional=40.0, reason=None),
        ]
    )
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        strategy_repository=strategy_repository,
        control_guard=guard,
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
    )

    await dispatcher.run(max_iterations=1)

    assert [call["strategy_id"] for call in guard.calls] == [11, 12]
    assert repository.blocked == [("task-1", "reduce_only")]
    assert repository.dispatched == [("task-2", "node-a")]
    assert redis_client.xadds == [
        (
            "stream:spot_exec_tasks:node-a",
            {
                "symbol": "BTC/USDT",
                "buy_exchange": "bitget",
                "sell_exchange": "gate",
                "spread_bps": "25.0",
                "user_id": "42",
                "source_message_id": "1-0",
                "task_uuid": "task-2",
                "strategy_config_id": "12",
                "target_quote_amount": "40.0",
            },
        )
    ]


@pytest.mark.asyncio
async def test_dispatcher_skips_user_when_no_strategy_matches_opportunity():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "ETH/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "spread_bps": "8.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    repository = FakeTaskRepository(task_uuid="task-1")
    strategy_repository = FakeStrategyConfigRepository(
        [
            FakeStrategyConfig(
                id=11,
                target_quote_amount=80.0,
                open_spread_bps_threshold=15.0,
                symbol_scope_json=["BTC/USDT"],
                exchange_scope_json=["bitget", "gate"],
            )
        ]
    )
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        strategy_repository=strategy_repository,
        task_repository=repository,
        stream_key="stream:spot_opps",
        block_ms=0,
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert repository.created == []
    assert redis_client.xadds == []


@pytest.mark.asyncio
async def test_dispatcher_blocks_when_platform_reduce_only_is_active():
    redis_client = FakeRedis()
    redis_client.route_values = {"route:user_node:42": "node-a"}
    guard = FakeControlGuard(
        allowed=False,
        approved_notional=0.0,
        reason="reduce_only",
    )
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        stream_key="stream:spot_opps",
        control_guard=guard,
        block_ms=0,
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert redis_client.xadds == []
    assert guard.calls[0]["user_id"] == "42"


@pytest.mark.asyncio
async def test_dispatcher_emits_control_rule_blocked_event():
    redis_client = FakeRedis()
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        stream_key="stream:spot_opps",
        control_guard=FakeControlGuard(
            allowed=False,
            approved_notional=0.0,
            reason="reduce_only",
        ),
        block_ms=0,
        event_router=router,
        region="main",
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert redis_client.xadds == []
    event = next(
        item for item in router.events if item.event_type == "control.rule.blocked"
    )
    assert event.service == "dispatcher"
    assert event.region == "main"
    assert event.symbol == "BTC/USDT"
    assert event.exchange == "bitget"
    assert event.payload["user_id"] == "42"
    assert event.payload["source_message_id"] == "1-0"
    assert event.payload["requested_notional"] == 15.0
    assert event.payload["approved_notional"] == 0.0
    assert event.payload["reason"] == "reduce_only"


@pytest.mark.asyncio
async def test_dispatcher_resizes_target_quote_amount_before_publishing_task():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "target_quote_amount": "50.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    guard = FakeControlGuard(
        allowed=True,
        approved_notional=35.0,
        reason=None,
    )
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        stream_key="stream:spot_opps",
        control_guard=guard,
        block_ms=0,
    )

    await dispatcher.run(max_iterations=1)

    assert redis_client.xadds[0][1]["target_quote_amount"] == "35.0"


@pytest.mark.asyncio
async def test_dispatcher_emits_control_rule_resized_event():
    redis_client = FakeRedis(
        xread_messages=[
            (
                "stream:spot_opps",
                [
                    (
                        "1-0",
                        {
                            "symbol": "BTC/USDT",
                            "buy_exchange": "bitget",
                            "sell_exchange": "gate",
                            "target_quote_amount": "50.0",
                        },
                    )
                ],
            )
        ]
    )
    redis_client.route_values = {"route:user_node:42": "node-a"}
    router = FakeEventRouter()
    dispatcher = RedisNodeTaskDispatcher(
        redis_client=redis_client,
        user_ids=["42"],
        route_resolver=UserNodeRouter(redis_client),
        task_publisher=NodeExecutionTaskPublisher(redis_client),
        stream_key="stream:spot_opps",
        control_guard=FakeControlGuard(
            allowed=True,
            approved_notional=35.0,
            reason=None,
        ),
        block_ms=0,
        event_router=router,
        region="main",
    )

    processed = await dispatcher.run(max_iterations=1)

    assert processed == 1
    assert redis_client.xadds[0][1]["target_quote_amount"] == "35.0"
    event = next(
        item for item in router.events if item.event_type == "control.rule.resized"
    )
    assert event.service == "dispatcher"
    assert event.region == "main"
    assert event.symbol == "BTC/USDT"
    assert event.exchange == "bitget"
    assert event.payload["user_id"] == "42"
    assert event.payload["source_message_id"] == "1-0"
    assert event.payload["requested_notional"] == 50.0
    assert event.payload["approved_notional"] == 35.0
    assert event.payload["reason"] == "limit_rule_applied"


@pytest.mark.asyncio
async def test_executor_worker_reads_only_its_node_stream():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    service = FakeSpotService()
    router = FakeEventRouter()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        block_ms=1,
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert service.calls[0]["symbol"] == "BTC/USDT"
    assert router.events[0].event_type == "executor.task.processed"
    assert router.events[0].service == "executor"
    assert router.events[0].region == "node-a"


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
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    account_repository = FakeAccountRepository(
        {
            "42": [
                FakeExchangeAccount(exchange="bitget"),
                FakeExchangeAccount(exchange="gate"),
            ]
        }
    )
    execution_accounts_by_exchange = {
        "bitget": {"credentials": "cred-a", "proxies": {"http": "http://127.0.0.1:8000"}},
        "gate": {"credentials": "cred-b", "proxies": {}},
    }
    resolver = FakeExecutorAccountTruthResolver(
        resolved=execution_accounts_by_exchange
    )
    dispatcher = FakeDispatcher()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=dispatcher,
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        account_repository=account_repository,
        account_truth_resolver=resolver,
        env_mode="testnet",
        block_ms=1,
        region="main",
    )

    processed = await consumer.run(max_iterations=1)

    assert processed == 1
    assert account_repository.calls == [{"user_id": 42, "env_mode": "testnet"}]
    assert resolver.calls == [
        {
            "accounts": account_repository.accounts_by_user_id["42"],
            "user_id": "42",
            "buy_exchange": "bitget",
            "sell_exchange": "gate",
            "env_mode": "testnet",
            "region": "main",
        }
    ]
    assert dispatcher.calls == [
        {
            "payload": {
                "task_uuid": "task-1",
                "user_id": "42",
                "symbol": "BTC/USDT",
                "buy_exchange": "bitget",
                "sell_exchange": "gate",
                "target_quote_amount": "40.0",
            },
            "credentials_by_exchange": {
                "bitget": "cred-a",
                "gate": "cred-b",
            },
            "execution_accounts_by_exchange": execution_accounts_by_exchange,
            "proxies_by_exchange": {
                "bitget": {"http": "http://127.0.0.1:8000"},
                "gate": {},
            },
        }
    ]
    assert repository.executing == [("task-1", "main")]
    assert repository.succeeded == ["task-1"]


@pytest.mark.asyncio
async def test_executor_consumer_uses_bound_account_ids_from_payload():
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
                            "buy_account_id": "101",
                            "sell_account_id": "202",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    dispatcher = FakeDispatcher()
    resolver = FakeExecutorAccountTruthResolver(
        resolved={
            "bitget": {"credentials": "cred-a", "proxies": {}},
            "gate": {"credentials": "cred-b", "proxies": {}},
        }
    )
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=dispatcher,
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        account_repository=FakeAccountRepository(
            {"42": [FakeExchangeAccount(exchange="bitget"), FakeExchangeAccount(exchange="gate")]}
        ),
        account_truth_resolver=resolver,
        env_mode="testnet",
        block_ms=1,
        region="main",
    )

    await consumer.run(max_iterations=1)

    assert resolver.bound_calls[0]["buy_account_id"] == "101"
    assert resolver.bound_calls[0]["sell_account_id"] == "202"


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
    repository = FakeTaskRepository(task_uuid="task-1")
    service = FakeSpotService()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        account_repository=FakeAccountRepository({"42": []}),
        account_truth_resolver=FakeExecutorAccountTruthResolver(
            error=ExecutorAccountTruthError(
                "executor_account_not_found",
                user_id="42",
                exchange="bitget",
                detail="no executable account for exchange=bitget",
            )
        ),
        env_mode="testnet",
        block_ms=1,
        region="main",
    )

    processed = await consumer.run(max_iterations=1)

    assert processed == 0
    assert service.calls == []
    assert repository.executing == [("task-1", "main")]
    assert repository.failed == [("task-1", "executor_account_not_found")]


@pytest.mark.asyncio
async def test_executor_binding_failure_persists_reason_and_does_not_call_spot_service():
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
                            "buy_account_id": "101",
                            "sell_account_id": "202",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    service = FakeSpotService()
    resolver = FailingBindingExecutorAccountTruthResolver(
        reason="executor_account_binding_not_found"
    )
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        account_repository=FakeAccountRepository({"42": []}),
        account_truth_resolver=resolver,
        env_mode="testnet",
        block_ms=1,
        region="main",
    )

    processed = await consumer.run(max_iterations=1)

    assert processed == 0
    assert service.calls == []
    assert resolver.calls == []
    assert len(resolver.bound_calls) == 1
    assert repository.executing == [("task-1", "main")]
    assert repository.failed == [("task-1", "executor_account_binding_not_found")]


@pytest.mark.asyncio
async def test_executor_marks_task_executing_and_succeeded():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    service = FakeSpotService()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert repository.executing == [("task-1", "node-a")]
    assert repository.succeeded == ["task-1"]


@pytest.mark.asyncio
async def test_executor_marks_execution_result_open_hedged():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    service = FakeSpotService()
    service.result = type(
        "ExecutionSummary",
        (),
        {
            "ok": True,
            "execution_status": "OPEN_HEDGED",
            "filled_exchanges": ["okx", "gate"],
            "failed_exchanges": [],
        },
    )()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert repository.execution_results == [
        (
            "task-1",
            {
                "lifecycle_status": "SUCCEEDED",
                "execution_status": "OPEN_HEDGED",
                "filled_exchanges": ["okx", "gate"],
                "failed_exchanges": [],
                "repair_action": "NONE",
                "repair_reason": "fully_hedged",
            },
        )
    ]


@pytest.mark.asyncio
async def test_executor_marks_execution_result_with_runtime_trade_execution_service_result():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                            "source_message_id": "src-1",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")

    class RuntimeTradeExecutionServiceResultStub:
        async def run_task(self, **kwargs):
            _ = kwargs
            return type(
                "ExecutionSummary",
                (),
                {
                    "ok": True,
                    "execution_status": "OPEN_HEDGED",
                    "filled_exchanges": ["okx", "gate"],
                    "failed_exchanges": [],
                },
            )()

    router = FakeEventRouter()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(RuntimeTradeExecutionServiceResultStub()),
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

    assert processed == 1
    assert repository.execution_results[0][1]["execution_status"] == "OPEN_HEDGED"
    event = _find_event(router.events, "executor.execution_result")
    assert event.payload["execution_status"] == "OPEN_HEDGED"
    assert event.payload["filled_exchanges"] == ["okx", "gate"]
    assert event.payload["failed_exchanges"] == []
    assert event.payload["buy_leg_status"] is None


@pytest.mark.asyncio
async def test_executor_marks_execution_result_open_hedged_with_rich_probe_fields():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    service = FakeSpotService()
    service.result = type(
        "ExecutionSummary",
        (),
        {
            "ok": True,
            "execution_status": "OPEN_HEDGED",
            "filled_exchanges": ["okx", "gate"],
            "failed_exchanges": [],
            "buy_leg_status": "final_fetched",
            "sell_leg_status": "final_fetched",
            "buy_leg_error_code": None,
            "sell_leg_error_code": None,
            "failed_stage": None,
        },
    )()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert repository.execution_results == [
        (
            "task-1",
            {
                "lifecycle_status": "SUCCEEDED",
                "execution_status": "OPEN_HEDGED",
                "filled_exchanges": ["okx", "gate"],
                "failed_exchanges": [],
                "repair_action": "NONE",
                "repair_reason": "fully_hedged",
            },
        )
    ]


@pytest.mark.asyncio
async def test_executor_marks_execution_result_open_partial_with_repair_plan():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    service = FakeSpotService()
    service.result = type(
        "ExecutionSummary",
        (),
        {
            "ok": False,
            "execution_status": "OPEN_PARTIAL",
            "filled_exchanges": ["okx"],
            "failed_exchanges": ["gate"],
        },
    )()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert repository.execution_results == [
        (
            "task-1",
            {
                "lifecycle_status": "FAILED",
                "execution_status": "OPEN_PARTIAL",
                "filled_exchanges": ["okx"],
                "failed_exchanges": ["gate"],
                "repair_action": "AUTO_HEDGE_REPAIRING",
                "repair_reason": "one_leg_failed",
            },
        )
    ]


@pytest.mark.asyncio
async def test_executor_marks_execution_result_open_partial_with_rich_probe_fields():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    service = FakeSpotService()
    service.result = type(
        "ExecutionSummary",
        (),
        {
            "ok": False,
            "execution_status": "OPEN_PARTIAL",
            "filled_exchanges": ["okx"],
            "failed_exchanges": ["gate"],
            "buy_leg_status": "created",
            "sell_leg_status": "create_failed",
            "buy_leg_error_code": None,
            "sell_leg_error_code": "sell_create_failed",
            "failed_stage": "create_sell",
        },
    )()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert repository.execution_results == [
        (
            "task-1",
            {
                "lifecycle_status": "FAILED",
                "execution_status": "OPEN_PARTIAL",
                "filled_exchanges": ["okx"],
                "failed_exchanges": ["gate"],
                "repair_action": "AUTO_HEDGE_REPAIRING",
                "repair_reason": "one_leg_failed",
            },
        )
    ]


@pytest.mark.asyncio
async def test_executor_emits_execution_result_event_for_rich_open_hedged_result():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                            "source_message_id": "src-1",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    service = FakeSpotService()
    service.result = type(
        "ExecutionSummary",
        (),
        {
            "ok": True,
            "execution_status": "OPEN_HEDGED",
            "filled_exchanges": ["okx", "gate"],
            "failed_exchanges": [],
            "buy_leg_status": "final_fetched",
            "sell_leg_status": "final_fetched",
            "buy_leg_error_code": None,
            "sell_leg_error_code": None,
            "buy_leg_error_detail": None,
            "sell_leg_error_detail": None,
            "failed_stage": None,
        },
    )()
    router = FakeEventRouter()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
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

    assert processed == 1
    event = _find_event(router.events, "executor.execution_result")
    assert event.service == "executor"
    assert event.region == "node-a"
    assert event.symbol == "BTC/USDT"
    assert event.exchange == "okx"
    assert event.exchanges == ["okx", "gate"]
    assert event.payload == {
        "task_uuid": "task-1",
        "user_id": "42",
        "source_message_id": "src-1",
        "buy_exchange": "okx",
        "sell_exchange": "gate",
        "execution_status": "OPEN_HEDGED",
        "filled_exchanges": ["okx", "gate"],
        "failed_exchanges": [],
        "buy_leg_status": "final_fetched",
        "sell_leg_status": "final_fetched",
        "buy_leg_error_code": None,
        "sell_leg_error_code": None,
        "buy_leg_error_detail": None,
        "sell_leg_error_detail": None,
        "failed_stage": None,
    }


@pytest.mark.asyncio
async def test_executor_emits_execution_result_event_for_rich_open_partial_result():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                            "source_message_id": "src-1",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    service = FakeSpotService()
    service.result = type(
        "ExecutionSummary",
        (),
        {
            "ok": False,
            "execution_status": "OPEN_PARTIAL",
            "filled_exchanges": ["okx"],
            "failed_exchanges": ["gate"],
            "buy_leg_status": "created",
            "sell_leg_status": "create_failed",
            "buy_leg_error_code": None,
            "sell_leg_error_code": "sell_create_failed",
            "buy_leg_error_detail": None,
            "sell_leg_error_detail": "create order failed",
            "failed_stage": "create_sell",
        },
    )()
    router = FakeEventRouter()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
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

    assert processed == 1
    event = _find_event(router.events, "executor.execution_result")
    assert event.payload["execution_status"] == "OPEN_PARTIAL"
    assert event.payload["filled_exchanges"] == ["okx"]
    assert event.payload["failed_exchanges"] == ["gate"]
    assert event.payload["buy_leg_status"] == "created"
    assert event.payload["sell_leg_status"] == "create_failed"
    assert event.payload["sell_leg_error_code"] == "sell_create_failed"
    assert event.payload["sell_leg_error_detail"] == "create order failed"
    assert event.payload["failed_stage"] == "create_sell"


@pytest.mark.asyncio
async def test_executor_emits_execution_result_event_for_summary_only_result():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    service = FakeSpotService()
    service.result = type(
        "ExecutionSummary",
        (),
        {
            "ok": True,
            "execution_status": "OPEN_HEDGED",
            "filled_exchanges": ["okx", "gate"],
            "failed_exchanges": [],
        },
    )()
    router = FakeEventRouter()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
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

    assert processed == 1
    event = _find_event(router.events, "executor.execution_result")
    assert event.payload["execution_status"] == "OPEN_HEDGED"
    assert event.payload["filled_exchanges"] == ["okx", "gate"]
    assert event.payload["failed_exchanges"] == []
    assert event.payload["buy_leg_status"] is None
    assert event.payload["sell_leg_status"] is None
    assert event.payload["buy_leg_error_code"] is None
    assert event.payload["sell_leg_error_code"] is None
    assert event.payload["failed_stage"] is None


@pytest.mark.asyncio
async def test_executor_emits_execution_result_event_without_task_repository():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                            "source_message_id": "src-1",
                        },
                    )
                ],
            )
        ]
    )
    service = FakeSpotService()
    service.result = type(
        "ExecutionSummary",
        (),
        {
            "ok": True,
            "execution_status": "OPEN_HEDGED",
            "filled_exchanges": ["okx", "gate"],
            "failed_exchanges": [],
        },
    )()
    router = FakeEventRouter()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=None,
        block_ms=1,
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    event = _find_event(router.events, "executor.execution_result")
    assert event.payload["task_uuid"] == "task-1"
    assert event.payload["user_id"] == "42"
    assert event.payload["source_message_id"] == "src-1"
    assert event.payload["execution_status"] == "OPEN_HEDGED"
    assert event.payload["filled_exchanges"] == ["okx", "gate"]
    assert event.payload["failed_exchanges"] == []


@pytest.mark.asyncio
async def test_executor_preflight_failure_does_not_emit_execution_result_event():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "okx",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    router = FakeEventRouter()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(FakeSpotService()),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=FakeTaskRepository(task_uuid="task-1"),
        block_ms=1,
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object()},
        max_iterations=1,
    )

    assert processed == 0
    assert all(
        event.event_type != "executor.execution_result" for event in router.events
    )


@pytest.mark.asyncio
async def test_executor_preflight_failure_does_not_write_execution_summary():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "okx",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(FakeSpotService()),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object()},
        max_iterations=1,
    )

    assert processed == 0
    assert repository.execution_results == []
    assert repository.failed == [("task-1", "executor_preflight_same_exchange")]


@pytest.mark.asyncio
async def test_executor_marks_task_failed_when_dispatch_raises():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=FailingDispatcher(),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        block_ms=1,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 0
    assert repository.executing == [("task-1", "node-a")]
    assert repository.failed == [("task-1", "dispatch boom")]


@pytest.mark.asyncio
async def test_executor_blocks_even_if_dispatcher_already_published_task():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
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
        control_guard=FakeControlGuard(
            allowed=False,
            approved_notional=0.0,
            reason="user.disable_open",
        ),
        block_ms=1,
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert service.calls == []


@pytest.mark.asyncio
async def test_executor_marks_task_blocked_when_control_guard_rejects():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                        },
                    )
                ],
            )
        ]
    )
    repository = FakeTaskRepository(task_uuid="task-1")
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(FakeSpotService()),
        stream_key="stream:spot_exec_tasks:node-a",
        task_repository=repository,
        control_guard=FakeControlGuard(
            allowed=False,
            approved_notional=0.0,
            reason="reduce_only",
        ),
        block_ms=1,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert repository.executing == [("task-1", "node-a")]
    assert repository.blocked == [("task-1", "reduce_only")]


@pytest.mark.asyncio
async def test_executor_emits_control_rule_blocked_event_before_dispatch():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                            "source_message_id": "src-1",
                        },
                    )
                ],
            )
        ]
    )
    router = FakeEventRouter()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(FakeSpotService()),
        stream_key="stream:spot_exec_tasks:node-a",
        control_guard=FakeControlGuard(
            allowed=False,
            approved_notional=0.0,
            reason="reduce_only",
        ),
        block_ms=1,
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert router.events[0].event_type == "control.rule.blocked"
    assert router.events[0].service == "executor"
    assert router.events[0].region == "node-a"
    assert router.events[0].symbol == "BTC/USDT"
    assert router.events[0].exchange == "okx"
    assert router.events[0].payload["user_id"] == "42"
    assert router.events[0].payload["source_message_id"] == "src-1"
    assert router.events[0].payload["requested_notional"] == 40.0
    assert router.events[0].payload["approved_notional"] == 0.0
    assert router.events[0].payload["reason"] == "reduce_only"


@pytest.mark.asyncio
async def test_executor_emits_control_rule_resized_event():
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
                            "buy_exchange": "okx",
                            "sell_exchange": "gate",
                            "target_quote_amount": "40.0",
                            "source_message_id": "src-1",
                        },
                    )
                ],
            )
        ]
    )
    service = FakeSpotService()
    router = FakeEventRouter()
    consumer = RedisExecutionTaskConsumer(
        redis_client=redis_client,
        dispatcher=RedisOpportunityDispatcher(service),
        stream_key="stream:spot_exec_tasks:node-a",
        control_guard=FakeControlGuard(
            allowed=True,
            approved_notional=18.0,
            reason=None,
        ),
        block_ms=1,
        event_router=router,
        region="node-a",
    )

    processed = await consumer.run(
        credentials_by_exchange={"okx": object(), "gate": object()},
        max_iterations=1,
    )

    assert processed == 1
    assert service.calls[0]["target_quote_amount"] == 18.0
    assert router.events[0].event_type == "control.rule.resized"
    assert router.events[0].service == "executor"
    assert router.events[0].region == "node-a"
    assert router.events[0].symbol == "BTC/USDT"
    assert router.events[0].exchange == "okx"
    assert router.events[0].payload["user_id"] == "42"
    assert router.events[0].payload["source_message_id"] == "src-1"
    assert router.events[0].payload["requested_notional"] == 40.0
    assert router.events[0].payload["approved_notional"] == 18.0
    assert router.events[0].payload["reason"] == "limit_rule_applied"
