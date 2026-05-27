import pytest

from app.runtime.repair_execution_service import (
    RuntimeRepairExecutionService,
    RuntimeRepairResult,
)


class FakeClient:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.markets = {"BTC/USDT": {"limits": {"amount": {"min": 0.0001}}}}
        self.orders = []

    async def fetch_ticker(self, symbol: str) -> dict:
        return {"symbol": symbol, "bid": 100.0, "ask": 101.0, "last": 100.5}

    async def create_order(self, symbol, order_type, side, amount, price, params):
        if self.should_fail:
            raise RuntimeError("repair order failed")
        self.orders.append((symbol, order_type, side, amount, price, params))
        return {"id": "repair-1", "symbol": symbol, "status": "closed"}

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        _ = symbol
        return amount


class FakeSession:
    def __init__(self, client) -> None:
        self.client = client
        self.markets = client.markets

    async def mark_ready(self) -> None:
        return None

    async def close(self) -> None:
        return None


class FakeSessionFactory:
    def __init__(self, client) -> None:
        self.client = client

    def create_session(self, *, exchange, env_mode, proxies, credentials):
        _ = exchange, env_mode, proxies, credentials
        return FakeSession(self.client)


@pytest.mark.asyncio
async def test_runtime_repair_execution_service_returns_repaired_for_successful_order():
    service = RuntimeRepairExecutionService(session_factory=FakeSessionFactory(FakeClient()))

    result = await service.run_task(
        task_uuid="task-1",
        symbol="BTC/USDT",
        buy_exchange="okx",
        sell_exchange="gate",
        target_exchanges=["gate"],
        credentials_by_exchange={"gate": object()},
        target_quote_amount=40.0,
        env_mode="testnet",
    )

    assert result == RuntimeRepairResult(
        ok=True,
        status="REPAIRED",
        task_uuid="task-1",
        target_exchanges=["gate"],
        repaired_exchanges=["gate"],
        remaining_failed_exchanges=[],
        reason=None,
    )


@pytest.mark.asyncio
async def test_runtime_repair_execution_service_keeps_only_non_repaired_targets_in_remaining_failed_exchanges():
    service = RuntimeRepairExecutionService(session_factory=FakeSessionFactory(FakeClient()))

    result = await service.run_task(
        task_uuid="task-1",
        symbol="BTC/USDT",
        buy_exchange="okx",
        sell_exchange="gate",
        target_exchanges=["gate", "okx"],
        credentials_by_exchange={"gate": object(), "okx": object()},
        target_quote_amount=40.0,
        env_mode="testnet",
    )

    assert result.repaired_exchanges == ["gate"]
    assert result.remaining_failed_exchanges == ["okx"]


@pytest.mark.asyncio
async def test_runtime_repair_execution_service_returns_manual_required_when_order_fails():
    service = RuntimeRepairExecutionService(
        session_factory=FakeSessionFactory(FakeClient(should_fail=True))
    )

    result = await service.run_task(
        task_uuid="task-1",
        symbol="BTC/USDT",
        buy_exchange="okx",
        sell_exchange="gate",
        target_exchanges=["gate"],
        credentials_by_exchange={"gate": object()},
        target_quote_amount=40.0,
        env_mode="testnet",
    )

    assert result.ok is False
    assert result.status == "MANUAL_REQUIRED"
    assert result.task_uuid == "task-1"
    assert result.target_exchanges == ["gate"]
    assert result.repaired_exchanges == []
    assert result.remaining_failed_exchanges == ["gate"]
    assert "repair order failed" in (result.reason or "")


@pytest.mark.asyncio
async def test_runtime_repair_execution_service_reports_only_target_exchange_as_remaining_when_repair_fails():
    service = RuntimeRepairExecutionService(
        session_factory=FakeSessionFactory(FakeClient(should_fail=True))
    )

    result = await service.run_task(
        task_uuid="task-1",
        symbol="BTC/USDT",
        buy_exchange="okx",
        sell_exchange="gate",
        target_exchanges=["gate", "okx"],
        credentials_by_exchange={"gate": object(), "okx": object()},
        target_quote_amount=40.0,
        env_mode="testnet",
    )

    assert result.repaired_exchanges == []
    assert result.remaining_failed_exchanges == ["gate"]
    assert "repair order failed" in result.reason
