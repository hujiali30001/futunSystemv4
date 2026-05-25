from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from models import ExchangeAccount


class AccountRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_enabled_accounts(self, *, user_id: int, env_mode: str) -> list[ExchangeAccount]:
        statement = (
            select(ExchangeAccount)
            .options(joinedload(ExchangeAccount.proxy))
            .where(
                ExchangeAccount.user_id == user_id,
                ExchangeAccount.env_mode == env_mode,
                ExchangeAccount.is_enabled.is_(True),
            )
            .order_by(ExchangeAccount.id.asc())
        )
        return list(self.session.scalars(statement))
