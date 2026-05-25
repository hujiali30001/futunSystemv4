import asyncio

from app.exchanges.adapters import ExchangeAdapter
from app.exchanges.session_manager import ExchangeClientFactory
from app.market.opportunity import (
    OpportunityCalculator,
    OrderbookSnapshot,
    SpotOpportunity,
    spot_opportunity_to_payload,
)
from app.runtime.redis_flow import MarketOpportunityPublisher, RedisOpportunityDispatcher


class LiveSpotFlowService:
    DEFAULT_ORDERBOOK_LIMIT = 5
    DEFAULT_TARGET_QUOTE_AMOUNT = 100.0

    def __init__(
        self,
        *,
        redis_client,
        session_factory: ExchangeClientFactory,
        spot_service,
        inline_dispatch_enabled: bool = False,
    ) -> None:
        self.redis_client = redis_client
        self.session_factory = session_factory
        self.spot_service = spot_service
        self.inline_dispatch_enabled = inline_dispatch_enabled
        self.calculator = OpportunityCalculator()
        self.publisher = MarketOpportunityPublisher(
            redis_client,
            zset_key="arb:zset:spot",
            stream_key="stream:spot_opps",
        )
        self.dispatcher = RedisOpportunityDispatcher(spot_service)

    async def run_once(
        self,
        *,
        exchanges: list[str],
        credentials_by_exchange: dict,
        symbol: str,
        env_mode: str = "testnet",
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
        orderbook_depth_limit: int | None = None,
        target_quote_amount: float | None = None,
    ) -> SpotOpportunity | None:
        sessions = {}
        adapters = {}
        try:
            for exchange in exchanges:
                session = self.session_factory.create_session(
                    exchange=exchange,
                    env_mode=env_mode,
                    proxies=(proxies_by_exchange or {}).get(exchange, {}),
                    credentials=credentials_by_exchange[exchange],
                )
                await session.mark_ready()
                sessions[exchange] = session
                adapters[exchange] = ExchangeAdapter(session)

            orderbooks = {
                exchange: await adapters[exchange].fetch_orderbook(
                    symbol,
                    limit=orderbook_depth_limit or self.DEFAULT_ORDERBOOK_LIMIT,
                )
                for exchange in exchanges
            }
            snapshots = {
                exchange: OrderbookSnapshot(
                    best_bid=float(orderbooks[exchange]["bids"][0][0]),
                    best_ask=float(orderbooks[exchange]["asks"][0][0]),
                    bids=orderbooks[exchange]["bids"],
                    asks=orderbooks[exchange]["asks"],
                )
                for exchange in exchanges
            }
            buy_exchange = min(exchanges, key=lambda name: snapshots[name].best_ask)
            sell_exchange = max(exchanges, key=lambda name: snapshots[name].best_bid)
            opportunity = self.calculator.build_depth_spot_opportunity(
                symbol=symbol,
                buy_exchange=buy_exchange,
                sell_exchange=sell_exchange,
                buy_snapshot=snapshots[buy_exchange],
                sell_snapshot=snapshots[sell_exchange],
                target_quote_amount=(
                    target_quote_amount or self.DEFAULT_TARGET_QUOTE_AMOUNT
                ),
            )
            if opportunity is None:
                return None
            await self.publisher.publish(opportunity)
            if self.inline_dispatch_enabled:
                payload = spot_opportunity_to_payload(opportunity)
                await self.dispatcher.dispatch(
                    {
                        "symbol": payload["symbol"],
                        "buy_exchange": payload["buy_exchange"],
                        "sell_exchange": payload["sell_exchange"],
                    },
                    credentials_by_exchange=credentials_by_exchange,
                )
            return opportunity
        finally:
            await asyncio.gather(
                *[adapter.close() for adapter in adapters.values()],
                return_exceptions=True,
            )
