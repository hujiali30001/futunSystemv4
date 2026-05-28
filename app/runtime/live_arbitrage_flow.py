import asyncio

from app.exchanges.adapters import ExchangeAdapter
from app.exchanges.session_manager import ExchangeAccountSession, ExchangeClientFactory
from app.market.opportunity import (
    ArbitrageOpportunity,
    OpportunityCalculator,
    OrderbookSnapshot,
)
from app.runtime.redis_flow import ArbitrageOpportunityPublisher


class SwapSymbolDiscovery:
    def __init__(self, session_factory: ExchangeClientFactory) -> None:
        self.session_factory = session_factory

    async def discover(
        self,
        *,
        exchanges: list[str],
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
        min_exchange_count: int = 2,
    ) -> dict[str, dict[str, str]]:
        symbol_to_swap: dict[str, dict[str, str]] = {}
        for exchange in exchanges:
            session = self.session_factory.create_session(
                exchange=exchange,
                env_mode="live",
                proxies=(proxies_by_exchange or {}).get(exchange, {}),
                credentials=None,
            )
            try:
                await session.mark_ready()
                for symbol, swap_symbol in self._extract_spot_to_swap(session.markets).items():
                    if symbol not in symbol_to_swap:
                        symbol_to_swap[symbol] = {}
                    symbol_to_swap[symbol][exchange] = swap_symbol
            finally:
                await session.close()

        return {
            sym: mappings
            for sym, mappings in sorted(symbol_to_swap.items())
            if len(mappings) >= min_exchange_count
        }

    @staticmethod
    def _extract_spot_to_swap(markets: dict) -> dict[str, str]:
        swap_by_base: dict[str, str] = {}
        for swap_symbol, market in markets.items():
            if market.get("type") != "swap":
                continue
            if str(market.get("quote", "")).upper() != "USDT":
                continue
            if not market.get("linear"):
                continue
            if not market.get("active", True):
                continue
            base = str(market.get("base", ""))
            swap_by_base[base] = swap_symbol

        mapping: dict[str, str] = {}
        for spot_symbol, market in markets.items():
            if market.get("type") not in (None, "", "spot"):
                continue
            if str(market.get("quote", "")).upper() != "USDT":
                continue
            if not market.get("active", True):
                continue
            base = str(market.get("base", ""))
            swap_symbol = swap_by_base.get(base)
            if swap_symbol is not None:
                mapping[spot_symbol] = swap_symbol

        return mapping


