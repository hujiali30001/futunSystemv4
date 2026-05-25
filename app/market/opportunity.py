from dataclasses import dataclass
from time import time


@dataclass(slots=True)
class OrderbookSnapshot:
    best_bid: float
    best_ask: float
    bids: list[list[float]]
    asks: list[list[float]]


@dataclass(slots=True)
class Opportunity:
    symbol: str
    spot_exchange: str
    derivative_exchange: str
    open_spread_bps: float
    close_spread_bps: float
    funding_rate: float
    annualized_bps: float
    redis_member: str
    timestamp: float


@dataclass(slots=True)
class SpotOpportunity:
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_ask: float
    sell_bid: float
    spread_bps: float
    redis_member: str
    timestamp: float
    effective_buy_price: float
    effective_sell_price: float
    target_quote_amount: float
    buy_depth_levels_used: int
    sell_depth_levels_used: int


def spot_opportunity_to_payload(opportunity: SpotOpportunity) -> dict[str, object]:
    return {
        "symbol": opportunity.symbol,
        "buy_exchange": opportunity.buy_exchange,
        "sell_exchange": opportunity.sell_exchange,
        "buy_ask": opportunity.buy_ask,
        "sell_bid": opportunity.sell_bid,
        "spread_bps": opportunity.spread_bps,
        "redis_member": opportunity.redis_member,
        "timestamp": opportunity.timestamp,
        "effective_buy_price": opportunity.effective_buy_price,
        "effective_sell_price": opportunity.effective_sell_price,
        "target_quote_amount": opportunity.target_quote_amount,
        "buy_depth_levels_used": opportunity.buy_depth_levels_used,
        "sell_depth_levels_used": opportunity.sell_depth_levels_used,
    }


class OpportunityCalculator:
    def build_opportunity(
        self,
        *,
        symbol: str,
        spot_exchange: str,
        derivative_exchange: str,
        spot: OrderbookSnapshot,
        derivative: OrderbookSnapshot,
        funding_rate: float,
    ) -> Opportunity:
        open_spread = (derivative.best_ask - spot.best_ask) / spot.best_ask
        close_spread = (derivative.best_bid - spot.best_bid) / spot.best_bid
        current_time = time()
        return Opportunity(
            symbol=symbol,
            spot_exchange=spot_exchange,
            derivative_exchange=derivative_exchange,
            open_spread_bps=open_spread * 10000,
            close_spread_bps=close_spread * 10000,
            funding_rate=funding_rate,
            annualized_bps=(open_spread + funding_rate) * 365 * 10000,
            redis_member=f"{spot_exchange}:{derivative_exchange}:{symbol}:{int(current_time * 1000)}",
            timestamp=current_time,
        )

    def _weighted_buy_price(
        self,
        asks: list[list[float]],
        *,
        target_quote_amount: float,
    ) -> tuple[float, int] | None:
        remaining_quote = target_quote_amount
        acquired_base = 0.0
        spent_quote = 0.0
        levels_used = 0
        for level in asks:
            price = level[0]
            size = level[1]
            level_quote = float(price) * float(size)
            take_quote = min(level_quote, remaining_quote)
            if take_quote <= 0:
                continue
            spent_quote += take_quote
            acquired_base += take_quote / float(price)
            remaining_quote -= take_quote
            levels_used += 1
            if remaining_quote <= 1e-9:
                return spent_quote / acquired_base, levels_used
        return None

    def _weighted_sell_price(
        self,
        bids: list[list[float]],
        *,
        target_quote_amount: float,
    ) -> tuple[float, int] | None:
        remaining_quote = target_quote_amount
        sold_base = 0.0
        received_quote = 0.0
        levels_used = 0
        for level in bids:
            price = level[0]
            size = level[1]
            level_quote = float(price) * float(size)
            take_quote = min(level_quote, remaining_quote)
            if take_quote <= 0:
                continue
            received_quote += take_quote
            sold_base += take_quote / float(price)
            remaining_quote -= take_quote
            levels_used += 1
            if remaining_quote <= 1e-9:
                return received_quote / sold_base, levels_used
        return None

    def build_depth_spot_opportunity(
        self,
        *,
        symbol: str,
        buy_exchange: str,
        sell_exchange: str,
        buy_snapshot: OrderbookSnapshot,
        sell_snapshot: OrderbookSnapshot,
        target_quote_amount: float,
    ) -> SpotOpportunity | None:
        weighted_buy = self._weighted_buy_price(
            buy_snapshot.asks,
            target_quote_amount=target_quote_amount,
        )
        weighted_sell = self._weighted_sell_price(
            sell_snapshot.bids,
            target_quote_amount=target_quote_amount,
        )
        if weighted_buy is None or weighted_sell is None:
            return None
        effective_buy_price, buy_levels_used = weighted_buy
        effective_sell_price, sell_levels_used = weighted_sell
        current_time = time()
        spread = (effective_sell_price - effective_buy_price) / effective_buy_price
        return SpotOpportunity(
            symbol=symbol,
            buy_exchange=buy_exchange,
            sell_exchange=sell_exchange,
            buy_ask=buy_snapshot.best_ask,
            sell_bid=sell_snapshot.best_bid,
            spread_bps=spread * 10000,
            redis_member=f"{buy_exchange}:{sell_exchange}:{symbol}:{int(current_time * 1000)}",
            timestamp=current_time,
            effective_buy_price=effective_buy_price,
            effective_sell_price=effective_sell_price,
            target_quote_amount=target_quote_amount,
            buy_depth_levels_used=buy_levels_used,
            sell_depth_levels_used=sell_levels_used,
        )

    def build_spot_opportunity(
        self,
        *,
        symbol: str,
        buy_exchange: str,
        sell_exchange: str,
        buy_ask: float,
        sell_bid: float,
    ) -> SpotOpportunity:
        current_time = time()
        spread = (sell_bid - buy_ask) / buy_ask
        return SpotOpportunity(
            symbol=symbol,
            buy_exchange=buy_exchange,
            sell_exchange=sell_exchange,
            buy_ask=buy_ask,
            sell_bid=sell_bid,
            spread_bps=spread * 10000,
            redis_member=f"{buy_exchange}:{sell_exchange}:{symbol}:{int(current_time * 1000)}",
            timestamp=current_time,
            effective_buy_price=buy_ask,
            effective_sell_price=sell_bid,
            target_quote_amount=0.0,
            buy_depth_levels_used=1,
            sell_depth_levels_used=1,
        )
