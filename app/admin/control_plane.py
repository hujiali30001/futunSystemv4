from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from app.admin.control_store import LimitRuleRecord, PlatformSwitchRecord


@dataclass(slots=True)
class LimitRule:
    scope: str
    scope_id: str
    limit_value: float
    symbol: str | None = None
    exchange: str | None = None
    strategy_id: int | None = None


@dataclass(slots=True)
class PlatformSwitch:
    key: str
    enabled: bool
    scope: str
    scope_id: str


@dataclass(slots=True)
class ControlDecision:
    allowed: bool
    approved_notional: float
    reason: str | None = None


class ControlPlane:
    def __init__(
        self,
        *,
        switches: list[PlatformSwitch],
        limit_rules: list[LimitRule],
    ) -> None:
        self.switches = switches
        self.limit_rules = limit_rules

    def evaluate_open_request(
        self,
        *,
        user_id: int,
        strategy_id: int | None,
        symbol: str,
        exchange: str,
        requested_notional: float,
    ) -> ControlDecision:
        if self._is_reduce_only_active(
            user_id=user_id,
            symbol=symbol,
            exchange=exchange,
        ):
            return ControlDecision(
                allowed=False,
                approved_notional=0.0,
                reason="reduce_only",
            )

        approved_notional = min(
            [requested_notional, *self._matching_limit_values(
                user_id=user_id,
                strategy_id=strategy_id,
                symbol=symbol,
                exchange=exchange,
            )]
        )
        return ControlDecision(
            allowed=approved_notional > 0,
            approved_notional=approved_notional,
            reason=None,
        )

    def _is_reduce_only_active(
        self,
        *,
        user_id: int,
        symbol: str,
        exchange: str,
    ) -> bool:
        for switch in self.switches:
            if not switch.enabled or switch.key != "platform.reduce_only":
                continue
            if switch.scope == "platform":
                return True
            if switch.scope == "user" and switch.scope_id == str(user_id):
                return True
            if switch.scope == "symbol" and switch.scope_id == symbol:
                return True
            if switch.scope == "exchange" and switch.scope_id == exchange:
                return True
        return False

    def _matching_limit_values(
        self,
        *,
        user_id: int,
        strategy_id: int | None,
        symbol: str,
        exchange: str,
    ) -> list[float]:
        values: list[float] = []
        for rule in self.limit_rules:
            if rule.scope == "platform":
                values.append(rule.limit_value)
            elif rule.scope == "user" and rule.scope_id == str(user_id):
                values.append(rule.limit_value)
            elif rule.scope == "strategy" and rule.scope_id == str(strategy_id):
                values.append(rule.limit_value)
            elif rule.scope == "symbol" and (
                rule.symbol == symbol or rule.scope_id == symbol
            ):
                values.append(rule.limit_value)
            elif rule.scope == "exchange" and (
                rule.exchange == exchange or rule.scope_id == exchange
            ):
                values.append(rule.limit_value)
        return values


def build_control_plane(
    *,
    limit_rules: Iterable["LimitRuleRecord"],
    switches: Iterable["PlatformSwitchRecord"],
) -> ControlPlane:
    return ControlPlane(
        switches=[
            PlatformSwitch(
                key=record.switch_key,
                enabled=record.enabled,
                scope=record.scope_type,
                scope_id=record.scope_id,
            )
            for record in switches
            if record.enabled
        ],
        limit_rules=[
            LimitRule(
                scope=record.scope_type,
                scope_id=record.scope_id,
                limit_value=record.limit_value,
                symbol=record.symbol,
                exchange=record.exchange,
                strategy_id=record.strategy_id,
            )
            for record in limit_rules
            if record.enabled and record.limit_type == "max_notional"
        ],
    )