class LiveArbitrageFlowService:
    def __init__(self, *, redis_client, session_factory=None) -> None:
        self.redis_client = redis_client
        self.session_factory = session_factory or ExchangeClientFactory()
        self.calculator = OpportunityCalculator()
        self.publisher = ArbitrageOpportunityPublisher(redis_client)

    async def publish_snapshots(
        self,
        *,
        symbol: str,
        spot_exchange: str,
        derivative_exchange: str,
        spot_snapshot: OrderbookSnapshot,
        derivative_snapshot: OrderbookSnapshot,
        funding_rate: float,
    ) -> tuple[ArbitrageOpportunity, ArbitrageOpportunity]:
        self._ensure_snapshot(spot_snapshot, name="spot_snapshot")
        self._ensure_snapshot(derivative_snapshot, name="derivative_snapshot")

        open_opp = self.calculator.build_arbitrage_opportunity(
            symbol=symbol,
            spot_exchange=spot_exchange,
            derivative_exchange=derivative_exchange,
            spot=spot_snapshot,
            derivative=derivative_snapshot,
            funding_rate=funding_rate,
            opportunity_type="OPEN",
        )
        close_opp = self.calculator.build_arbitrage_opportunity(
            symbol=symbol,
            spot_exchange=spot_exchange,
            derivative_exchange=derivative_exchange,
            spot=spot_snapshot,
            derivative=derivative_snapshot,
            funding_rate=funding_rate,
            opportunity_type="CLOSE",
        )

        await self.publisher.publish(open_opp)
        await self.publisher.publish(close_opp)

        fs_open = self.calculator.build_arbitrage_opportunity(
            symbol=symbol,
            spot_exchange=derivative_exchange,
            derivative_exchange=spot_exchange,
            spot=derivative_snapshot,
            derivative=spot_snapshot,
            funding_rate=funding_rate,
            opportunity_type="OPEN",
        )
        fs_open.direction = "futures_spot"
        fs_close = self.calculator.build_arbitrage_opportunity(
            symbol=symbol,
            spot_exchange=derivative_exchange,
            derivative_exchange=spot_exchange,
            spot=derivative_snapshot,
            derivative=spot_snapshot,
            funding_rate=funding_rate,
            opportunity_type="CLOSE",
        )
        fs_close.direction = "futures_spot"
        await self.publisher.publish(fs_open)
        await self.publisher.publish(fs_close)

        return open_opp, close_opp

    @staticmethod
    def _ensure_snapshot(snapshot: object, *, name: str) -> None:
        if not isinstance(snapshot, OrderbookSnapshot):
            raise TypeError(f"{name} must be an OrderbookSnapshot")

    async def _fetch_and_store_tickers(
        self,
        *,
        adapters: dict[str, "ExchangeAdapter"],
        symbol_swap_map: dict[str, dict[str, str]],
    ) -> None:
        symbols_by_exchange: dict[str, set[str]] = {}
        swap_of: dict[str, set[tuple[str, str]]] = {}
        for symbol, ex_swaps in symbol_swap_map.items():
            for exchange, swap_symbol in ex_swaps.items():
                if exchange not in symbols_by_exchange:
                    symbols_by_exchange[exchange] = set()
                    swap_of[exchange] = set()
                symbols_by_exchange[exchange].add(symbol)
                symbols_by_exchange[exchange].add(swap_symbol)
                swap_of[exchange].add((swap_symbol, symbol))

        async def _fetch_for_exchange(exchange: str, symbols: set[str]) -> None:
            adapter = adapters.get(exchange)
            if adapter is None:
                return
            swaps = {s: spot for s, spot in swap_of.get(exchange, set())}
            sem = asyncio.Semaphore(3)

            async def _one(sym: str) -> None:
                async with sem:
                    try:
                        ticker = await adapter.fetch_ticker(sym)
                    except Exception:
                        return
                    qv = float(
                        ticker.get("quoteVolume")
                        or ticker.get("quote_volume")
                        or 0
                    )
                    last = float(ticker.get("last") or 0)
                    val = f"{qv}|{last}"
                    await self.redis_client.setex(
                        f"md:ticker:{exchange}:{sym}",
                        300,
                        val,
                    )
                    spot_of_swap = swaps.get(sym)
                    if spot_of_swap and spot_of_swap != sym:
                        await self.redis_client.setex(
                            f"md:ticker:{exchange}:swap:{spot_of_swap}",
                            300,
                            val,
                        )

            await asyncio.gather(*[_one(s) for s in symbols], return_exceptions=True)

        await asyncio.gather(
            *[_fetch_for_exchange(ex, syms) for ex, syms in symbols_by_exchange.items()],
            return_exceptions=True,
        )

    async def run_batch(
        self,
        *,
        exchanges: list[str],
        credentials_by_exchange: dict,
        symbol_swap_map: dict[str, dict[str, str]],
        env_mode: str = "testnet",
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
        orderbook_depth_limit: int = 5,
        concurrency: int = 10,
    ) -> list[dict]:
        sessions: dict[str, ExchangeAccountSession] = {}
        adapters: dict[str, ExchangeAdapter] = {}
        failed_exchanges: set[str] = set()
        try:
            for exchange in exchanges:
                try:
                    cred = credentials_by_exchange.get(exchange)
                    session = self.session_factory.create_session(
                        exchange=exchange,
                        env_mode=env_mode,
                        proxies=(proxies_by_exchange or {}).get(exchange, {}),
                        credentials=cred,
                    )
                    await session.mark_ready()
                    if session.client and hasattr(session.client, "load_markets"):
                        await session.client.load_markets()
                    sessions[exchange] = session
                    adapters[exchange] = ExchangeAdapter(session)
                except Exception:
                    failed_exchanges.add(exchange)

            usable_exchanges = [e for e in exchanges if e not in failed_exchanges]
            if len(usable_exchanges) < 2:
                return []

            sem = asyncio.Semaphore(concurrency)
            results: list[dict] = []

            async def _scan_one(symbol: str) -> None:
                ex_swaps = symbol_swap_map.get(symbol, {})
                usable_swaps = {
                    ex: sw for ex, sw in ex_swaps.items()
                    if ex in adapters
                }
                if len(usable_swaps) < 2:
                    return
                async with sem:
                    ex_list = sorted(usable_swaps.keys())
                    spot_orderbooks: dict[str, OrderbookSnapshot] = {}
                    swap_orderbooks: dict[str, OrderbookSnapshot] = {}
                    funding_rates: dict[str, float] = {}

                    for exchange in ex_list:
                        adapter = adapters.get(exchange)
                        if adapter is None:
                            continue
                        swap_symbol = usable_swaps.get(exchange)
                        if swap_symbol is None:
                            continue
                        try:
                            spot_ob = await adapter.fetch_orderbook(
                                symbol, limit=orderbook_depth_limit
                            )
                            spot_orderbooks[exchange] = OrderbookSnapshot(
                                best_bid=float(spot_ob["bids"][0][0]),
                                best_ask=float(spot_ob["asks"][0][0]),
                                bids=spot_ob["bids"],
                                asks=spot_ob["asks"],
                            )
                        except Exception:
                            continue
                        try:
                            swap_ob = await adapter.fetch_orderbook(
                                swap_symbol, limit=orderbook_depth_limit
                            )
                            swap_orderbooks[exchange] = OrderbookSnapshot(
                                best_bid=float(swap_ob["bids"][0][0]),
                                best_ask=float(swap_ob["asks"][0][0]),
                                bids=swap_ob["bids"],
                                asks=swap_ob["asks"],
                            )
                        except Exception:
                            continue
                        try:
                            fr_data = await _fetch_funding_rate_safe(
                                adapter, swap_symbol
                            )
                            if isinstance(fr_data, dict):
                                funding_rates[exchange] = float(
                                    fr_data.get("fundingRate", 0)
                                    or fr_data.get("funding_rate", 0)
                                    or 0
                                )
                        except Exception:
                            funding_rates[exchange] = 0.0

                    for spot_exchange in ex_list:
                        spot_snap = spot_orderbooks.get(spot_exchange)
                        if spot_snap is None:
                            continue
                        for deriv_exchange in ex_list:
                            swap_snap = swap_orderbooks.get(deriv_exchange)
                            if swap_snap is None:
                                continue
                            fr = funding_rates.get(deriv_exchange, 0.0)
                            try:
                                open_opp, close_opp = await self.publish_snapshots(
                                    symbol=symbol,
                                    spot_exchange=spot_exchange,
                                    derivative_exchange=deriv_exchange,
                                    spot_snapshot=spot_snap,
                                    derivative_snapshot=swap_snap,
                                    funding_rate=fr,
                                )
                                results.append(
                                    {
                                        "symbol": symbol,
                                        "exchange": f"{spot_exchange}/{deriv_exchange}",
                                        "open_spread_bps": open_opp.open_spread_bps,
                                        "close_spread_bps": close_opp.close_spread_bps,
                                        "funding_rate": fr,
                                    }
                                )
                            except Exception:
                                continue

            await asyncio.gather(
                *[_scan_one(s) for s in symbol_swap_map],
                return_exceptions=True,
            )

            await self._fetch_and_store_tickers(
                adapters=adapters,
                symbol_swap_map=symbol_swap_map,
            )

            return results
        finally:
            await asyncio.gather(
                *[adapter.close() for adapter in adapters.values()],
                return_exceptions=True,
            )


async def _fetch_funding_rate_safe(
    adapter: ExchangeAdapter, swap_symbol: str
) -> dict | None:
    client = adapter.session.client
    if client is None:
        return None
    try:
        if hasattr(client, "fetch_funding_rate"):
            return await client.fetch_funding_rate(swap_symbol)
        if hasattr(client, "fetch_funding_rate_history"):
            result = await client.fetch_funding_rate_history(swap_symbol)
            if isinstance(result, list) and result:
                return result[-1]
            return result
    except Exception:
        pass
    return None
