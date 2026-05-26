from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.runtime.trade_execution_service import (
    RuntimeExecutionResult,
    RuntimeTradeExecutionService,
)


@dataclass(slots=True)
class ArbitrageExecutionAdapter:
    execution_service: RuntimeTradeExecutionService

    async def execute_task(
        self,
        *,
        task: Any,
        credentials_by_exchange: dict[str, Any],
        execution_accounts_by_exchange: dict[str, Any],
        env_mode: str,
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
    ) -> RuntimeExecutionResult:
        task_type = str(task.task_type).lower()
        if task_type == "open":
            buy_exchange = str(task.spot_exchange)
            sell_exchange = str(task.derivative_exchange)
        elif task_type == "close":
            buy_exchange = str(task.derivative_exchange)
            sell_exchange = str(task.spot_exchange)
        else:
            raise ValueError(f"unsupported task_type: {task.task_type}")

        return await self.execution_service.run_task(
            exchanges=[buy_exchange, sell_exchange],
            buy_exchange=buy_exchange,
            sell_exchange=sell_exchange,
            credentials_by_exchange=credentials_by_exchange,
            execution_accounts_by_exchange=execution_accounts_by_exchange,
            symbol=str(task.symbol),
            target_quote_amount=float(task.target_notional),
            env_mode=env_mode,
            proxies_by_exchange=proxies_by_exchange,
        )
