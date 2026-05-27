from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.session import build_session_factory
from app.db.task_repository import ArbitrageTaskCreate, TaskRepository
from models import Base, User


def test_build_session_factory_returns_reusable_sqlalchemy_session():
    session_factory = build_session_factory("sqlite:///:memory:")
    Base.metadata.create_all(session_factory.kw["bind"])

    with session_factory() as session:
        session.add(User(id=1, username="factory-user"))
        session.commit()

    with session_factory() as session:
        user = session.get(User, 1)

    assert user is not None
    assert user.username == "factory-user"


def test_task_repository_creates_and_updates_task_status():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    task = repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="task-1",
            user_id=42,
            strategy_config_id=None,
            opportunity_id="opp-1",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="okx",
            derivative_exchange="gate",
            target_notional=100.0,
            expected_spread_bps=120.0,
            expected_funding_bps=0.0,
            idempotency_key="idem-1",
            home_region="main",
        )
    )

    repository.mark_dispatched(task.task_uuid, worker_node_id="node-a")
    repository.mark_executing(task.task_uuid, worker_node_id="node-a")
    repository.mark_succeeded(task.task_uuid)

    refreshed = repository.get_by_task_uuid("task-1")

    assert refreshed is not None
    assert refreshed.status == "SUCCEEDED"
    assert refreshed.worker_node_id == "node-a"
    assert refreshed.dispatched_at is not None
    assert refreshed.started_at is not None
    assert refreshed.finished_at is not None


def test_task_repository_lists_dispatchable_arbitrage_tasks_in_created_and_dispatched_states():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-open-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )
    repository.mark_dispatched("arb-open-1", worker_node_id="node-a")
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-close-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="2-0",
            env_mode="testnet",
            task_type="close",
            symbol="ETH/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=80.0,
            expected_spread_bps=10.0,
            expected_funding_bps=1.0,
            idempotency_key="42:2-0:close:11",
            home_region="main",
        )
    )
    repository.mark_succeeded("arb-close-1")

    items = repository.list_executable_tasks(env_mode="testnet", limit=10)

    assert [item.task_uuid for item in items] == ["arb-open-1"]
    assert items[0].status == "DISPATCHED"


def test_mark_executing_sets_running_state_and_started_at():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-open-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )

    task = repository.mark_executing("arb-open-1", worker_node_id="node-a")

    assert task.status == "RUNNING"
    assert task.worker_node_id == "node-a"
    assert task.started_at is not None


def test_claim_next_executable_task_claims_created_task_and_sets_running_fields():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-open-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )

    claimed = repository.claim_next_executable_task(
        worker_node_id="node-a",
        env_mode="testnet",
    )

    assert claimed is not None
    assert claimed.task_uuid == "arb-open-1"
    assert claimed.status == "RUNNING"
    assert claimed.worker_node_id == "node-a"
    assert claimed.started_at is not None


def test_claim_next_executable_task_does_not_return_same_task_twice():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-open-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )

    first = repository.claim_next_executable_task(
        worker_node_id="node-a",
        env_mode="testnet",
    )
    second = repository.claim_next_executable_task(
        worker_node_id="node-b",
        env_mode="testnet",
    )

    assert first is not None
    assert second is None


def test_claim_next_executable_task_skips_running_and_terminal_states():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-succeeded",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )
    repository.mark_succeeded("arb-succeeded")
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-running",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="2-0",
            env_mode="testnet",
            task_type="open",
            symbol="ETH/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=80.0,
            expected_spread_bps=10.0,
            expected_funding_bps=1.0,
            idempotency_key="42:2-0:open:11",
            home_region="main",
        )
    )
    repository.mark_executing("arb-running", worker_node_id="node-a")

    claimed = repository.claim_next_executable_task(
        worker_node_id="node-b",
        env_mode="testnet",
    )

    assert claimed is None


def test_task_repository_persists_bound_account_ids():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    task = repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="task-1",
            user_id=42,
            strategy_config_id=None,
            opportunity_id="opp-1",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="bitget",
            derivative_exchange="gate",
            target_notional=100.0,
            expected_spread_bps=120.0,
            expected_funding_bps=0.0,
            idempotency_key="idem-1",
            home_region="main",
            buy_account_id=101,
            sell_account_id=202,
        )
    )

    refreshed = repository.get_by_task_uuid(task.task_uuid)

    assert refreshed is not None
    assert refreshed.buy_account_id == 101
    assert refreshed.sell_account_id == 202


