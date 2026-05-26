from app.market.opportunity import ArbitrageOpportunity, OpportunityCalculator, OrderbookSnapshot
from app.runtime.redis_flow import ArbitrageOpportunityPublisher


class LiveArbitrageFlowService:
    def __init__(self, *, redis_client) -> None:
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

        open_opportunity = self.calculator.build_arbitrage_opportunity(
            symbol=symbol,
            spot_exchange=spot_exchange,
            derivative_exchange=derivative_exchange,
            spot=spot_snapshot,
            derivative=derivative_snapshot,
            funding_rate=funding_rate,
            opportunity_type="OPEN",
        )
        close_opportunity = self.calculator.build_arbitrage_opportunity(
            symbol=symbol,
            spot_exchange=spot_exchange,
            derivative_exchange=derivative_exchange,
            spot=spot_snapshot,
            derivative=derivative_snapshot,
            funding_rate=funding_rate,
            opportunity_type="CLOSE",
        )

        await self.publisher.publish(open_opportunity)
        await self.publisher.publish(close_opportunity)
        return open_opportunity, close_opportunity

    @staticmethod
    def _ensure_snapshot(snapshot: object, *, name: str) -> None:
        if not isinstance(snapshot, OrderbookSnapshot):
            raise TypeError(f"{name} must be an OrderbookSnapshot")
