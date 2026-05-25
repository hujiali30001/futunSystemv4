from sqlalchemy.orm import Session

from models import StrategyConfig


class StrategyConfigRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_enabled_for_user(
        self,
        *,
        user_id: int,
        strategy_type: str = "spot_futures",
    ) -> list[StrategyConfig]:
        return (
            self.session.query(StrategyConfig)
            .filter(
                StrategyConfig.user_id == user_id,
                StrategyConfig.is_enabled.is_(True),
                StrategyConfig.strategy_type == strategy_type,
            )
            .order_by(StrategyConfig.id.asc())
            .all()
        )