def test_task_repository_sets_default_auto_recovery_fields_on_create():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    task = repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-open-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )

    assert task.retry_count == 0
    assert task.max_retry_count == 2
    assert task.cooldown_until is None
    assert task.failure_reason is None
    assert task.auto_recovery_status == "NONE"


def test_claim_next_executable_task_skips_cooldown_tasks_until_due():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-cooldown",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )
    repository.mark_auto_recovery_cooldown(
        "arb-cooldown",
        failure_reason="temporary_route_failure",
        cooldown_until=datetime.utcnow() + timedelta(minutes=5),
    )

    claimed = repository.claim_next_executable_task(
        worker_node_id="node-a",
        env_mode="testnet",
    )

    assert claimed is None


def test_claim_next_executable_task_can_claim_due_retry_pending_task():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-retry",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:retry:11",
            home_region="main",
        )
    )
    repository.mark_auto_recovery_retry(
        "arb-retry",
        failure_reason="temporary_route_failure",
    )

    claimed = repository.claim_next_executable_task(
        worker_node_id="node-a",
        env_mode="testnet",
    )

    assert claimed is not None
    assert claimed.task_uuid == "arb-retry"
    assert claimed.status == "RUNNING"


def test_mark_auto_recovery_retry_increments_retry_count_and_requeues_task():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-retry-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )
    repository.mark_executing("arb-retry-1", worker_node_id="node-a")

    task = repository.mark_auto_recovery_retry(
        "arb-retry-1",
        failure_reason="temporary_route_failure",
    )

    assert task.status == "DISPATCHED"
    assert task.status_reason is None
    assert task.retry_count == 1
    assert task.auto_recovery_status == "RETRY_PENDING"
    assert task.failure_reason == "temporary_route_failure"
    assert task.cooldown_until is None
    assert task.finished_at is None


def test_mark_auto_recovery_cooldown_sets_due_time_without_finishing_task():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-cooldown-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )
    repository.mark_executing("arb-cooldown-1", worker_node_id="node-a")
    due_at = datetime.utcnow() + timedelta(minutes=3)

    task = repository.mark_auto_recovery_cooldown(
        "arb-cooldown-1",
        failure_reason="retry_limit_reached",
        cooldown_until=due_at,
    )

    assert task.status == "DISPATCHED"
    assert task.status_reason is None
    assert task.auto_recovery_status == "COOLDOWN"
    assert task.failure_reason == "retry_limit_reached"
    assert task.cooldown_until is not None
    assert task.finished_at is None


def test_mark_auto_recovery_exhausted_marks_final_failure():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-exhausted-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )
    repository.mark_executing("arb-exhausted-1", worker_node_id="node-a")

    task = repository.mark_auto_recovery_exhausted(
        "arb-exhausted-1",
        failure_reason="cooldown_retried_and_failed",
    )

    assert task.status == "FAILED"
    assert task.status_reason == "auto_recovery_exhausted"
    assert task.auto_recovery_status == "EXHAUSTED"
    assert task.failure_reason == "cooldown_retried_and_failed"
    assert task.cooldown_until is None
    assert task.finished_at is not None


def test_mark_failed_clears_cooldown_and_marks_exhausted():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="arb-hard-failed-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )
    repository.mark_auto_recovery_cooldown(
        "arb-hard-failed-1",
        failure_reason="temporary_route_failure",
        cooldown_until=datetime.utcnow() + timedelta(minutes=5),
    )

    task = repository.mark_failed(
        "arb-hard-failed-1",
        reason="unexpected_executor_exception",
    )

    assert task.status == "FAILED"
    assert task.status_reason == "unexpected_executor_exception"
    assert task.failure_reason == "unexpected_executor_exception"
    assert task.auto_recovery_status == "EXHAUSTED"
    assert task.cooldown_until is None
    assert task.finished_at is not None


