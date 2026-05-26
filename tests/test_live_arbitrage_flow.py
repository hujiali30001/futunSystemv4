import pytest

from app.market.opportunity import OrderbookSnapshot
from app.runtime.live_arbitrage_flow import LiveArbitrageFlowService


class FakeRedis:
    def __init__(self):
        self.zadds = []
        self.xadds = []

    async def zadd(self, key, mapping):
        self.zadds.append((key, mapping))
        return 1

    async def xadd(self, key, fields):
        self.xadds.append((key, fields))
        return "1-0"


@pytest.mark.asyncio
async def test_publish_snapshots_publishes_open_and_close_opportunities():
    redis_client = FakeRedis()
    flow = LiveArbitrageFlowService(redis_client=redis_client)

    published = await flow.publish_snapshots(
        symbol="BTC/USDT",
        spot_exchange="binance",
        derivative_exchange="okx",
        spot_snapshot=OrderbookSnapshot(
            best_bid=100.0,
            best_ask=101.0,
            bids=[[100.0, 5.0]],
            asks=[[101.0, 5.0]],
        ),
        derivative_snapshot=OrderbookSnapshot(
            best_bid=104.0,
            best_ask=105.0,
            bids=[[104.0, 4.0]],
            asks=[[105.0, 4.0]],
        ),
        funding_rate=0.0005,
    )

    assert [opportunity.opportunity_type for opportunity in published] == [
        "OPEN",
        "CLOSE",
    ]
    assert redis_client.zadds[0][0] == "arb:zset:open"
    assert redis_client.zadds[1][0] == "arb:zset:close"
    assert redis_client.xadds[0][1]["opportunity_type"] == "OPEN"
    assert redis_client.xadds[1][1]["opportunity_type"] == "CLOSE"
    assert published[0].spot_exchange == "binance"
    assert published[0].derivative_exchange == "okx"
    assert published[0].open_spread_bps > 0
    assert published[1].close_spread_bps > 0


@pytest.mark.asyncio
async def test_publish_snapshots_rejects_non_normalized_snapshot_inputs():
    redis_client = FakeRedis()
    flow = LiveArbitrageFlowService(redis_client=redis_client)

    with pytest.raises(TypeError, match="OrderbookSnapshot"):
        await flow.publish_snapshots(
            symbol="BTC/USDT",
            spot_exchange="binance",
            derivative_exchange="okx",
            spot_snapshot={
                "best_bid": 100.0,
                "best_ask": 101.0,
                "bids": [[100.0, 5.0]],
                "asks": [[101.0, 5.0]],
            },
            derivative_snapshot=OrderbookSnapshot(
                best_bid=104.0,
                best_ask=105.0,
                bids=[[104.0, 4.0]],
                asks=[[105.0, 4.0]],
            ),
            funding_rate=0.0005,
        )

    assert redis_client.zadds == []
    assert redis_client.xadds == []
