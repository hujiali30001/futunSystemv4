import pytest

from app.trading.executor import ExecutionResult, TradeExecutor
from app.trading.risk_manager import RiskManager
from app.trading.tasks import ExecutionLeg, ExecutionTask


class FakeAdapter:
    def __init__(self, name: str, should_fail: bool = False) -> None:
        self.name = name
        self.should_fail = should_fail

    async def create_order(self, request):
        if self.should_fail:
            raise RuntimeError(f"{self.name} failed")
        return {"id": f"{self.name}-1", "status": "filled", "amount": request.amount}


@pytest.mark.asyncio
async def test_execute_open_submits_both_legs_and_reports_partial_failure():
    task = ExecutionTask(
        task_id="task-1",
        symbol="BTC/USDT",
        open_legs=[
            ExecutionLeg(exchange="binance", side="buy", order_type="market", amount=1.0),
            ExecutionLeg(exchange="okx", side="sell", order_type="market", amount=1.0),
        ],
    )

    executor = TradeExecutor(
        adapter_factory={
            "binance": FakeAdapter("binance"),
            "okx": FakeAdapter("okx", should_fail=True),
        }
    )

    result = await executor.execute_open(task)

    assert result.status == "OPEN_PARTIAL"
    assert result.filled_exchanges == ["binance"]
    assert result.failed_exchanges == ["okx"]


def test_risk_manager_returns_none_plan_for_open_hedged():
    manager = RiskManager()
    result = ExecutionResult(
        status="OPEN_HEDGED",
        filled_exchanges=["okx", "gate"],
        failed_exchanges=[],
    )

    plan = manager.build_repair_plan(result)

    assert plan.action == "NONE"
    assert plan.reason == "fully_hedged"
