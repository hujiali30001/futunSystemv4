import asyncio

from app.exchanges.adapters import ExchangeAdapter
from app.exchanges.session_manager import ExchangeAccountSession, ExchangeClientFactory
from app.market.opportunity import (
    OpportunityCalculator,
    OrderbookSnapshot,
    SpotOpportunity,
    spot_opportunity_to_payload,
)
from app.runtime.redis_flow import MarketOpportunityPublisher, RedisOpportunityDispatcher


class SymbolDiscovery:
    _QUOTE_WHITELIST = frozenset({"USDT"})
    _EXCLUDE_PREFIXES = ("1000", "1M")

    def __init__(self, session_factory: ExchangeClientFactory) -> None:
        self.session_factory = session_factory

    async def discover(
        self,
        *,
        exchanges: list[str],
        credentials_by_exchange: dict,
        env_mode: str = "testnet",
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
    ) -> list[str]:
        symbol_sets: list[set[str]] = []
        for exchange in exchanges:
            session = self.session_factory.create_session(
                exchange=exchange,
                env_mode=env_mode,
                proxies=(proxies_by_exchange or {}).get(exchange, {}),
                credentials=credentials_by_exchange.get(exchange),
            )
            try:
                await session.mark_ready()
                pairs = self._extract_spot_usdt_pairs(session.markets)
                symbol_sets.append(pairs)
            finally:
                await session.close()

        common = set.intersection(*symbol_sets) if symbol_sets else set()
        return sorted(common)

    def _extract_spot_usdt_pairs(self, markets: dict) -> set[str]:
        pairs: set[str] = set()
        for symbol, market in markets.items():
            if str(market.get("quote", "")).upper() != "USDT":
                continue
            if not market.get("active", True):
                continue
            if market.get("type") not in (None, "", "spot"):
                continue
            base = str(market.get("base", ""))
            if base.startswith(self._EXCLUDE_PREFIXES):
                continue
            pairs.add(symbol)
        return pairs


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

    async def run_batch(
        self,
        *,
        exchanges: list[str],
        credentials_by_exchange: dict,
        symbols: list[str],
        env_mode: str = "testnet",
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
        orderbook_depth_limit: int | None = None,
        target_quote_amount: float | None = None,
        concurrency: int = 15,
    ) -> list[SpotOpportunity]:
        sessions: dict[str, ExchangeAccountSession] = {}
        adapters: dict[str, ExchangeAdapter] = {}
        depth = orderbook_depth_limit or self.DEFAULT_ORDERBOOK_LIMIT
        quote = target_quote_amount or self.DEFAULT_TARGET_QUOTE_AMOUNT
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

            sem = asyncio.Semaphore(concurrency)
            results: list[SpotOpportunity] = []

            async def _scan_one(symbol: str) -> None:
                async with sem:
                    orderbooks = {
                        ex: await adapters[ex].fetch_orderbook(symbol, limit=depth)
                        for ex in exchanges
                    }
                    snapshots = {
                        ex: OrderbookSnapshot(
                            best_bid=float(orderbooks[ex]["bids"][0][0]),
                            best_ask=float(orderbooks[ex]["asks"][0][0]),
                            bids=orderbooks[ex]["bids"],
                            asks=orderbooks[ex]["asks"],
                        )
                        for ex in exchanges
                    }
                    buy_ex = min(exchanges, key=lambda n: snapshots[n].best_ask)
                    sell_ex = max(exchanges, key=lambda n: snapshots[n].best_bid)
                    opportunity = self.calculator.build_depth_spot_opportunity(
                        symbol=symbol,
                        buy_exchange=buy_ex,
                        sell_exchange=sell_ex,
                        buy_snapshot=snapshots[buy_ex],
                        sell_snapshot=snapshots[sell_ex],
                        target_quote_amount=quote,
                    )
                    if opportunity is not None:
                        await self.publisher.publish(opportunity)
                        results.append(opportunity)

            await asyncio.gather(
                *[_scan_one(s) for s in symbols],
                return_exceptions=True,
            )
            return results
        finally:
            await asyncio.gather(
                *[adapter.close() for adapter in adapters.values()],
                return_exceptions=True,
            )
