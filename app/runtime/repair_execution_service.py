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
    def __init__(self, session_factory: ExchangeClientFactory | None = None, order_recorder=None) -> None:
        self.session_factory = session_factory or ExchangeClientFactory()
        self.order_recorder = order_recorder

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
        adapter = None
        session = None
        try:
            session = self.session_factory.create_session(
                exchange=target_exchange,
                env_mode=env_mode,
                proxies=(proxies_by_exchange or {}).get(target_exchange, {}),
                credentials=credentials_by_exchange[target_exchange],
            )
            await session.mark_ready()
            adapter = ExchangeAdapter(session)

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

            repair_order_id = None
            if self.order_recorder is not None:
                import uuid
                client_id = f"repair_{target_exchange}_{uuid.uuid4().hex[:8]}"
                repair_order_id = await self.order_recorder.record_submit(
                    task_id=0,
                    leg_type="spot",
                    exchange=target_exchange,
                    side=side,
                    market_type="spot",
                    client_order_id=client_id,
                    symbol=symbol,
                    order_type="market",
                    price=None,
                    amount=float(amount),
                )

            try:
                result = await adapter.create_order(
                    OrderRequest(
                        symbol=symbol,
                        side=side,
                        order_type="market",
                        amount=float(amount),
                        price=market_order_price,
                    )
                )
                if self.order_recorder is not None and repair_order_id is not None:
                    filled = float(result.get("filled", 0) or 0)
                    fee = result.get("fee")
                    fee_cost = float(fee.get("cost", 0)) if isinstance(fee, dict) else None
                    fee_currency = str(fee.get("currency", "")) if isinstance(fee, dict) else None
                    await self.order_recorder.record_open(
                        order_id=repair_order_id,
                        exchange_order_id=str(result.get("id", "")),
                        avg_price=float(result.get("average", 0) or 0) if filled > 0 else None,
                        filled_amount=filled,
                        fee_cost=fee_cost,
                        fee_currency=fee_currency,
                        raw_response=result,
                    )
            except Exception as exc:
                if self.order_recorder is not None and repair_order_id is not None:
                    await self.order_recorder.record_failed(order_id=repair_order_id, reason=str(exc))
                raise
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
                reason=f"{type(exc).__name__}: {exc}",
            )
        finally:
            if adapter is not None:
                await adapter.close()
