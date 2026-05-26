from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
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

    repository.create_task(task_data)

    try:
        repository.create_task(
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
    except IntegrityError:
        session.rollback()
    else:
        raise AssertionError("expected duplicate idempotency_key to raise IntegrityError")
