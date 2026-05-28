import app.market.opportunity as market_opportunity
import market_scanner

from app.market.opportunity import OpportunityCalculator, OrderbookSnapshot
from market_scanner import Opportunity, OpportunityCalculator as ExportedCalculator
from market_scanner import OrderbookSnapshot as ExportedSnapshot


def test_calculator_returns_open_and_close_spread():
    calculator = OpportunityCalculator()
    spot = OrderbookSnapshot(
        best_bid=100.0,
        best_ask=101.0,
        bids=[[100.0, 5.0]],
        asks=[[101.0, 5.0]],
    )
    future = OrderbookSnapshot(
        best_bid=104.0,
        best_ask=105.0,
        bids=[[104.0, 4.0]],
        asks=[[105.0, 4.0]],
    )

    result = calculator.build_opportunity(
        symbol="BTC/USDT",
        spot_exchange="binance",
        derivative_exchange="okx",
        spot=spot,
        derivative=future,
        funding_rate=0.0005,
    )

    assert round(result.open_spread_bps, 2) == round(
        ((104.0 - 101.0) / 101.0) * 10000,
        2,
    )
    assert round(result.close_spread_bps, 2) == round(
        ((105.0 - 100.0) / 100.0) * 10000,
        2,
    )
    assert result.redis_member.startswith("binance:okx:BTC/USDT")


def test_market_scanner_exports_opportunity_primitives():
    assert ExportedCalculator is OpportunityCalculator
    assert ExportedSnapshot is OrderbookSnapshot
    assert Opportunity is market_opportunity.ArbitrageOpportunity


def test_arbitrage_opportunity_to_payload_contains_open_close_boundary_fields():
    opportunity = market_opportunity.ArbitrageOpportunity(
        symbol="BTC/USDT",
        spot_exchange="binance",
        derivative_exchange="okx",
        opportunity_type="OPEN",
        open_spread_bps=120.5,
        close_spread_bps=90.25,
        funding_rate=0.0004,
        annualized_bps=150.0,
        redis_member="binance:okx:BTC/USDT:OPEN:1",
        timestamp=123.0,
    )

    payload = market_opportunity.arbitrage_opportunity_to_payload(opportunity)

    assert payload == {
        "symbol": "BTC/USDT",
        "spot_exchange": "binance",
        "derivative_exchange": "okx",
        "opportunity_type": "OPEN",
        "open_spread_bps": 120.5,
        "close_spread_bps": 90.25,
        "funding_rate": 0.0004,
        "annualized_bps": 150.0,
        "redis_member": "binance:okx:BTC/USDT:OPEN:1",
        "timestamp": 123.0,
        "direction": "spot_futures",
    }


def test_calculator_builds_arbitrage_opportunity_with_explicit_type():
    calculator = OpportunityCalculator()
    spot = OrderbookSnapshot(
        best_bid=100.0,
        best_ask=101.0,
        bids=[[100.0, 5.0]],
        asks=[[101.0, 5.0]],
    )
    derivative = OrderbookSnapshot(
        best_bid=104.0,
        best_ask=105.0,
        bids=[[104.0, 4.0]],
        asks=[[105.0, 4.0]],
    )

    result = calculator.build_arbitrage_opportunity(
        symbol="BTC/USDT",
        spot_exchange="binance",
        derivative_exchange="okx",
        spot=spot,
        derivative=derivative,
        funding_rate=0.0005,
        opportunity_type="CLOSE",
    )

    assert result.opportunity_type == "CLOSE"
    assert round(result.open_spread_bps, 2) == round(
        ((104.0 - 101.0) / 101.0) * 10000,
        2,
    )
    assert round(result.close_spread_bps, 2) == round(
        ((105.0 - 100.0) / 100.0) * 10000,
        2,
    )
    assert result.redis_member.startswith("binance:okx:BTC/USDT:CLOSE:")


def test_market_scanner_exports_arbitrage_opportunity_name():
    assert market_scanner.ArbitrageOpportunity.__name__ == "ArbitrageOpportunity"


def test_calculator_builds_spot_opportunity_from_effective_depth_prices():
    calculator = OpportunityCalculator()

    buy_snapshot = OrderbookSnapshot(
        best_bid=99.0,
        best_ask=100.0,
        bids=[[99.0, 1.0]],
        asks=[[100.0, 0.5], [101.0, 0.5]],
    )
    sell_snapshot = OrderbookSnapshot(
        best_bid=103.0,
        best_ask=104.0,
        bids=[[103.0, 0.5], [102.0, 0.5]],
        asks=[[104.0, 1.0]],
    )

    opportunity = calculator.build_depth_spot_opportunity(
        symbol="BTC/USDT",
        buy_exchange="bitget",
        sell_exchange="gate",
        buy_snapshot=buy_snapshot,
        sell_snapshot=sell_snapshot,
        target_quote_amount=100.0,
    )

    assert opportunity.buy_exchange == "bitget"
    assert opportunity.sell_exchange == "gate"
    assert opportunity.effective_buy_price > 0
    assert opportunity.effective_sell_price > opportunity.effective_buy_price
    assert opportunity.target_quote_amount == 100.0
    assert opportunity.buy_depth_levels_used >= 1
    assert opportunity.sell_depth_levels_used >= 1


def test_calculator_returns_none_when_depth_is_insufficient():
    calculator = OpportunityCalculator()

    buy_snapshot = OrderbookSnapshot(
        best_bid=99.0,
        best_ask=100.0,
        bids=[[99.0, 0.1]],
        asks=[[100.0, 0.001]],
    )
    sell_snapshot = OrderbookSnapshot(
        best_bid=103.0,
        best_ask=104.0,
        bids=[[103.0, 0.001]],
        asks=[[104.0, 0.1]],
    )

    opportunity = calculator.build_depth_spot_opportunity(
        symbol="BTC/USDT",
        buy_exchange="bitget",
        sell_exchange="gate",
        buy_snapshot=buy_snapshot,
        sell_snapshot=sell_snapshot,
        target_quote_amount=100.0,
    )

    assert opportunity is None


def test_calculator_accepts_orderbook_levels_with_extra_exchange_fields():
    calculator = OpportunityCalculator()

    buy_snapshot = OrderbookSnapshot(
        best_bid=99.0,
        best_ask=100.0,
        bids=[[99.0, 1.0, 0]],
        asks=[[100.0, 0.5, 0], [101.0, 0.5, 0]],
    )
    sell_snapshot = OrderbookSnapshot(
        best_bid=103.0,
        best_ask=104.0,
        bids=[[103.0, 0.5, 0], [102.0, 0.5, 0]],
        asks=[[104.0, 1.0, 0]],
    )

    opportunity = calculator.build_depth_spot_opportunity(
        symbol="BTC/USDT",
        buy_exchange="bitget",
        sell_exchange="gate",
        buy_snapshot=buy_snapshot,
        sell_snapshot=sell_snapshot,
        target_quote_amount=100.0,
    )

    assert opportunity is not None
    assert opportunity.target_quote_amount == 100.0
