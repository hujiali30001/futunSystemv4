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
