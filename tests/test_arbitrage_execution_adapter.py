import pytest

from app.runtime.trade_execution_service import RuntimeExecutionResult


class TradeExecutionServiceStub:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def run_task(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class TaskRecord:
    def __init__(self, *, task_type: str):
        self.id = 1
        self.task_uuid = "task-1"
        self.task_type = task_type
        self.symbol = "BTC/USDT"
        self.spot_exchange = "binance"
        self.derivative_exchange = "okx"
        self.target_notional = 100.0


@pytest.mark.asyncio
async def test_adapter_maps_open_task_to_spot_buy_and_derivative_sell():
    from app.runtime.arbitrage_execution_adapter import ArbitrageExecutionAdapter

    service = TradeExecutionServiceStub(
        RuntimeExecutionResult(
            ok=True,
            execution_status="OPEN_HEDGED",
            filled_exchanges=["binance", "okx"],
            failed_exchanges=[],
        )
    )
    adapter = ArbitrageExecutionAdapter(execution_service=service)

    result = await adapter.execute_task(
        task=TaskRecord(task_type="open"),
        credentials_by_exchange={"binance": object(), "okx": object()},
        execution_accounts_by_exchange={"binance": object(), "okx": object()},
        env_mode="testnet",
        proxies_by_exchange={"binance": {}, "okx": {}},
    )

    assert result.execution_status == "OPEN_HEDGED"
    assert service.calls[0]["buy_exchange"] == "binance"
    assert service.calls[0]["sell_exchange"] == "okx"
    assert service.calls[0]["target_quote_amount"] == 100.0


@pytest.mark.asyncio
async def test_adapter_maps_close_task_to_reverse_direction():
    from app.runtime.arbitrage_execution_adapter import ArbitrageExecutionAdapter

    service = TradeExecutionServiceStub(
        RuntimeExecutionResult(
            ok=True,
            execution_status="CLOSE_HEDGED",
            filled_exchanges=["okx", "binance"],
            failed_exchanges=[],
        )
    )
    adapter = ArbitrageExecutionAdapter(execution_service=service)

    result = await adapter.execute_task(
        task=TaskRecord(task_type="close"),
        credentials_by_exchange={"binance": object(), "okx": object()},
        execution_accounts_by_exchange={"binance": object(), "okx": object()},
        env_mode="testnet",
        proxies_by_exchange={"binance": {}, "okx": {}},
    )

    assert result.execution_status == "CLOSE_HEDGED"
    assert service.calls[0]["buy_exchange"] == "okx"
    assert service.calls[0]["sell_exchange"] == "binance"


@pytest.mark.asyncio
async def test_adapter_rejects_unknown_task_type():
    from app.runtime.arbitrage_execution_adapter import ArbitrageExecutionAdapter

    adapter = ArbitrageExecutionAdapter(
        execution_service=TradeExecutionServiceStub(
            RuntimeExecutionResult(
                ok=True,
                execution_status="OPEN_HEDGED",
                filled_exchanges=[],
                failed_exchanges=[],
            )
        )
    )

    with pytest.raises(ValueError, match="unsupported task_type"):
        await adapter.execute_task(
            task=TaskRecord(task_type="rebalance"),
            credentials_by_exchange={"binance": object(), "okx": object()},
            execution_accounts_by_exchange={"binance": object(), "okx": object()},
            env_mode="testnet",
            proxies_by_exchange={"binance": {}, "okx": {}},
        )
