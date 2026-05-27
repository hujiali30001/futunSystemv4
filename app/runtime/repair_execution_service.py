from __future__ import annotations

from dataclasses import dataclass

from app.exchanges.adapters import ExchangeAdapter, OrderRequest
from app.exchanges.session_manager import ExchangeClientFactory, ExchangeCredentials


@dataclass(slots=True)
class RuntimeRepairResult:
    ok: bool
    status: str
    task_uuid: str
    target_exchanges: list[str]
    repaired_exchanges: list[str]
    remaining_failed_exchanges: list[str]
    reason: str | None = None


class RuntimeRepairExecutionService:
    def __init__(self, session_factory: ExchangeClientFactory | None = None) -> None:
        self.session_factory = session_factory or ExchangeClientFactory()

    async def run_task(
        self,
        *,
        task_uuid: str,
        symbol: str,
        buy_exchange: str,
        sell_exchange: str,
        target_exchanges: list[str],
        credentials_by_exchange: dict[str, ExchangeCredentials],
        target_quote_amount: float = 15.0,
        env_mode: str = "testnet",
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
    ) -> RuntimeRepairResult:
        target_exchange = target_exchanges[0]
        remaining_target_exchanges = list(target_exchanges[1:])
        side = "buy" if target_exchange == buy_exchange else "sell"
        session = self.session_factory.create_session(
            exchange=target_exchange,
            env_mode=env_mode,
            proxies=(proxies_by_exchange or {}).get(target_exchange, {}),
            credentials=credentials_by_exchange[target_exchange],
        )
        await session.mark_ready()
        adapter = ExchangeAdapter(session)

        try:
            ticker = await adapter.fetch_ticker(symbol)
            reference_price = (
                ticker.get("last") or ticker.get("ask") or ticker.get("bid") or 1.0
            )
            markets = getattr(session, "markets", None) or {}
            market_info = markets.get(symbol) or {}
            limits = market_info.get("limits", {}) if isinstance(market_info, dict) else {}
            amount_info = limits.get("amount", {}) if isinstance(limits, dict) else {}
            min_amount = amount_info.get("min", 0.01) if isinstance(amount_info, dict) else 0.01
            amount = adapter.amount_to_precision(
                symbol,
                max(float(min_amount), float(target_quote_amount) / float(reference_price)),
            )
            market_order_price = float(reference_price) if side == "buy" else None
            await adapter.create_order(
                OrderRequest(
                    symbol=symbol,
                    side=side,
                    order_type="market",
                    amount=float(amount),
                    price=market_order_price,
                )
            )
            return RuntimeRepairResult(
                ok=True,
                status="REPAIRED",
                task_uuid=task_uuid,
                target_exchanges=list(target_exchanges),
                repaired_exchanges=[target_exchange],
                remaining_failed_exchanges=remaining_target_exchanges,
                reason=None,
            )
        except Exception as exc:
            return RuntimeRepairResult(
                ok=False,
                status="MANUAL_REQUIRED",
                task_uuid=task_uuid,
                target_exchanges=list(target_exchanges),
                repaired_exchanges=[],
                remaining_failed_exchanges=[target_exchange],
                reason=str(exc),
            )
        finally:
            await adapter.close()
