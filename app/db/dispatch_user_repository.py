from sqlalchemy import select
from sqlalchemy.orm import Session

from models import ExchangeAccount, StrategyConfig, User


class DispatchUserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_dispatchable_user_ids(self, *, env_mode: str) -> list[str]:
        statement = (
            select(User.id)
            .where(User.is_trading_enabled.is_(True))
            .where(
                select(ExchangeAccount.id)
                .where(
                    ExchangeAccount.user_id == User.id,
                    ExchangeAccount.env_mode == env_mode,
                    ExchangeAccount.is_enabled.is_(True),
                )
                .exists()
            )
            .where(
                select(StrategyConfig.id)
                .where(
                    StrategyConfig.user_id == User.id,
                    StrategyConfig.strategy_type == "spot_futures",
                    StrategyConfig.is_enabled.is_(True),
                )
                .exists()
            )
            .order_by(User.id.asc())
        )
        return [str(user_id) for user_id in self.session.scalars(statement)]