def test_task_repository_marks_execution_result_with_summary_fields():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    task = repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="task-1",
            user_id=42,
            strategy_config_id=None,
            opportunity_id="opp-1",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="okx",
            derivative_exchange="gate",
            target_notional=100.0,
            expected_spread_bps=120.0,
            expected_funding_bps=0.0,
            idempotency_key="idem-1",
            home_region="main",
        )
    )

    repository.mark_execution_result(
        task.task_uuid,
        lifecycle_status="FAILED",
        execution_status="OPEN_PARTIAL",
        filled_exchanges=["okx"],
        failed_exchanges=["gate"],
        repair_action="AUTO_HEDGE_REPAIRING",
        repair_reason="one_leg_failed",
    )

    refreshed = repository.get_by_task_uuid(task.task_uuid)

    assert refreshed is not None
    assert refreshed.status == "FAILED"
    assert refreshed.status_reason is None
    assert refreshed.execution_status == "OPEN_PARTIAL"
    assert refreshed.filled_exchanges_json == ["okx"]
    assert refreshed.failed_exchanges_json == ["gate"]
    assert refreshed.repair_action == "AUTO_HEDGE_REPAIRING"
    assert refreshed.repair_reason == "one_leg_failed"
    assert refreshed.finished_at is not None


def test_mark_repair_result_marks_manual_required_summary():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    task = repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="task-1",
            user_id=42,
            strategy_config_id=None,
            opportunity_id="opp-1",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="okx",
            derivative_exchange="gate",
            target_notional=100.0,
            expected_spread_bps=120.0,
            expected_funding_bps=0.0,
            idempotency_key="idem-1",
            home_region="main",
        )
    )

    repository.mark_repair_result(
        task.task_uuid,
        lifecycle_status="FAILED",
        execution_status="OPEN_PARTIAL",
        filled_exchanges=["okx"],
        failed_exchanges=["gate"],
        repair_action="AUTO_HEDGE_REPAIRING",
        repair_reason="one_leg_failed",
        status_reason="manual_required",
    )

    refreshed = repository.get_by_task_uuid(task.task_uuid)

    assert refreshed is not None
    assert refreshed.status == "FAILED"
    assert refreshed.status_reason == "manual_required"
    assert refreshed.execution_status == "OPEN_PARTIAL"


def test_task_repository_finds_closeable_open_task_by_user_symbol_and_exchanges():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    created = repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="task-open-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="1-0",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=25.0,
            expected_funding_bps=5.0,
            idempotency_key="42:1-0:open:11",
            home_region="main",
        )
    )

    matched = repository.find_closeable_task(
        user_id=42,
        symbol="BTC/USDT",
        spot_exchange="binance",
        derivative_exchange="okx",
        env_mode="testnet",
    )

    assert matched is not None
    assert matched.task_uuid == created.task_uuid


def test_task_repository_returns_none_when_only_close_task_exists():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="task-close-1",
            user_id=42,
            strategy_config_id=11,
            opportunity_id="2-0",
            env_mode="testnet",
            task_type="close",
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            target_notional=100.0,
            expected_spread_bps=18.0,
            expected_funding_bps=3.0,
            idempotency_key="42:2-0:close:11",
            home_region="main",
        )
    )

    matched = repository.find_closeable_task(
        user_id=42,
        symbol="BTC/USDT",
        spot_exchange="binance",
        derivative_exchange="okx",
        env_mode="testnet",
    )

    assert matched is None


def test_task_repository_enforces_unique_idempotency_key():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(id=42, username="u42"))
    session.commit()

    repository = TaskRepository(session)
    task_data = ArbitrageTaskCreate(
        task_uuid="task-1",
        user_id=42,
        strategy_config_id=None,
        opportunity_id="opp-1",
        env_mode="testnet",
        task_type="open",
        symbol="BTC/USDT",
        spot_exchange="okx",
        derivative_exchange="gate",
        target_notional=100.0,
        expected_spread_bps=120.0,
        expected_funding_bps=0.0,
        idempotency_key="idem-1",
        home_region="main",
    )

    task1 = repository.create_task(task_data)
    assert task1.task_uuid == "task-1"

    duplicate = repository.create_task(
        ArbitrageTaskCreate(
            task_uuid="task-2",
            user_id=42,
            strategy_config_id=None,
            opportunity_id="opp-2",
            env_mode="testnet",
            task_type="open",
            symbol="BTC/USDT",
            spot_exchange="okx",
            derivative_exchange="gate",
            target_notional=50.0,
            expected_spread_bps=80.0,
            expected_funding_bps=0.0,
            idempotency_key="idem-1",
            home_region="main",
        )
    )
    assert duplicate.id == task1.id
    assert duplicate.target_notional == 100.0
