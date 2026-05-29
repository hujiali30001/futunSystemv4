import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from app.admin.control_plane import build_control_plane
from app.admin.control_store import ControlPlaneStore
from app.db.task_repository import ArbitrageTaskCreate
from app.runtime.redis_flow import (
    build_node_execution_task_payload,
    build_repair_task_payload,
)
from app.runtime.runtime_events import RuntimeEvent
from app.trading.executor import ExecutionResult
from app.trading.risk_manager import RepairPlan, RiskManager


@dataclass(slots=True)
class ControlPlaneLoader:
    store: ControlPlaneStore

    async def load(self):
        return build_control_plane(
            limit_rules=await self.store.list_limit_rules(),
            switches=await self.store.list_switches(),
        )


@dataclass(slots=True)
class ControlGuard:
    control_plane_loader: Any
    event_router: Any | None = None
    service_name: str = "dispatcher"
    region: str = "default"

    async def evaluate(
        self,
        *,
        user_id: str,
        symbol: str,
        exchange: str,
        requested_notional: float,
        strategy_id: int | None = None,
    ):
        plane = await self.control_plane_loader.load()
        return plane.evaluate_open_request(
            user_id=int(user_id),
            strategy_id=strategy_id,
            symbol=symbol,
            exchange=exchange,
            requested_notional=requested_notional,
        )


def _build_control_rule_event(
    *,
    event_type: str,
    service: str,
    region: str,
    symbol: str | None,
    exchange: str | None,
    user_id: str,
    source_message_id: str | None,
    requested_notional: float,
    approved_notional: float,
    reason: str | None,
) -> RuntimeEvent:
    message = (
        "control rule blocked request"
        if event_type == "control.rule.blocked"
        else "control rule resized request"
    )
    effective_reason = reason
    if effective_reason is None and event_type == "control.rule.resized":
        effective_reason = "limit_rule_applied"
    return RuntimeEvent(
        event_type=event_type,
        level="INFO",
        service=service,
        region=region,
        symbol=symbol,
        exchange=exchange,
        message=message,
        payload={
            "user_id": user_id,
            "source_message_id": source_message_id,
            "requested_notional": requested_notional,
            "approved_notional": approved_notional,
            "reason": effective_reason,
        },
    )


def _build_dispatch_user_event(
    *,
    event_type: str,
    region: str,
    payload: dict[str, object],
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type=event_type,
        level="INFO",
        service="dispatcher",
        region=region,
        message="dispatcher user discovery event",
        payload=payload,
    )


def _build_executor_execution_result_event(
    *,
    region: str,
    payload: dict[str, object],
    result: Any,
) -> RuntimeEvent:
    buy_exchange = (
        str(payload["buy_exchange"]) if payload.get("buy_exchange") is not None else None
    )
    sell_exchange = (
        str(payload["sell_exchange"]) if payload.get("sell_exchange") is not None else None
    )
    exchanges = [
        exchange for exchange in (buy_exchange, sell_exchange) if exchange is not None
    ]
    return RuntimeEvent(
        event_type="executor.execution_result",
        level="INFO",
        service="executor",
        region=region,
        symbol=str(payload["symbol"]) if payload.get("symbol") is not None else None,
        exchange=buy_exchange,
        exchanges=exchanges,
        message="executor execution result",
        payload={
            "task_uuid": (
                str(payload["task_uuid"]) if payload.get("task_uuid") is not None else None
            ),
            "user_id": (
                str(payload["user_id"]) if payload.get("user_id") is not None else None
            ),
            "source_message_id": (
                str(payload["source_message_id"])
                if payload.get("source_message_id") is not None
                else None
            ),
            "buy_exchange": buy_exchange,
            "sell_exchange": sell_exchange,
            "execution_status": getattr(result, "execution_status", None),
            "filled_exchanges": list(getattr(result, "filled_exchanges", []) or []),
            "failed_exchanges": list(getattr(result, "failed_exchanges", []) or []),
            "buy_leg_status": getattr(result, "buy_leg_status", None),
            "sell_leg_status": getattr(result, "sell_leg_status", None),
            "buy_leg_error_code": getattr(result, "buy_leg_error_code", None),
            "sell_leg_error_code": getattr(result, "sell_leg_error_code", None),
            "buy_leg_error_detail": getattr(result, "buy_leg_error_detail", None),
            "sell_leg_error_detail": getattr(result, "sell_leg_error_detail", None),
            "failed_stage": getattr(result, "failed_stage", None),
        },
    )


def _build_executor_repair_planned_event(
    *,
    region: str,
    payload: dict[str, object],
    execution_status: str,
    filled_exchanges: list[str],
    failed_exchanges: list[str],
    repair_plan: RepairPlan,
) -> RuntimeEvent:
    buy_exchange = (
        str(payload["buy_exchange"]) if payload.get("buy_exchange") is not None else None
    )
    sell_exchange = (
        str(payload["sell_exchange"]) if payload.get("sell_exchange") is not None else None
    )
    exchanges = [
        exchange for exchange in (buy_exchange, sell_exchange) if exchange is not None
    ]
    return RuntimeEvent(
        event_type="executor.repair_planned",
        level="INFO",
        service="executor",
        region=region,
        symbol=str(payload["symbol"]) if payload.get("symbol") is not None else None,
        exchange=buy_exchange,
        exchanges=exchanges,
        message="executor repair planned",
        payload={
            "task_uuid": (
                str(payload["task_uuid"]) if payload.get("task_uuid") is not None else None
            ),
            "user_id": (
                str(payload["user_id"]) if payload.get("user_id") is not None else None
            ),
            "symbol": (
                str(payload["symbol"]) if payload.get("symbol") is not None else None
            ),
            "buy_exchange": buy_exchange,
            "sell_exchange": sell_exchange,
            "execution_status": execution_status,
            "filled_exchanges": list(filled_exchanges),
            "failed_exchanges": list(failed_exchanges),
            "repair_action": repair_plan.action,
            "repair_reason": repair_plan.reason,
            "target_exchanges": list(failed_exchanges),
        },
    )


def _build_repair_finished_event(
    *,
    region: str,
    payload: dict[str, object],
    result: Any,
) -> RuntimeEvent:
    buy_exchange = (
        str(payload["buy_exchange"]) if payload.get("buy_exchange") is not None else None
    )
    sell_exchange = (
        str(payload["sell_exchange"]) if payload.get("sell_exchange") is not None else None
    )
    exchanges = [
        exchange for exchange in (buy_exchange, sell_exchange) if exchange is not None
    ]
    level = "INFO" if getattr(result, "ok", False) else "ERROR"
    return RuntimeEvent(
        event_type="repair.task.finished",
        level=level,
        service="repair",
        region=region,
        symbol=str(payload["symbol"]) if payload.get("symbol") is not None else None,
        exchange=buy_exchange,
        exchanges=exchanges,
        message="repair task finished",
        payload={
            "task_uuid": (
                str(payload["task_uuid"]) if payload.get("task_uuid") is not None else None
            ),
            "repair_action": (
                str(payload.get("repair_action"))
                if payload.get("repair_action") is not None
                else None
            ),
            "repair_reason": (
                str(payload.get("repair_reason"))
                if payload.get("repair_reason") is not None
                else None
            ),
            "target_exchanges": list(getattr(result, "target_exchanges", []) or []),
            "repaired_exchanges": list(getattr(result, "repaired_exchanges", []) or []),
            "remaining_failed_exchanges": list(
                getattr(result, "remaining_failed_exchanges", []) or []
            ),
            "status": getattr(result, "status", None),
            "reason": getattr(result, "reason", None),
        },
    )


def _build_arb_executor_execution_result_event(
    *,
    region: str,
    task,
    result: Any,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type="arb.executor.execution_result",
        level="INFO",
        service="arb_executor",
        region=region,
        symbol=str(task.symbol),
        exchange=str(task.spot_exchange),
        exchanges=[str(task.spot_exchange), str(task.derivative_exchange)],
        message="arbitrage executor execution result",
        payload={
            "task_uuid": str(task.task_uuid),
            "user_id": str(task.user_id),
            "symbol": str(task.symbol),
            "task_type": str(task.task_type),
            "spot_exchange": str(task.spot_exchange),
            "derivative_exchange": str(task.derivative_exchange),
            "execution_status": getattr(result, "execution_status", None),
            "filled_exchanges": list(getattr(result, "filled_exchanges", []) or []),
            "failed_exchanges": list(getattr(result, "failed_exchanges", []) or []),
            "failed_errors": [
                str(e) for e in (getattr(result, "failed_errors", None) or [])
            ],
        },
    )


def _build_arb_executor_repair_planned_event(
    *,
    region: str,
    task,
    execution_status: str,
    filled_exchanges: list[str],
    failed_exchanges: list[str],
    repair_plan: RepairPlan,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type="arb.executor.repair_planned",
        level="INFO",
        service="arb_executor",
        region=region,
        symbol=str(task.symbol),
        exchange=str(task.spot_exchange),
        exchanges=[str(task.spot_exchange), str(task.derivative_exchange)],
        message="arbitrage executor repair planned",
        payload={
            "task_uuid": str(task.task_uuid),
            "symbol": str(task.symbol),
            "task_type": str(task.task_type),
            "execution_status": execution_status,
            "filled_exchanges": list(filled_exchanges),
            "failed_exchanges": list(failed_exchanges),
            "repair_action": repair_plan.action,
            "repair_reason": repair_plan.reason,
            "target_exchanges": list(failed_exchanges),
        },
    )


def _build_arb_executor_task_failed_event(
    *,
    region: str,
    task,
    result: Any,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type="arb.executor.task_failed",
        level="ERROR",
        service="arb_executor",
        region=region,
        symbol=str(task.symbol),
        exchange=str(task.spot_exchange),
        exchanges=[str(task.spot_exchange), str(task.derivative_exchange)],
        message="arbitrage executor task failed",
        payload={
            "task_uuid": str(task.task_uuid),
            "user_id": str(task.user_id),
            "symbol": str(task.symbol),
            "task_type": str(task.task_type),
            "error": str(getattr(result, "execution_status", "FAILED") or "FAILED"),
            "failed_exchanges": list(getattr(result, "failed_exchanges", []) or []),
        },
    )


def _build_arb_repair_finished_event(
    *,
    region: str,
    task,
    result: Any,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type="arb.repair.finished",
        level="INFO" if getattr(result, "ok", False) else "ERROR",
        service="arb_repair",
        region=region,
        symbol=str(task.symbol),
        exchange=str(task.spot_exchange),
        exchanges=[str(task.spot_exchange), str(task.derivative_exchange)],
        message="arbitrage repair finished",
        payload={
            "task_uuid": str(task.task_uuid),
            "symbol": str(task.symbol),
            "task_type": str(task.task_type),
            "status": getattr(result, "status", None),
            "repaired_exchanges": list(getattr(result, "repaired_exchanges", []) or []),
            "remaining_failed_exchanges": list(
                getattr(result, "remaining_failed_exchanges", []) or []
            ),
            "reason": getattr(result, "reason", None),
        },
    )


def _build_arb_recovery_retry_scheduled_event(
    *,
    region: str,
    task,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type="arb.recovery.retry_scheduled",
        level="INFO",
        service="arb_executor",
        region=region,
        symbol=str(task.symbol),
        exchange=str(task.spot_exchange),
        exchanges=[str(task.spot_exchange), str(task.derivative_exchange)],
        message="arbitrage recovery retry scheduled",
        payload={
            "task_uuid": str(task.task_uuid),
            "user_id": str(task.user_id),
            "symbol": str(task.symbol),
            "task_type": str(task.task_type),
            "spot_exchange": str(task.spot_exchange),
            "derivative_exchange": str(task.derivative_exchange),
            "failure_reason": getattr(task, "failure_reason", None),
            "retry_count": int(getattr(task, "retry_count", 0) or 0),
            "max_retry_count": int(getattr(task, "max_retry_count", 0) or 0),
            "auto_recovery_status": str(
                getattr(task, "auto_recovery_status", "NONE") or "NONE"
            ),
            "next_action": "RETRY_PENDING",
        },
    )


def _build_arb_recovery_cooldown_started_event(
    *,
    region: str,
    task,
) -> RuntimeEvent:
    cooldown_until = getattr(task, "cooldown_until", None)
    return RuntimeEvent(
        event_type="arb.recovery.cooldown_started",
        level="INFO",
        service="arb_executor",
        region=region,
        symbol=str(task.symbol),
        exchange=str(task.spot_exchange),
        exchanges=[str(task.spot_exchange), str(task.derivative_exchange)],
        message="arbitrage recovery cooldown started",
        payload={
            "task_uuid": str(task.task_uuid),
            "user_id": str(task.user_id),
            "symbol": str(task.symbol),
            "task_type": str(task.task_type),
            "spot_exchange": str(task.spot_exchange),
            "derivative_exchange": str(task.derivative_exchange),
            "failure_reason": getattr(task, "failure_reason", None),
            "retry_count": int(getattr(task, "retry_count", 0) or 0),
            "max_retry_count": int(getattr(task, "max_retry_count", 0) or 0),
            "auto_recovery_status": str(
                getattr(task, "auto_recovery_status", "NONE") or "NONE"
            ),
            "cooldown_until": (
                cooldown_until.isoformat() if cooldown_until is not None else None
            ),
            "next_action": "COOLDOWN",
        },
    )


def _build_arb_recovery_exhausted_event(
    *,
    region: str,
    task,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type="arb.recovery.exhausted",
        level="ERROR",
        service="arb_executor",
        region=region,
        symbol=str(task.symbol),
        exchange=str(task.spot_exchange),
        exchanges=[str(task.spot_exchange), str(task.derivative_exchange)],
        message="arbitrage recovery exhausted",
        payload={
            "task_uuid": str(task.task_uuid),
            "user_id": str(task.user_id),
            "symbol": str(task.symbol),
            "task_type": str(task.task_type),
            "spot_exchange": str(task.spot_exchange),
            "derivative_exchange": str(task.derivative_exchange),
            "failure_reason": getattr(task, "failure_reason", None),
            "retry_count": int(getattr(task, "retry_count", 0) or 0),
            "max_retry_count": int(getattr(task, "max_retry_count", 0) or 0),
            "auto_recovery_status": str(
                getattr(task, "auto_recovery_status", "NONE") or "NONE"
            ),
            "next_action": "EXHAUSTED",
        },
    )


def _build_arb_dispatcher_user_discovered_event(
    *,
    region: str,
    payload: dict[str, object],
    user_id: str,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type="arb.dispatcher.user_discovered",
        level="INFO",
        service="arb_dispatcher",
        region=region,
        symbol=str(payload["symbol"]) if payload.get("symbol") is not None else None,
        exchange=(
            str(payload["spot_exchange"])
            if payload.get("spot_exchange") is not None
            else None
        ),
        exchanges=[
            str(payload["spot_exchange"]),
            str(payload["derivative_exchange"]),
        ],
        message="arbitrage dispatcher user discovered",
        payload={
            "user_id": str(user_id),
            "symbol": str(payload["symbol"]),
            "opportunity_type": str(payload["opportunity_type"]),
            "spot_exchange": str(payload["spot_exchange"]),
            "derivative_exchange": str(payload["derivative_exchange"]),
            "source_message_id": str(payload["source_message_id"]),
        },
    )


def _build_arb_dispatcher_task_created_event(
    *,
    region: str,
    payload: dict[str, object],
    user_id: str,
    worker_node_id: str,
    task_record,
    strategy,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type="arb.dispatcher.task_created",
        level="INFO",
        service="arb_dispatcher",
        region=region,
        symbol=str(payload["symbol"]),
        exchange=str(payload["spot_exchange"]),
        exchanges=[str(payload["spot_exchange"]), str(payload["derivative_exchange"])],
        message="arbitrage dispatcher task created",
        payload={
            "task_uuid": str(task_record.task_uuid),
            "user_id": str(user_id),
            "strategy_config_id": (
                None if strategy is None else str(getattr(strategy, "id", None))
            ),
            "symbol": str(payload["symbol"]),
            "opportunity_type": str(payload["opportunity_type"]),
            "spot_exchange": str(payload["spot_exchange"]),
            "derivative_exchange": str(payload["derivative_exchange"]),
            "worker_node_id": str(worker_node_id),
        },
    )


def _build_arb_dispatcher_task_skipped_event(
    *,
    region: str,
    payload: dict[str, object],
    user_id: str,
    skip_reason: str,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type="arb.dispatcher.task_skipped",
        level="INFO",
        service="arb_dispatcher",
        region=region,
        symbol=str(payload["symbol"]),
        exchange=str(payload["spot_exchange"]),
        exchanges=[str(payload["spot_exchange"]), str(payload["derivative_exchange"])],
        message="arbitrage dispatcher task skipped",
        payload={
            "user_id": str(user_id),
            "symbol": str(payload["symbol"]),
            "opportunity_type": str(payload["opportunity_type"]),
            "skip_reason": skip_reason,
            "source_message_id": str(payload["source_message_id"]),
        },
    )


def _parse_market_type_scope(raw_value: Any) -> set[str]:
    if raw_value is None:
        return set()
    values = {
        part.strip().lower()
        for part in str(raw_value).split(",")
        if part.strip()
    }
    return {value for value in values if value in {"spot", "swap"}}


def _normalize_account_region(raw_value: Any) -> str:
    if raw_value is None:
        return "default"
    normalized = str(raw_value).strip().lower()
    return normalized or "default"


def _matches_strategy(payload: dict, strategy) -> bool:
    symbol = str(payload["symbol"])
    buy_exchange = str(payload["buy_exchange"])
    sell_exchange = str(payload["sell_exchange"])
    spread_bps = float(payload.get("spread_bps", 0.0))

    symbols = list(getattr(strategy, "symbol_scope_json", []) or [])
    exchanges = set(getattr(strategy, "exchange_scope_json", []) or [])

    if symbols and symbol not in symbols:
        return False
    if exchanges and ({buy_exchange, sell_exchange} - exchanges):
        return False
    if spread_bps < float(getattr(strategy, "open_spread_bps_threshold", 0.0)):
        return False
    return True


def _evaluate_account_exchange_coverage(
    *,
    payload: dict,
    accounts: list[Any],
    dispatcher_region: str = "default",
) -> dict[str, object]:
    available_exchanges = sorted(
        {str(account.exchange) for account in accounts if getattr(account, "exchange", None)}
    )
    auto_trade_enabled_exchanges = sorted(
        {
            str(account.exchange)
            for account in accounts
            if getattr(account, "exchange", None)
            and getattr(account, "is_auto_trade_enabled", True)
        }
    )
    allowed_market_types = ["spot", "swap"]
    allowed_market_type_set = set(allowed_market_types)
    normalized_dispatcher_region = _normalize_account_region(dispatcher_region)
    market_type_scopes_by_exchange: dict[str, list[str]] = {}
    account_regions_by_exchange: dict[str, list[str]] = {}
    market_type_covered_exchanges: set[str] = set()
    region_covered_exchanges: set[str] = set()

    for account in accounts:
        exchange = getattr(account, "exchange", None)
        if not exchange:
            continue
        exchange_name = str(exchange)
        parsed_scope = _parse_market_type_scope(
            getattr(account, "market_type_scope", None)
        )
        normalized_region = _normalize_account_region(
            getattr(account, "account_region", None)
        )
        existing_scope = set(market_type_scopes_by_exchange.get(exchange_name, []))
        existing_scope.update(parsed_scope)
        market_type_scopes_by_exchange[exchange_name] = sorted(existing_scope)
        existing_regions = set(account_regions_by_exchange.get(exchange_name, []))
        existing_regions.add(normalized_region)
        account_regions_by_exchange[exchange_name] = sorted(existing_regions)
        if (
            getattr(account, "is_auto_trade_enabled", True)
            and allowed_market_type_set.intersection(parsed_scope)
        ):
            market_type_covered_exchanges.add(exchange_name)
            if (
                normalized_region == "default"
                or normalized_region == normalized_dispatcher_region
            ):
                region_covered_exchanges.add(exchange_name)

    buy_exchange = str(payload["buy_exchange"])
    sell_exchange = str(payload["sell_exchange"])
    has_exchange_coverage = (
        buy_exchange in available_exchanges and sell_exchange in available_exchanges
    )
    has_auto_trade_coverage = (
        buy_exchange in auto_trade_enabled_exchanges
        and sell_exchange in auto_trade_enabled_exchanges
    )
    has_market_type_coverage = (
        buy_exchange in market_type_covered_exchanges
        and sell_exchange in market_type_covered_exchanges
    )
    has_region_coverage = (
        buy_exchange in region_covered_exchanges
        and sell_exchange in region_covered_exchanges
    )
    return {
        "available_exchanges": available_exchanges,
        "auto_trade_enabled_exchanges": auto_trade_enabled_exchanges,
        "market_type_scopes_by_exchange": market_type_scopes_by_exchange,
        "dispatcher_region": normalized_dispatcher_region,
        "account_regions_by_exchange": account_regions_by_exchange,
        "allowed_market_types": allowed_market_types,
        "has_exchange_coverage": has_exchange_coverage,
        "has_auto_trade_coverage": has_auto_trade_coverage,
        "has_market_type_coverage": has_market_type_coverage,
        "has_region_coverage": has_region_coverage,
    }


def _iter_eligible_accounts_for_exchange(
    *,
    accounts: list[Any],
    exchange: str,
    dispatcher_region: str = "default",
) -> list[Any]:
    allowed_market_type_set = {"spot", "swap"}
    normalized_dispatcher_region = _normalize_account_region(dispatcher_region)
    candidates = []
    for account in accounts:
        if str(getattr(account, "exchange", "")) != exchange:
            continue
        if not getattr(account, "is_auto_trade_enabled", True):
            continue
        parsed_scope = _parse_market_type_scope(getattr(account, "market_type_scope", None))
        if not allowed_market_type_set.intersection(parsed_scope):
            continue
        normalized_region = _normalize_account_region(
            getattr(account, "account_region", None)
        )
        if (
            normalized_region != "default"
            and normalized_region != normalized_dispatcher_region
        ):
            continue
        candidates.append(account)
    return candidates


def _select_bound_accounts(
    *,
    payload: dict,
    accounts: list[Any],
    dispatcher_region: str = "default",
) -> dict[str, Any] | None:
    buy_exchange = str(payload["buy_exchange"])
    sell_exchange = str(payload["sell_exchange"])
    buy_candidates = _iter_eligible_accounts_for_exchange(
        accounts=accounts,
        exchange=buy_exchange,
        dispatcher_region=dispatcher_region,
    )
    sell_candidates = _iter_eligible_accounts_for_exchange(
        accounts=accounts,
        exchange=sell_exchange,
        dispatcher_region=dispatcher_region,
    )
    if not buy_candidates or not sell_candidates:
        return None
    return {
        "buy_account": min(buy_candidates, key=lambda item: int(getattr(item, "id", 0))),
        "sell_account": min(
            sell_candidates, key=lambda item: int(getattr(item, "id", 0))
        ),
    }


class ExecutorPreflightError(RuntimeError):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class ExecutorPreflightValidator:
    required_fields = (
        "task_uuid",
        "user_id",
        "symbol",
        "buy_exchange",
        "sell_exchange",
    )

    def validate(
        self,
        *,
        payload: dict[str, Any],
        execution_accounts_by_exchange: dict[str, Any] | None,
    ) -> None:
        for field in self.required_fields:
            raw_value = payload.get(field)
            if raw_value is None or str(raw_value).strip() == "":
                raise ExecutorPreflightError(
                    "executor_preflight_invalid_payload",
                    f"missing required field: {field}",
                )

        buy_exchange = str(payload["buy_exchange"])
        sell_exchange = str(payload["sell_exchange"])

        try:
            target_quote_amount = float(payload.get("target_quote_amount", "0"))
        except (TypeError, ValueError) as exc:
            raise ExecutorPreflightError(
                "executor_preflight_invalid_amount",
                "target_quote_amount must be a positive number",
            ) from exc
        if target_quote_amount <= 0:
            raise ExecutorPreflightError(
                "executor_preflight_invalid_amount",
                "target_quote_amount must be a positive number",
            )

        if (
            payload.get("buy_account_id") is not None
            and payload.get("sell_account_id") is not None
        ):
            if not execution_accounts_by_exchange:
                raise ExecutorPreflightError(
                    "executor_preflight_account_resolution_failed",
                    "binding payload requires resolved execution accounts",
                )
            for exchange in (buy_exchange, sell_exchange):
                if execution_accounts_by_exchange.get(exchange) is None:
                    raise ExecutorPreflightError(
                        "executor_preflight_account_resolution_failed",
                        f"missing resolved execution account for exchange={exchange}",
                    )

        if execution_accounts_by_exchange:
            for exchange in (buy_exchange, sell_exchange):
                resolved_account = execution_accounts_by_exchange.get(exchange)
                if resolved_account is None:
                    continue
                resolved_exchange = (
                    resolved_account.get("exchange")
                    if isinstance(resolved_account, dict)
                    else getattr(resolved_account, "exchange", None)
                )
                if resolved_exchange is None:
                    continue
                if str(resolved_exchange) != exchange:
                    raise ExecutorPreflightError(
                        "executor_preflight_account_exchange_mismatch",
                        f"resolved execution account exchange mismatch for {exchange}",
                    )
            bound_account_ids_by_exchange = {
                buy_exchange: payload.get("buy_account_id"),
                sell_exchange: payload.get("sell_account_id"),
            }
            for exchange, bound_account_id in bound_account_ids_by_exchange.items():
                if bound_account_id is None:
                    continue
                resolved_account = execution_accounts_by_exchange.get(exchange)
                if resolved_account is None:
                    continue
                resolved_account_id = (
                    resolved_account.get("account_id")
                    if isinstance(resolved_account, dict)
                    else getattr(resolved_account, "account_id", None)
                )
                if resolved_account_id is None:
                    continue
                if str(resolved_account_id) != str(bound_account_id):
                    raise ExecutorPreflightError(
                        "executor_preflight_account_resolution_failed",
                        f"resolved execution account id mismatch for {exchange}",
                    )


def _classify_arbitrage_failure(
    *,
    execution_status: str,
    failure_reason: str,
    repair_result: Any | None = None,
) -> str:
    if repair_result is not None and not getattr(repair_result, "ok", False):
        return "REPAIR_FAILED"

    normalized = (failure_reason or "").lower()
    transient_network_keywords = (
        "timeout",
        "connection",
        "network",
        "reset",
        "temporarily unavailable",
    )
    temporary_route_keywords = (
        "route",
        "missing execution account",
        "dispatcher region",
    )
    exchange_rejected_keywords = (
        "reject",
        "invalid",
        "insufficient",
        "reduce-only",
        "order not accepted",
    )

    if any(keyword in normalized for keyword in transient_network_keywords):
        return "TRANSIENT_NETWORK"
    if any(keyword in normalized for keyword in temporary_route_keywords):
        return "TEMPORARY_ROUTE"
    if any(keyword in normalized for keyword in exchange_rejected_keywords):
        return "EXCHANGE_REJECTED"
    return "UNKNOWN_HARD_FAILURE"


@dataclass(slots=True)
class ArbitrageAutoRecoveryDecision:
    action: str
    failure_reason: str
    cooldown_until: datetime | None = None


def _cooldown_seconds_for_failure_category(*, failure_category: str, retry_count: int) -> int:
    base_windows = {
        "TRANSIENT_NETWORK": 0,
        "TEMPORARY_ROUTE": 60,
        "EXCHANGE_REJECTED": 300,
        "REPAIR_FAILED": 180,
        "UNKNOWN_HARD_FAILURE": 0,
    }
    base_seconds = int(base_windows.get(failure_category, 0) or 0)
    if base_seconds <= 0:
        return 0
    multiplier = min(max(int(retry_count or 0) + 1, 1), 3)
    return base_seconds * multiplier


def _decide_arbitrage_recovery(
    *,
    task,
    failure_category: str,
    failure_reason: str,
    now: datetime | None = None,
) -> ArbitrageAutoRecoveryDecision:
    retry_count = int(getattr(task, "retry_count", 0) or 0)
    auto_recovery_status = str(
        getattr(task, "auto_recovery_status", "NONE") or "NONE"
    )
    current_time = now or datetime.utcnow()

    if failure_category == "TRANSIENT_NETWORK":
        return ArbitrageAutoRecoveryDecision(
            action="RETRY_PENDING",
            failure_reason=failure_category,
        )
    if failure_category in {"TEMPORARY_ROUTE", "EXCHANGE_REJECTED"}:
        cooldown_seconds = _cooldown_seconds_for_failure_category(
            failure_category=failure_category,
            retry_count=retry_count,
        )
        return ArbitrageAutoRecoveryDecision(
            action="COOLDOWN",
            failure_reason=failure_category,
            cooldown_until=current_time + timedelta(seconds=cooldown_seconds),
        )
    if failure_category == "REPAIR_FAILED":
        if auto_recovery_status == "COOLDOWN":
            return ArbitrageAutoRecoveryDecision(
                action="EXHAUSTED",
                failure_reason=failure_category,
            )
        cooldown_seconds = _cooldown_seconds_for_failure_category(
            failure_category=failure_category,
            retry_count=retry_count,
        )
        return ArbitrageAutoRecoveryDecision(
            action="COOLDOWN",
            failure_reason=failure_category,
            cooldown_until=current_time + timedelta(seconds=cooldown_seconds),
        )
    return ArbitrageAutoRecoveryDecision(
        action="EXHAUSTED",
        failure_reason="UNKNOWN_HARD_FAILURE",
    )


class ArbitrageExecutionTaskConsumer:
    def __init__(
        self,
        *,
        task_repository,
        execution_adapter,
        repair_service,
        account_repository,
        worker_node_id: str,
        env_mode: str = "testnet",
        risk_manager: RiskManager | None = None,
        event_router=None,
        region: str | None = None,
        auto_recovery_cooldown_seconds: int = 300,
        order_recorder=None,
    ) -> None:
        self.task_repository = task_repository
        self.execution_adapter = execution_adapter
        self.repair_service = repair_service
        self.account_repository = account_repository
        self.worker_node_id = worker_node_id
        self.env_mode = env_mode
        self.risk_manager = risk_manager or RiskManager()
        self.event_router = event_router
        self.region = region or worker_node_id
        self.auto_recovery_cooldown_seconds = auto_recovery_cooldown_seconds
        self.order_recorder = order_recorder
        if self.order_recorder is not None:
            self.execution_adapter.execution_service.order_recorder = self.order_recorder
            self.repair_service.order_recorder = self.order_recorder

    def _resolve_execution_exchanges(self, task) -> tuple[str, str]:
        task_type = str(getattr(task, "task_type", "")).lower()
        spot_exchange = str(task.spot_exchange)
        derivative_exchange = str(task.derivative_exchange)
        if task_type == "open":
            return spot_exchange, derivative_exchange
        if task_type == "close":
            return derivative_exchange, spot_exchange
        raise ValueError(f"unsupported task_type: {task.task_type}")

    def _is_user_trading_enabled(self, user_id: int) -> bool:
        try:
            session = self.account_repository.session if self.account_repository else None
            if session is None:
                return True
            from models import User
            user = session.query(User).filter(
                User.id == user_id, User.is_trading_enabled.is_(True)
            ).first()
            return user is not None
        except Exception:
            return True

    def _build_execution_accounts(self, task) -> dict[str, Any]:
        accounts = list(
            self.account_repository.list_enabled_accounts(
                user_id=int(task.user_id),
                env_mode=self.env_mode,
            )
            or []
        )
        dispatcher_region = _normalize_account_region(
            getattr(task, "home_region", self.worker_node_id)
        )
        execution_accounts: dict[str, Any] = {}
        for exchange in self._resolve_execution_exchanges(task):
            candidates = _iter_eligible_accounts_for_exchange(
                accounts=accounts,
                exchange=exchange,
                dispatcher_region=dispatcher_region,
            )
            if not candidates:
                raise LookupError(f"missing execution account for exchange={exchange}")
            execution_accounts[exchange] = min(
                candidates,
                key=lambda item: int(getattr(item, "id", 0)),
            )
        return execution_accounts

    async def _apply_auto_recovery(
        self,
        *,
        task,
        execution_status: str,
        failure_reason: str,
        repair_result: Any | None = None,
    ) -> None:
        failure_category = _classify_arbitrage_failure(
            execution_status=execution_status,
            failure_reason=failure_reason,
            repair_result=repair_result,
        )
        decision = _decide_arbitrage_recovery(
            task=task,
            failure_category=failure_category,
            failure_reason=failure_reason,
        )
        updated_task = None
        if decision.action == "RETRY_PENDING":
            updated_task = self.task_repository.mark_auto_recovery_retry(
                str(task.task_uuid),
                failure_reason=decision.failure_reason,
            )
            if self.event_router is not None and updated_task is not None:
                await self.event_router.dispatch(
                    _build_arb_recovery_retry_scheduled_event(
                        region=self.region,
                        task=updated_task,
                    )
                )
            return
        if decision.action == "COOLDOWN":
            updated_task = self.task_repository.mark_auto_recovery_cooldown(
                str(task.task_uuid),
                failure_reason=decision.failure_reason,
                cooldown_until=decision.cooldown_until,
            )
            if self.event_router is not None and updated_task is not None:
                await self.event_router.dispatch(
                    _build_arb_recovery_cooldown_started_event(
                        region=self.region,
                        task=updated_task,
                    )
                )
            return
        updated_task = self.task_repository.mark_auto_recovery_exhausted(
            str(task.task_uuid),
            failure_reason=decision.failure_reason,
        )
        if self.event_router is not None and updated_task is not None:
            await self.event_router.dispatch(
                _build_arb_recovery_exhausted_event(
                    region=self.region,
                    task=updated_task,
                )
            )

    @staticmethod
    def _should_skip_repair(result) -> str | None:
        filled_exchanges = list(getattr(result, "filled_exchanges", []) or [])
        failed_exchanges = list(getattr(result, "failed_exchanges", []) or [])
        if not filled_exchanges and len(failed_exchanges) >= 2:
            return "both_legs_failed"
        reason = str(getattr(result, "reason", "") or "").lower()
        failed_errors_text = " ".join(
            str(e) for e in (getattr(result, "failed_errors", None) or [])
        ).lower()
        if any(kw in reason or kw in failed_errors_text
               for kw in ("insufficient", "balance_not_enough", "insufficientfunds")):
            return "insufficient_funds"
        return None

    async def _run_repair(
        self,
        *,
        task,
        result,
        credentials_by_exchange: dict[str, Any],
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
    ) -> None:
        buy_exchange, sell_exchange = self._resolve_execution_exchanges(task)
        failed_exchanges = list(getattr(result, "failed_exchanges", []) or [])
        filled_exchanges = list(getattr(result, "filled_exchanges", []) or [])
        repair_plan = self.risk_manager.build_repair_plan(
            ExecutionResult(
                status=str(getattr(result, "execution_status", "") or ""),
                filled_exchanges=filled_exchanges,
                failed_exchanges=failed_exchanges,
            )
        )
        if self.event_router is not None:
            await self.event_router.dispatch(
                _build_arb_executor_repair_planned_event(
                    region=self.region,
                    task=task,
                    execution_status=str(getattr(result, "execution_status", "") or ""),
                    filled_exchanges=filled_exchanges,
                    failed_exchanges=failed_exchanges,
                    repair_plan=repair_plan,
                )
            )
        repair_result = await self.repair_service.run_task(
            task_uuid=str(task.task_uuid),
            symbol=str(task.symbol),
            buy_exchange=buy_exchange,
            sell_exchange=sell_exchange,
            target_exchanges=failed_exchanges,
            credentials_by_exchange=credentials_by_exchange,
            target_quote_amount=float(task.target_notional),
            env_mode=self.env_mode,
            proxies_by_exchange=proxies_by_exchange,
            db_task_id=int(getattr(task, "id", 0)),
        )
        if getattr(repair_result, "ok", False):
            repaired_exchanges = list(
                getattr(repair_result, "repaired_exchanges", []) or []
            )
            self.task_repository.mark_repair_result(
                str(task.task_uuid),
                lifecycle_status="SUCCEEDED",
                execution_status="OPEN_HEDGED",
                filled_exchanges=list(dict.fromkeys(filled_exchanges + repaired_exchanges)),
                failed_exchanges=[],
                repair_action=repair_plan.action,
                repair_reason="repair_succeeded",
                status_reason=None,
            )
            if self.event_router is not None:
                await self.event_router.dispatch(
                    _build_arb_repair_finished_event(
                        region=self.region,
                        task=task,
                        result=repair_result,
                    )
                )
            return

        remaining_failed_exchanges = list(
            getattr(repair_result, "remaining_failed_exchanges", []) or failed_exchanges
        )
        if self.event_router is not None:
            await self.event_router.dispatch(
                _build_arb_repair_finished_event(
                    region=self.region,
                    task=task,
                    result=repair_result,
                )
            )
        failure_reason = str(
            getattr(repair_result, "reason", None)
            or getattr(repair_result, "status", "")
            or "repair_failed_manual_required"
        )
        await self._apply_auto_recovery(
            task=task,
            execution_status=str(getattr(result, "execution_status", "") or ""),
            failure_reason=failure_reason,
            repair_result=repair_result,
        )

    async def run_once(
        self,
        *,
        credentials_by_exchange: dict[str, Any],
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
    ) -> int:
        task = self.task_repository.claim_next_executable_task(
            worker_node_id=self.worker_node_id,
            env_mode=self.env_mode,
        )
        if task is None:
            return 0
        if not self._is_user_trading_enabled(int(task.user_id)):
            self.task_repository.mark_failed(
                task_uuid=str(task.task_uuid),
                reason="user_trading_disabled",
            )
            return 0
        try:
            execution_accounts = self._build_execution_accounts(task)
            result = await self.execution_adapter.execute_task(
                task=task,
                credentials_by_exchange=credentials_by_exchange,
                execution_accounts_by_exchange=execution_accounts,
                env_mode=self.env_mode,
                proxies_by_exchange=proxies_by_exchange,
            )
            if (
                hasattr(result, "order_ids")
                and result.order_ids
                and self.order_recorder is not None
            ):
                from app.trading.order_poller import OrderPoller

                poller = OrderPoller(
                    order_recorder=self.order_recorder,
                    adapter_factory=self.adapter_factory,
                )
                for exchange, oid in result.order_ids.items():
                    if exchange not in (getattr(result, "failed_exchanges", []) or []):
                        real_eid = (result.exchange_order_ids or {}).get(exchange, "pending")
                        asyncio.create_task(
                            poller.poll_until_closed(
                                order_id=oid,
                                exchange=exchange,
                                exchange_order_id=real_eid,
                                symbol=task.symbol,
                                task_id=task.id if hasattr(task, "id") else 0,
                                current_filled=0.0,
                            )
                        )
            if (
                self.event_router is not None
                and getattr(result, "execution_status", None) is not None
            ):
                failed_errors = getattr(result, "failed_errors", None) or []
                await self.event_router.dispatch(
                    _build_arb_executor_execution_result_event(
                        region=self.region,
                        task=task,
                        result=result,
                    )
                )
            execution_status = str(
                getattr(result, "execution_status", "FAILED") or "FAILED"
            )
            filled_exchanges = list(getattr(result, "filled_exchanges", []) or [])
            failed_exchanges = list(getattr(result, "failed_exchanges", []) or [])
            repair_plan = self.risk_manager.build_repair_plan(
                ExecutionResult(
                    status=execution_status,
                    filled_exchanges=filled_exchanges,
                    failed_exchanges=failed_exchanges,
                )
            )
            if getattr(result, "ok", False):
                self.task_repository.mark_execution_result(
                    str(task.task_uuid),
                    lifecycle_status="SUCCEEDED",
                    execution_status=execution_status,
                    filled_exchanges=filled_exchanges,
                    failed_exchanges=failed_exchanges,
                    repair_action=repair_plan.action,
                    repair_reason=repair_plan.reason,
                )
                return 1
            if execution_status == "SKIPPED":
                self.task_repository.mark_failed(
                    str(task.task_uuid),
                    reason=str(getattr(result, "reason", None) or "skipped"),
                )
                return 1
            if execution_status == "OPEN_PARTIAL" and failed_exchanges:
                skip_reason = self._should_skip_repair(result)
                if skip_reason:
                    self.task_repository.mark_failed(
                        str(task.task_uuid),
                        reason=f"skip_repair: {skip_reason}",
                    )
                    if self.event_router is not None:
                        await self.event_router.dispatch(
                            _build_arb_executor_task_failed_event(
                                region=self.region,
                                task=task,
                                result=result,
                            )
                        )
                    return 1
                await self._run_repair(
                    task=task,
                    result=result,
                    credentials_by_exchange=credentials_by_exchange,
                    proxies_by_exchange=proxies_by_exchange,
                )
                return 1
            if self.event_router is not None:
                await self.event_router.dispatch(
                    _build_arb_executor_task_failed_event(
                        region=self.region,
                        task=task,
                        result=result,
                    )
                )
            failure_reason = str(
                getattr(result, "reason", None)
                or getattr(result, "execution_status", "")
                or "execution_failed_non_repairable"
            )
            await self._apply_auto_recovery(
                task=task,
                execution_status=execution_status,
                failure_reason=failure_reason,
            )
            return 1
        except Exception as exc:
            self.task_repository.mark_failed(
                str(task.task_uuid),
                reason=getattr(exc, "reason", str(exc)),
            )
            return 0


class ContinuousSpotScanner:
    def __init__(
        self,
        *,
        flow_service,
        poll_interval_seconds: float = 1.0,
        event_router=None,
        region: str = "default",
    ) -> None:
        self.flow_service = flow_service
        self.poll_interval_seconds = poll_interval_seconds
        self.event_router = event_router
        self.region = region

    async def run(
        self,
        *,
        exchanges: list[str],
        credentials_by_exchange: dict,
        symbol: str | None = None,
        symbols: list[str] | None = None,
        env_mode: str = "testnet",
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
        orderbook_depth_limit: int = 5,
        target_quote_amount: float = 100.0,
        max_iterations: int | None = None,
        batch_concurrency: int = 15,
        batch_threshold: int = 10,
        symbol_exchanges: dict[str, list[str]] | None = None,
    ) -> None:
        active_symbols = symbols or ([symbol] if symbol is not None else [])
        if len(active_symbols) >= batch_threshold:
            iteration = 0
            while max_iterations is None or iteration < max_iterations:
                results = await self.flow_service.run_batch(
                    exchanges=exchanges,
                    credentials_by_exchange=credentials_by_exchange,
                    symbols=active_symbols,
                    symbol_exchanges=symbol_exchanges,
                    env_mode=env_mode,
                    proxies_by_exchange=proxies_by_exchange,
                    orderbook_depth_limit=orderbook_depth_limit,
                    target_quote_amount=target_quote_amount,
                    concurrency=batch_concurrency,
                )
                for result in results:
                    if self.event_router is not None:
                        await self.event_router.dispatch(
                            RuntimeEvent(
                                event_type="opportunity.detected",
                                level="INFO",
                                service="scanner",
                                region=self.region,
                                symbol=result.symbol,
                                message="opportunity detected",
                                payload={
                                    "buy_exchange": result.buy_exchange,
                                    "sell_exchange": result.sell_exchange,
                                    "spread_bps": result.spread_bps,
                                },
                            )
                        )
                if self.event_router is not None:
                    await self.event_router.dispatch(
                        RuntimeEvent(
                            event_type="scanner.iteration.succeeded",
                            level="INFO",
                            service="scanner",
                            region=self.region,
                            symbol=None,
                            message="scanner iteration succeeded",
                            payload={
                                "exchanges": exchanges,
                                "symbols_count": len(active_symbols),
                                "opportunities_count": len(results),
                            },
                        )
                    )
                iteration += 1
                if max_iterations is None or iteration < max_iterations:
                    await asyncio.sleep(self.poll_interval_seconds)
            return

        iteration = 0
        while max_iterations is None or iteration < max_iterations:
            for active_symbol in active_symbols:
                try:
                    result = await self.flow_service.run_once(
                        exchanges=exchanges,
                        credentials_by_exchange=credentials_by_exchange,
                        symbol=active_symbol,
                        env_mode=env_mode,
                        proxies_by_exchange=proxies_by_exchange,
                        orderbook_depth_limit=orderbook_depth_limit,
                        target_quote_amount=target_quote_amount,
                    )
                    if self.event_router is not None and result is not None:
                        await self.event_router.dispatch(
                            RuntimeEvent(
                                event_type="opportunity.detected",
                                level="INFO",
                                service="scanner",
                                region=self.region,
                                symbol=active_symbol,
                                message="opportunity detected",
                                payload={
                                    "buy_exchange": result.buy_exchange,
                                    "sell_exchange": result.sell_exchange,
                                    "spread_bps": result.spread_bps,
                                },
                            )
                        )
                        await self.event_router.dispatch(
                            RuntimeEvent(
                                event_type="scanner.iteration.succeeded",
                                level="INFO",
                                service="scanner",
                                region=self.region,
                                symbol=active_symbol,
                                message="scanner iteration succeeded",
                                payload={"exchanges": exchanges},
                            )
                        )
                except Exception as exc:
                    if self.event_router is not None:
                        await self.event_router.dispatch(
                            RuntimeEvent(
                                event_type="scanner.iteration.failed",
                                level="ERROR",
                                service="scanner",
                                region=self.region,
                                symbol=active_symbol,
                                message="scanner iteration failed",
                                payload={"error": str(exc)},
                            )
                        )
            iteration += 1
            if max_iterations is None or iteration < max_iterations:
                await asyncio.sleep(self.poll_interval_seconds)


class ContinuousArbitrageScanner:
    def __init__(
        self,
        *,
        flow_service,
        poll_interval_seconds: float = 5.0,
        event_router=None,
        region: str = "default",
    ) -> None:
        self.flow_service = flow_service
        self.poll_interval_seconds = poll_interval_seconds
        self.event_router = event_router
        self.region = region

    async def run(
        self,
        *,
        exchanges: list[str],
        credentials_by_exchange: dict,
        symbol_swap_map: dict[str, dict[str, str]],
        env_mode: str = "testnet",
        proxies_by_exchange: dict[str, dict[str, str]] | None = None,
        orderbook_depth_limit: int = 5,
        max_iterations: int | None = None,
        batch_concurrency: int = 8,
    ) -> None:
        iteration = 0
        while max_iterations is None or iteration < max_iterations:
            try:
                results = await self.flow_service.run_batch(
                    exchanges=exchanges,
                    credentials_by_exchange=credentials_by_exchange,
                    symbol_swap_map=symbol_swap_map,
                    env_mode=env_mode,
                    proxies_by_exchange=proxies_by_exchange,
                    orderbook_depth_limit=orderbook_depth_limit,
                    concurrency=batch_concurrency,
                )
                for r in results:
                    if self.event_router is not None:
                        await self.event_router.dispatch(
                            RuntimeEvent(
                                event_type="arbitrage.opportunity.detected",
                                level="INFO",
                                service="arb_scanner",
                                region=self.region,
                                symbol=r["symbol"],
                                exchange=r["exchange"],
                                message="arbitrage opportunity detected",
                                payload={
                                    "symbol": r["symbol"],
                                    "exchange": r["exchange"],
                                    "open_spread_bps": r["open_spread_bps"],
                                    "close_spread_bps": r["close_spread_bps"],
                                    "funding_rate": r["funding_rate"],
                                },
                            )
                        )
                if self.event_router is not None:
                    await self.event_router.dispatch(
                        RuntimeEvent(
                            event_type="arb_scanner.iteration.succeeded",
                            level="INFO",
                            service="arb_scanner",
                            region=self.region,
                            symbol=None,
                            message="arbitrage scanner iteration succeeded",
                            payload={
                                "exchanges": exchanges,
                                "symbols_count": len(symbol_swap_map),
                                "opportunities_count": len(results),
                            },
                        )
                    )
            except Exception as exc:
                if self.event_router is not None:
                    await self.event_router.dispatch(
                        RuntimeEvent(
                            event_type="arb_scanner.iteration.failed",
                            level="ERROR",
                            service="arb_scanner",
                            region=self.region,
                            message="arbitrage scanner iteration failed",
                            payload={"error": str(exc)},
                        )
                    )
            iteration += 1
            if max_iterations is None or iteration < max_iterations:
                await asyncio.sleep(self.poll_interval_seconds)


class RedisSpotConsumer:
    processed_event_type = "consumer.message.processed"
    processed_event_service = "consumer"
    processed_event_message = "consumer message processed"
    failed_event_type = "consumer.message.failed"
    failed_event_service = "consumer"
    failed_event_message = "consumer message failed"

    def __init__(
        self,
        *,
        redis_client,
        dispatcher,
        stream_key: str,
        block_ms: int = 1000,
        event_router=None,
        region: str = "default",
    ) -> None:
        self.redis_client = redis_client
        self.dispatcher = dispatcher
        self.stream_key = stream_key
        self.block_ms = block_ms
        self.event_router = event_router
        self.region = region
        self.last_id = "0-0"

    def _build_processed_event(self, *, message_id: str, payload: dict) -> RuntimeEvent:
        return RuntimeEvent(
            event_type=self.processed_event_type,
            level="INFO",
            service=self.processed_event_service,
            region=self.region,
            symbol=payload.get("symbol"),
            message=self.processed_event_message,
            payload={
                "message_id": message_id,
                "buy_exchange": payload.get("buy_exchange"),
                "sell_exchange": payload.get("sell_exchange"),
                "spread_bps": float(payload.get("spread_bps", 0.0)),
            },
        )

    def _build_failed_event(
        self, *, message_id: str, payload: dict, error: Exception
    ) -> RuntimeEvent:
        return RuntimeEvent(
            event_type=self.failed_event_type,
            level="ERROR",
            service=self.failed_event_service,
            region=self.region,
            symbol=payload.get("symbol"),
            message=self.failed_event_message,
            payload={
                "message_id": message_id,
                "error": str(error),
            },
        )

    async def run(
        self,
        *,
        credentials_by_exchange: dict,
        max_iterations: int | None = None,
    ) -> int:
        iteration = 0
        processed = 0
        while max_iterations is None or iteration < max_iterations:
            entries = await self.redis_client.xread(
                {self.stream_key: self.last_id},
                count=1,
                block=self.block_ms,
            )
            for _, messages in entries:
                for message_id, payload in messages:
                    try:
                        await self.dispatcher.dispatch(
                            payload,
                            credentials_by_exchange=credentials_by_exchange,
                        )
                        self.last_id = message_id
                        processed += 1
                        if self.event_router is not None:
                            await self.event_router.dispatch(
                                self._build_processed_event(
                                    message_id=message_id,
                                    payload=payload,
                                )
                            )
                    except Exception as exc:
                        if self.event_router is not None:
                            await self.event_router.dispatch(
                                self._build_failed_event(
                                    message_id=message_id,
                                    payload=payload,
                                    error=exc,
                                )
                            )
            iteration += 1
        return processed


class RedisArbitrageConsumer(RedisSpotConsumer):
    processed_event_type = "arb.consumer.message.processed"
    processed_event_service = "arb_consumer"
    processed_event_message = "arbitrage opportunity processed"
    failed_event_type = "arb.consumer.message.failed"
    failed_event_service = "arb_consumer"
    failed_event_message = "arbitrage opportunity failed"

    async def run(
        self,
        *,
        credentials_by_exchange: dict | None = None,
        max_iterations: int | None = None,
    ) -> int:
        iteration = 0
        processed = 0
        while max_iterations is None or iteration < max_iterations:
            entries = await self.redis_client.xread(
                {self.stream_key: self.last_id},
                count=1,
                block=self.block_ms,
            )
            for _, messages in entries:
                for message_id, payload in messages:
                    enriched_payload = dict(payload)
                    enriched_payload["source_message_id"] = str(
                        payload.get("source_message_id", message_id)
                    )
                    try:
                        await self.dispatcher.dispatch(
                            enriched_payload,
                            credentials_by_exchange=credentials_by_exchange or {},
                        )
                        self.last_id = message_id
                        processed += 1
                        if self.event_router is not None:
                            await self.event_router.dispatch(
                                self._build_processed_event(
                                    message_id=message_id,
                                    payload=enriched_payload,
                                )
                            )
                    except Exception as exc:
                        if self.event_router is not None:
                            await self.event_router.dispatch(
                                self._build_failed_event(
                                    message_id=message_id,
                                    payload=enriched_payload,
                                    error=exc,
                                )
                            )
            iteration += 1
        return processed


class RedisArbitrageTaskDispatcher:
    def __init__(
        self,
        *,
        redis_client,
        user_ids: list[str],
        route_resolver,
        task_repository=None,
        strategy_repository=None,
        dispatch_user_repository=None,
        account_repository=None,
        stream_key: str,
        block_ms: int = 1000,
        event_router=None,
        region: str = "default",
        env_mode: str = "testnet",
    ) -> None:
        self.redis_client = redis_client
        self.user_ids = user_ids
        self.route_resolver = route_resolver
        self.task_repository = task_repository
        self.strategy_repository = strategy_repository
        self.dispatch_user_repository = dispatch_user_repository
        self.account_repository = account_repository
        self.stream_key = stream_key
        self.block_ms = block_ms
        self.event_router = event_router
        self.region = region
        self.env_mode = env_mode
        self.last_id = "0-0"

    def _resolve_candidate_user_ids(self) -> list[str]:
        if self.dispatch_user_repository is None:
            return list(self.user_ids)
        discovered_user_ids = self.dispatch_user_repository.list_dispatchable_user_ids(
            env_mode=self.env_mode
        )
        if not self.user_ids:
            return discovered_user_ids
        allowed_user_ids = set(discovered_user_ids)
        return [user_id for user_id in self.user_ids if user_id in allowed_user_ids]

    def _load_user_accounts(self, *, user_id: str) -> list[Any] | None:
        if self.account_repository is None:
            return None
        accounts = self.account_repository.list_enabled_accounts(
            user_id=int(user_id),
            env_mode=self.env_mode,
        )
        return list(accounts or [])

    def _get_user_node(self, *, user_id: str, payload: dict[str, Any]) -> str:
        if self.dispatch_user_repository is None:
            return "main"
        session = getattr(self.dispatch_user_repository, "session", None)
        if session is None:
            return "main"
        from models import User
        user = session.query(User).filter(User.id == int(user_id)).first()
        if user is None:
            return "main"
        return str(user.node_id) if user.node_id else "main"

    def _has_required_account_coverage(
        self, *, payload: dict[str, Any], accounts: list[Any] | None
    ) -> bool:
        if accounts is None:
            return True
        coverage = _evaluate_account_exchange_coverage(
            payload={
                "buy_exchange": payload["spot_exchange"],
                "sell_exchange": payload["derivative_exchange"],
            },
            accounts=accounts,
            dispatcher_region=self.region,
        )
        return bool(
            coverage["has_exchange_coverage"] and coverage["has_auto_trade_coverage"]
        )

    @staticmethod
    def _pick_best_tier(tiers: list | None, spread_bps: float, *, is_close: bool = False) -> dict | None:
        if not tiers:
            return None
        if is_close:
            candidates = [t for t in tiers if spread_bps <= float(t.get("spread_bps", 0))]
        else:
            candidates = [t for t in tiers if float(t.get("spread_bps", 0)) <= spread_bps]
        if not candidates:
            return None
        candidates.sort(key=lambda t: float(t.get("spread_bps", 0)), reverse=True)
        return candidates[0]

    def _iter_matching_strategies(self, *, user_id: str, payload: dict[str, Any]):
        if self.strategy_repository is None:
            return [None]

        strategies = self.strategy_repository.list_enabled_for_user(
            user_id=int(user_id),
            strategy_type="spot_futures",
        )
        matched = []
        payload_symbol = str(payload["symbol"])
        payload_exchanges = {
            str(payload["spot_exchange"]),
            str(payload["derivative_exchange"]),
        }
        open_spread_bps = float(payload.get("open_spread_bps", 0.0))
        close_spread_bps = float(payload.get("close_spread_bps", 0.0))
        opportunity_type = str(payload.get("opportunity_type", "OPEN"))
        for strategy in strategies:
            symbols = list(getattr(strategy, "symbol_scope_json", []) or [])
            exchanges = set(getattr(strategy, "exchange_scope_json", []) or [])
            _symbol_set = set(symbols)
            for s in list(_symbol_set):
                if "/" in s:
                    _symbol_set.add(s.split("/")[0])
                else:
                    _symbol_set.add(s + "/USDT")
            if symbols and payload_symbol not in _symbol_set:
                continue
            if exchanges and (payload_exchanges - exchanges):
                continue
            if len(exchanges) >= 2 and len(payload_exchanges) < 2:
                continue
            if opportunity_type == "OPEN":
                tiers = getattr(strategy, "open_tiers_json", []) or []
                if tiers:
                    if not RedisArbitrageTaskDispatcher._pick_best_tier(tiers, open_spread_bps):
                        continue
                else:
                    threshold = float(getattr(strategy, "open_spread_bps_threshold", 0.0) or 0.0)
                    if open_spread_bps < threshold:
                        continue
            elif opportunity_type == "CLOSE":
                tiers = getattr(strategy, "close_tiers_json", []) or []
                if tiers:
                    if not RedisArbitrageTaskDispatcher._pick_best_tier(tiers, close_spread_bps, is_close=True):
                        continue
                else:
                    threshold = float(getattr(strategy, "close_spread_bps_threshold", 0.0) or 0.0)
                    if close_spread_bps > threshold:
                        continue
            matched.append(strategy)
        return matched

    def _resolve_target_notional(self, *, strategy, task_type: str, payload: dict[str, Any]) -> float:
        if strategy is None:
            return 0.0
        base_amount = float(getattr(strategy, "target_quote_amount", 0.0) or 0.0)
        max_notional = float(getattr(strategy, "max_single_task_notional", 0.0) or 0.0)
        if task_type == "open":
            tiers = getattr(strategy, "open_tiers_json", []) or []
            spread_bps = float(payload.get("open_spread_bps", 0.0))
        else:
            tiers = getattr(strategy, "close_tiers_json", []) or []
            spread_bps = float(payload.get("close_spread_bps", 0.0))
        tier = RedisArbitrageTaskDispatcher._pick_best_tier(tiers, spread_bps, is_close=(task_type == "close"))
        ratio = float(tier["ratio"]) if tier else 1.0
        target_notional = base_amount * ratio
        if max_notional > 0:
            target_notional = min(target_notional, max_notional)
        return target_notional

    def _create_arbitrage_task(
        self,
        *,
        user_id: str,
        message_id: str,
        payload: dict[str, Any],
        strategy,
    ):
        if self.task_repository is None:
            return None
        strategy_id = None if strategy is None else int(strategy.id)
        task_type = "open" if str(payload["opportunity_type"]) == "OPEN" else "close"
        idempotency_key = f"{user_id}:{message_id}:{task_type}"
        if strategy_id is not None:
            idempotency_key = f"{idempotency_key}:{strategy_id}"
        return self.task_repository.create_task(
            ArbitrageTaskCreate(
                task_uuid=uuid4().hex,
                user_id=int(user_id),
                strategy_config_id=strategy_id,
                opportunity_id=message_id,
                env_mode=self.env_mode,
                task_type=task_type,
                symbol=str(payload["symbol"]),
                spot_exchange=str(payload["spot_exchange"]),
                derivative_exchange=str(payload["derivative_exchange"]),
                target_notional=self._resolve_target_notional(
                    strategy=strategy,
                    task_type=task_type,
                    payload=payload,
                ),
                expected_spread_bps=(
                    float(payload.get("open_spread_bps", 0.0))
                    if task_type == "open"
                    else float(payload.get("close_spread_bps", 0.0))
                ),
                expected_funding_bps=float(payload.get("funding_rate", 0.0)) * 10000,
                idempotency_key=idempotency_key,
                home_region=self.region,
            )
        )

    async def run(self, *, max_iterations: int | None = None) -> int:
        iteration = 0
        processed = 0
        while max_iterations is None or iteration < max_iterations:
            entries = await self.redis_client.xread(
                {self.stream_key: self.last_id},
                count=1,
                block=self.block_ms,
            )
            for _, messages in entries:
                for message_id, payload in messages:
                    effective_payload = dict(payload)
                    effective_payload["source_message_id"] = str(
                        payload.get("source_message_id", message_id)
                    )
                    for user_id in self._resolve_candidate_user_ids():
                        node_id = self._get_user_node(user_id=user_id, payload=effective_payload)
                        if node_id is None:
                            if self.event_router is not None:
                                await self.event_router.dispatch(
                                    _build_arb_dispatcher_task_skipped_event(
                                        region=self.region,
                                        payload=effective_payload,
                                        user_id=user_id,
                                        skip_reason="route_unavailable",
                                    )
                                )
                            continue
                        if self.event_router is not None:
                            await self.event_router.dispatch(
                                _build_arb_dispatcher_user_discovered_event(
                                    region=self.region,
                                    payload=effective_payload,
                                    user_id=user_id,
                                )
                            )
                        accounts = self._load_user_accounts(user_id=user_id)
                        if not self._has_required_account_coverage(
                            payload=effective_payload,
                            accounts=accounts,
                        ):
                            if self.event_router is not None:
                                await self.event_router.dispatch(
                                    _build_arb_dispatcher_task_skipped_event(
                                        region=self.region,
                                        payload=effective_payload,
                                        user_id=user_id,
                                        skip_reason="account_coverage_missing",
                                    )
                                )
                            continue
                        matched_any = False
                        for strategy in self._iter_matching_strategies(
                            user_id=user_id,
                            payload=effective_payload,
                        ):
                            matched_any = True
                            if str(effective_payload["opportunity_type"]) == "CLOSE":
                                if self.task_repository is None:
                                    continue
                                try:
                                    closeable = self.task_repository.find_closeable_task(
                                        user_id=int(user_id),
                                        symbol=str(effective_payload["symbol"]),
                                        spot_exchange=str(effective_payload["spot_exchange"]),
                                        derivative_exchange=str(
                                            effective_payload["derivative_exchange"]
                                        ),
                                        env_mode=self.env_mode,
                                    )
                                except Exception:
                                    if self.task_repository is not None:
                                        self.task_repository.session.rollback()
                                    if self.event_router is not None:
                                        await self.event_router.dispatch(
                                            _build_arb_dispatcher_task_skipped_event(
                                                region=self.region,
                                                payload=effective_payload,
                                                user_id=user_id,
                                                skip_reason="close_lookup_failed",
                                            )
                                        )
                                    continue
                                if closeable is None:
                                    if self.event_router is not None:
                                        await self.event_router.dispatch(
                                            _build_arb_dispatcher_task_skipped_event(
                                                region=self.region,
                                                payload=effective_payload,
                                                user_id=user_id,
                                                skip_reason="close_context_missing",
                                            )
                                        )
                                    continue
                            try:
                                if self.task_repository is not None and self.task_repository.has_recent_exhausted_cooldown(
                                    user_id=int(user_id),
                                    symbol=str(effective_payload["symbol"]),
                                    spot_exchange=str(effective_payload["spot_exchange"]),
                                    derivative_exchange=str(effective_payload["derivative_exchange"]),
                                    strategy_config_id=int(strategy.id) if strategy is not None else None,
                                    env_mode=self.env_mode,
                                ):
                                    if self.event_router is not None:
                                        await self.event_router.dispatch(
                                            _build_arb_dispatcher_task_skipped_event(
                                                region=self.region,
                                                payload=effective_payload,
                                                user_id=user_id,
                                                skip_reason="prior_task_exhausted",
                                            )
                                        )
                                    self.task_repository.session.rollback()
                                    continue
                                task_record = self._create_arbitrage_task(
                                    user_id=user_id,
                                    message_id=str(effective_payload["source_message_id"]),
                                    payload=effective_payload,
                                    strategy=strategy,
                                )
                            except Exception:
                                if self.task_repository is not None:
                                    self.task_repository.session.rollback()
                                continue
                            if task_record is not None and self.event_router is not None:
                                await self.event_router.dispatch(
                                    _build_arb_dispatcher_task_created_event(
                                        region=self.region,
                                        payload=effective_payload,
                                        user_id=user_id,
                                        worker_node_id=node_id,
                                        task_record=task_record,
                                        strategy=strategy,
                                    )
                                )
                        if not matched_any and self.event_router is not None:
                            await self.event_router.dispatch(
                                _build_arb_dispatcher_task_skipped_event(
                                    region=self.region,
                                    payload=effective_payload,
                                    user_id=user_id,
                                    skip_reason="threshold_not_matched",
                                )
                            )
                    self.last_id = message_id
                    processed += 1
            iteration += 1
        return processed


class RedisExecutionTaskConsumer(RedisSpotConsumer):
    processed_event_type = "executor.task.processed"
    processed_event_service = "executor"
    processed_event_message = "executor task processed"
    failed_event_type = "executor.task.failed"
    failed_event_service = "executor"
    failed_event_message = "executor task failed"

    def __init__(
        self,
        *,
        control_guard=None,
        task_repository=None,
        account_repository=None,
        account_truth_resolver=None,
        preflight_validator=None,
        risk_manager=None,
        repair_task_publisher=None,
        env_mode: str = "testnet",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.control_guard = control_guard
        self.task_repository = task_repository
        self.account_repository = account_repository
        self.account_truth_resolver = account_truth_resolver
        self.preflight_validator = preflight_validator or ExecutorPreflightValidator()
        self.risk_manager = risk_manager or RiskManager()
        self.repair_task_publisher = repair_task_publisher
        self.env_mode = env_mode

    def _resolve_execution_accounts(self, *, payload: dict) -> dict | None:
        if self.account_repository is None or self.account_truth_resolver is None:
            return None

        accounts = self.account_repository.list_enabled_accounts(
            user_id=int(payload["user_id"]),
            env_mode=self.env_mode,
        )
        resolved_accounts = list(accounts or [])
        if (
            payload.get("buy_account_id") is not None
            and payload.get("sell_account_id") is not None
        ):
            return self.account_truth_resolver.resolve_bound_accounts(
                accounts=resolved_accounts,
                user_id=str(payload["user_id"]),
                buy_account_id=str(payload["buy_account_id"]),
                sell_account_id=str(payload["sell_account_id"]),
                buy_exchange=str(payload["buy_exchange"]),
                sell_exchange=str(payload["sell_exchange"]),
                env_mode=self.env_mode,
                region=self.region,
            )
        return self.account_truth_resolver.resolve_accounts(
            accounts=resolved_accounts,
            user_id=str(payload["user_id"]),
            buy_exchange=str(payload["buy_exchange"]),
            sell_exchange=str(payload["sell_exchange"]),
            env_mode=self.env_mode,
            region=self.region,
        )

    async def run(
        self,
        *,
        credentials_by_exchange: dict | None = None,
        max_iterations: int | None = None,
    ) -> int:
        iteration = 0
        processed = 0
        while max_iterations is None or iteration < max_iterations:
            entries = await self.redis_client.xread(
                {self.stream_key: self.last_id},
                count=1,
                block=self.block_ms,
            )
            for _, messages in entries:
                for message_id, payload in messages:
                    try:
                        task_uuid = (
                            str(payload["task_uuid"])
                            if payload.get("task_uuid") is not None
                            else None
                        )
                        if task_uuid is not None and self.task_repository is not None:
                            try:
                                self.task_repository.mark_executing(
                                    task_uuid,
                                    worker_node_id=self.region,
                                )
                            except LookupError:
                                self.last_id = message_id
                                processed += 1
                                continue
                        effective_payload = payload
                        if self.control_guard is not None:
                            requested_notional = float(
                                payload.get("target_quote_amount", 15.0)
                            )
                            decision = await self.control_guard.evaluate(
                                user_id=str(payload["user_id"]),
                                symbol=str(payload["symbol"]),
                                exchange=str(payload["buy_exchange"]),
                                requested_notional=requested_notional,
                            )
                            if not decision.allowed:
                                if task_uuid is not None and self.task_repository is not None:
                                    self.task_repository.mark_blocked(
                                        task_uuid,
                                        reason=decision.reason or "blocked",
                                    )
                                self.last_id = message_id
                                processed += 1
                                if self.event_router is not None:
                                    await self.event_router.dispatch(
                                        _build_control_rule_event(
                                            event_type="control.rule.blocked",
                                            service="executor",
                                            region=self.region,
                                            symbol=payload.get("symbol"),
                                            exchange=payload.get("buy_exchange"),
                                            user_id=str(payload["user_id"]),
                                            source_message_id=str(
                                                payload.get("source_message_id")
                                            )
                                            if payload.get("source_message_id")
                                            is not None
                                            else None,
                                            requested_notional=requested_notional,
                                            approved_notional=decision.approved_notional,
                                            reason=decision.reason,
                                        )
                                    )
                                    await self.event_router.dispatch(
                                        self._build_processed_event(
                                            message_id=message_id,
                                            payload=payload,
                                        )
                                    )
                                continue
                            if 0 < decision.approved_notional < requested_notional:
                                if self.event_router is not None:
                                    await self.event_router.dispatch(
                                        _build_control_rule_event(
                                            event_type="control.rule.resized",
                                            service="executor",
                                            region=self.region,
                                            symbol=payload.get("symbol"),
                                            exchange=payload.get("buy_exchange"),
                                            user_id=str(payload["user_id"]),
                                            source_message_id=str(
                                                payload.get("source_message_id")
                                            )
                                            if payload.get("source_message_id")
                                            is not None
                                            else None,
                                            requested_notional=requested_notional,
                                            approved_notional=decision.approved_notional,
                                            reason=decision.reason,
                                        )
                                    )
                                effective_payload = dict(payload)
                                effective_payload["target_quote_amount"] = str(
                                    decision.approved_notional
                                )

                        execution_accounts_by_exchange = (
                            self._resolve_execution_accounts(payload=effective_payload)
                        )
                        self.preflight_validator.validate(
                            payload=effective_payload,
                            execution_accounts_by_exchange=execution_accounts_by_exchange,
                        )
                        dispatch_credentials_by_exchange = credentials_by_exchange
                        proxies_by_exchange = None
                        if execution_accounts_by_exchange is not None:
                            dispatch_credentials_by_exchange = {
                                exchange: (
                                    account["credentials"]
                                    if isinstance(account, dict)
                                    else account.credentials
                                )
                                for exchange, account in execution_accounts_by_exchange.items()
                            }
                            proxies_by_exchange = {
                                exchange: (
                                    account["proxies"]
                                    if isinstance(account, dict)
                                    else account.proxies
                                )
                                for exchange, account in execution_accounts_by_exchange.items()
                            }
                        if dispatch_credentials_by_exchange is None:
                            raise RuntimeError(
                                "credentials_by_exchange is required when account truth resolution is unavailable"
                            )

                        result = await self.dispatcher.dispatch(
                            effective_payload,
                            execution_accounts_by_exchange=execution_accounts_by_exchange,
                            credentials_by_exchange=dispatch_credentials_by_exchange,
                            proxies_by_exchange=proxies_by_exchange,
                        )
                        execution_status = getattr(result, "execution_status", None)
                        if execution_status is not None:
                            filled_exchanges = list(
                                getattr(result, "filled_exchanges", []) or []
                            )
                            failed_exchanges = list(
                                getattr(result, "failed_exchanges", []) or []
                            )
                            repair_plan = self.risk_manager.build_repair_plan(
                                ExecutionResult(
                                    status=execution_status,
                                    filled_exchanges=filled_exchanges,
                                    failed_exchanges=failed_exchanges,
                                )
                            )
                            target_exchanges = list(failed_exchanges)
                            if (
                                self.repair_task_publisher is not None
                                and execution_status == "OPEN_PARTIAL"
                                and failed_exchanges
                                and repair_plan.action != "NONE"
                            ):
                                await self.repair_task_publisher.publish(
                                    node_id=self.region,
                                    task_payload=build_repair_task_payload(
                                        effective_payload,
                                        execution_status=execution_status,
                                        failed_exchanges=failed_exchanges,
                                        repair_action=repair_plan.action,
                                        repair_reason=repair_plan.reason,
                                        target_exchanges=target_exchanges,
                                    ),
                                )
                            if (
                                self.event_router is not None
                                and execution_status == "OPEN_PARTIAL"
                                and failed_exchanges
                                and repair_plan.action != "NONE"
                            ):
                                await self.event_router.dispatch(
                                    _build_executor_repair_planned_event(
                                        region=self.region,
                                        payload=effective_payload,
                                        execution_status=execution_status,
                                        filled_exchanges=filled_exchanges,
                                        failed_exchanges=failed_exchanges,
                                        repair_plan=repair_plan,
                                    )
                                )
                            lifecycle_status = (
                                "SUCCEEDED"
                                if execution_status == "OPEN_HEDGED"
                                else "FAILED"
                            )
                            should_emit_failed_event = lifecycle_status == "FAILED"
                            if (
                                execution_status == "OPEN_PARTIAL"
                                and failed_exchanges
                                and repair_plan.action != "NONE"
                            ):
                                should_emit_failed_event = False
                            if execution_status == "SKIPPED":
                                should_emit_failed_event = False
                            if task_uuid is not None and self.task_repository is not None:
                                self.task_repository.mark_execution_result(
                                    task_uuid,
                                    lifecycle_status=lifecycle_status,
                                    execution_status=execution_status,
                                    filled_exchanges=filled_exchanges,
                                    failed_exchanges=failed_exchanges,
                                    repair_action=repair_plan.action,
                                    repair_reason=repair_plan.reason,
                                )
                            if self.event_router is not None:
                                await self.event_router.dispatch(
                                    _build_executor_execution_result_event(
                                        region=self.region,
                                        payload=effective_payload,
                                        result=result,
                                    )
                                )
                            self.last_id = message_id
                            processed += 1
                            if should_emit_failed_event:
                                if self.event_router is not None:
                                    await self.event_router.dispatch(
                                        self._build_failed_event(
                                            message_id=message_id,
                                            payload=payload,
                                            error=RuntimeError(execution_status),
                                        )
                                    )
                                continue
                            if self.event_router is not None:
                                await self.event_router.dispatch(
                                    self._build_processed_event(
                                        message_id=message_id,
                                        payload=effective_payload,
                                    )
                                )
                            continue
                        if task_uuid is not None and self.task_repository is not None:
                            self.task_repository.mark_succeeded(task_uuid)
                        self.last_id = message_id
                        processed += 1
                        if self.event_router is not None:
                            await self.event_router.dispatch(
                                self._build_processed_event(
                                    message_id=message_id,
                                    payload=effective_payload,
                                )
                            )
                    except Exception as exc:
                        if self.event_router is not None:
                            await self.event_router.dispatch(
                                self._build_failed_event(
                                    message_id=message_id,
                                    payload=payload,
                                    error=exc,
                                )
                            )
                        if task_uuid is not None and self.task_repository is not None:
                            failure_reason = getattr(exc, "reason", str(exc))
                            self.task_repository.mark_failed(
                                task_uuid,
                                reason=failure_reason,
                            )
            iteration += 1
        return processed


class RedisRepairTaskConsumer(RedisSpotConsumer):
    processed_event_type = "repair.task.processed"
    processed_event_service = "repair"
    processed_event_message = "repair task processed"
    failed_event_type = "repair.task.failed"
    failed_event_service = "repair"
    failed_event_message = "repair task failed"

    def __init__(
        self,
        *,
        repair_service,
        task_repository=None,
        env_mode: str = "testnet",
        **kwargs,
    ) -> None:
        super().__init__(dispatcher=repair_service, **kwargs)
        self.repair_service = repair_service
        self.task_repository = task_repository
        self.env_mode = env_mode

    async def run(
        self,
        *,
        credentials_by_exchange: dict | None = None,
        max_iterations: int | None = None,
    ) -> int:
        iteration = 0
        processed = 0
        while max_iterations is None or iteration < max_iterations:
            entries = await self.redis_client.xread(
                {self.stream_key: self.last_id},
                count=1,
                block=self.block_ms,
            )
            for _, messages in entries:
                for message_id, payload in messages:
                    try:
                        target_exchanges = [
                            item
                            for item in str(payload.get("target_exchanges", "")).split(",")
                            if item
                        ]
                        if (
                            str(payload.get("repair_action", "")) != "AUTO_HEDGE_REPAIRING"
                            or str(payload.get("execution_status", "")) != "OPEN_PARTIAL"
                            or not target_exchanges
                        ):
                            self.last_id = message_id
                            processed += 1
                            continue
                        result = await self.repair_service.run_task(
                            task_uuid=str(payload["task_uuid"]),
                            symbol=str(payload["symbol"]),
                            buy_exchange=str(payload["buy_exchange"]),
                            sell_exchange=str(payload["sell_exchange"]),
                            target_exchanges=target_exchanges,
                            credentials_by_exchange=credentials_by_exchange or {},
                            target_quote_amount=float(
                                payload.get("target_quote_amount", "15.0")
                            ),
                            env_mode=self.env_mode,
                            db_task_id=self._lookup_db_task_id(str(payload["task_uuid"])),
                        )
                        if self.task_repository is not None:
                            if result.ok:
                                self.task_repository.mark_repair_result(
                                    str(payload["task_uuid"]),
                                    lifecycle_status="SUCCEEDED",
                                    execution_status="OPEN_HEDGED",
                                    filled_exchanges=[
                                        str(payload["buy_exchange"]),
                                        str(payload["sell_exchange"]),
                                    ],
                                    failed_exchanges=[],
                                    repair_action=str(payload["repair_action"]),
                                    repair_reason="repair_succeeded",
                                    status_reason=None,
                                )
                            else:
                                remaining_failed_exchanges = list(
                                    getattr(result, "remaining_failed_exchanges", []) or []
                                )
                                self.task_repository.mark_repair_result(
                                    str(payload["task_uuid"]),
                                    lifecycle_status="FAILED",
                                    execution_status="OPEN_PARTIAL",
                                    filled_exchanges=[
                                        exchange
                                        for exchange in (
                                            str(payload["buy_exchange"]),
                                            str(payload["sell_exchange"]),
                                        )
                                        if exchange not in remaining_failed_exchanges
                                    ],
                                    failed_exchanges=remaining_failed_exchanges,
                                    repair_action=str(payload["repair_action"]),
                                    repair_reason=str(payload["repair_reason"]),
                                    status_reason="manual_required",
                                )
                        if self.event_router is not None:
                            await self.event_router.dispatch(
                                _build_repair_finished_event(
                                    region=self.region,
                                    payload=payload,
                                    result=result,
                                )
                            )
                            await self.event_router.dispatch(
                                self._build_processed_event(
                                    message_id=message_id,
                                    payload=payload,
                                )
                            )
                        self.last_id = message_id
                        processed += 1
                    except Exception as exc:
                        if self.event_router is not None:
                            await self.event_router.dispatch(
                                self._build_failed_event(
                                    message_id=message_id,
                                    payload=payload,
                                    error=exc,
                                )
                            )
            iteration += 1
        return processed

    def _lookup_db_task_id(self, task_uuid: str) -> int:
        if self.task_repository is None:
            return 0
        try:
            session = self.task_repository.session
            from models import ArbitrageTask
            task = session.query(ArbitrageTask).filter(
                ArbitrageTask.task_uuid == task_uuid
            ).first()
            return int(task.id) if task else 0
        except Exception:
            return 0


class RedisNodeTaskDispatcher:
    def __init__(
        self,
        *,
        redis_client,
        user_ids: list[str],
        dispatch_user_repository=None,
        account_repository=None,
        route_resolver,
        task_publisher,
        stream_key: str,
        strategy_repository=None,
        control_guard=None,
        task_repository=None,
        block_ms: int = 1000,
        event_router=None,
        region: str = "default",
        env_mode: str = "testnet",
    ) -> None:
        self.redis_client = redis_client
        self.user_ids = user_ids
        self.dispatch_user_repository = dispatch_user_repository
        self.account_repository = account_repository
        self.route_resolver = route_resolver
        self.task_publisher = task_publisher
        self.stream_key = stream_key
        self.strategy_repository = strategy_repository
        self.control_guard = control_guard
        self.task_repository = task_repository
        self.block_ms = block_ms
        self.event_router = event_router
        self.region = region
        self.env_mode = env_mode
        self.last_id = "0-0"

    def _resolve_candidate_user_ids(self) -> list[str]:
        if self.dispatch_user_repository is None:
            return list(self.user_ids)

        discovered_user_ids = self.dispatch_user_repository.list_dispatchable_user_ids(
            env_mode=self.env_mode
        )
        if not self.user_ids:
            return discovered_user_ids

        allowed_user_ids = set(discovered_user_ids)
        return [user_id for user_id in self.user_ids if user_id in allowed_user_ids]

    def _iter_matching_strategies(self, *, user_id: str, payload: dict) -> list[Any]:
        if self.strategy_repository is None:
            return [None]
        strategies = self.strategy_repository.list_enabled_for_user(user_id=int(user_id))
        return [strategy for strategy in strategies if _matches_strategy(payload, strategy)]

    def _load_user_accounts(self, *, user_id: str) -> list[Any] | None:
        if self.account_repository is None:
            return None
        accounts = self.account_repository.list_enabled_accounts(
            user_id=int(user_id),
            env_mode=self.env_mode,
        )
        return list(accounts or [])

    def _create_database_task(
        self,
        *,
        user_id: str,
        message_id: str,
        payload: dict,
        strategy,
        requested_notional: float,
        buy_account_id: int | None = None,
        sell_account_id: int | None = None,
    ):
        if self.task_repository is None:
            return None
        strategy_id = None if strategy is None else int(strategy.id)
        idempotency_key = f"{user_id}:{message_id}:open"
        if strategy_id is not None:
            idempotency_key = f"{idempotency_key}:{strategy_id}"
        return self.task_repository.create_task(
            ArbitrageTaskCreate(
                task_uuid=uuid4().hex,
                user_id=int(user_id),
                strategy_config_id=strategy_id,
                opportunity_id=message_id,
                env_mode=self.env_mode,
                task_type="open",
                symbol=str(payload["symbol"]),
                spot_exchange=str(payload["buy_exchange"]),
                derivative_exchange=str(payload["sell_exchange"]),
                target_notional=requested_notional,
                expected_spread_bps=float(payload.get("spread_bps", 0.0)),
                expected_funding_bps=0.0,
                idempotency_key=idempotency_key,
                home_region=self.region,
                buy_account_id=buy_account_id,
                sell_account_id=sell_account_id,
            )
        )

    async def run(self, *, max_iterations: int | None = None) -> int:
        iteration = 0
        processed = 0
        while max_iterations is None or iteration < max_iterations:
            entries = await self.redis_client.xread(
                {self.stream_key: self.last_id},
                count=1,
                block=self.block_ms,
            )
            for _, messages in entries:
                for message_id, payload in messages:
                    candidate_user_ids = self._resolve_candidate_user_ids()
                    if self.event_router is not None:
                        await self.event_router.dispatch(
                            _build_dispatch_user_event(
                                event_type="dispatcher.user.discovery.succeeded",
                                region=self.region,
                                payload={"candidate_user_ids": candidate_user_ids},
                            )
                        )
                    for user_id in candidate_user_ids:
                        node_id = await self.route_resolver.get_user_node(user_id)
                        if node_id is None:
                            if self.event_router is not None:
                                await self.event_router.dispatch(
                                    _build_dispatch_user_event(
                                        event_type="dispatcher.user.skipped",
                                        region=self.region,
                                        payload={
                                            "user_id": user_id,
                                            "reason": "user_route_missing",
                                        },
                                    )
                                )
                            continue
                        accounts = self._load_user_accounts(user_id=user_id)
                        binding = None
                        if accounts is not None:
                            coverage = _evaluate_account_exchange_coverage(
                                payload=payload,
                                accounts=accounts,
                                dispatcher_region=self.region,
                            )
                            if not coverage["has_exchange_coverage"]:
                                if self.event_router is not None:
                                    await self.event_router.dispatch(
                                        _build_dispatch_user_event(
                                            event_type="dispatcher.user.skipped",
                                            region=self.region,
                                            payload={
                                                "user_id": user_id,
                                                "reason": "account_exchange_coverage_missing",
                                                "buy_exchange": str(payload["buy_exchange"]),
                                                "sell_exchange": str(payload["sell_exchange"]),
                                                "available_exchanges": coverage[
                                                    "available_exchanges"
                                                ],
                                                "auto_trade_enabled_exchanges": coverage[
                                                    "auto_trade_enabled_exchanges"
                                                ],
                                            },
                                        )
                                    )
                                continue
                            if not coverage["has_auto_trade_coverage"]:
                                if self.event_router is not None:
                                    await self.event_router.dispatch(
                                        _build_dispatch_user_event(
                                            event_type="dispatcher.user.skipped",
                                            region=self.region,
                                            payload={
                                                "user_id": user_id,
                                                "reason": "account_auto_trade_disabled",
                                                "buy_exchange": str(payload["buy_exchange"]),
                                                "sell_exchange": str(payload["sell_exchange"]),
                                                "available_exchanges": coverage[
                                                    "available_exchanges"
                                                ],
                                                "auto_trade_enabled_exchanges": coverage[
                                                    "auto_trade_enabled_exchanges"
                                                ],
                                            },
                                        )
                                    )
                                continue
                            if not coverage["has_market_type_coverage"]:
                                if self.event_router is not None:
                                    await self.event_router.dispatch(
                                        _build_dispatch_user_event(
                                            event_type="dispatcher.user.skipped",
                                            region=self.region,
                                            payload={
                                                "user_id": user_id,
                                                "reason": "account_market_type_scope_missing",
                                                "buy_exchange": str(payload["buy_exchange"]),
                                                "sell_exchange": str(payload["sell_exchange"]),
                                                "available_exchanges": coverage[
                                                    "available_exchanges"
                                                ],
                                                "auto_trade_enabled_exchanges": coverage[
                                                    "auto_trade_enabled_exchanges"
                                                ],
                                                "market_type_scopes_by_exchange": coverage[
                                                    "market_type_scopes_by_exchange"
                                                ],
                                                "allowed_market_types": coverage[
                                                    "allowed_market_types"
                                                ],
                                            },
                                        )
                                    )
                                continue
                            if not coverage["has_region_coverage"]:
                                if self.event_router is not None:
                                    await self.event_router.dispatch(
                                        _build_dispatch_user_event(
                                            event_type="dispatcher.user.skipped",
                                            region=self.region,
                                            payload={
                                                "user_id": user_id,
                                                "reason": "account_region_mismatch",
                                                "buy_exchange": str(payload["buy_exchange"]),
                                                "sell_exchange": str(payload["sell_exchange"]),
                                                "available_exchanges": coverage[
                                                    "available_exchanges"
                                                ],
                                                "auto_trade_enabled_exchanges": coverage[
                                                    "auto_trade_enabled_exchanges"
                                                ],
                                                "market_type_scopes_by_exchange": coverage[
                                                    "market_type_scopes_by_exchange"
                                                ],
                                                "dispatcher_region": coverage[
                                                    "dispatcher_region"
                                                ],
                                                "account_regions_by_exchange": coverage[
                                                    "account_regions_by_exchange"
                                                ],
                                            },
                                        )
                                    )
                                continue
                            binding = _select_bound_accounts(
                                payload=payload,
                                accounts=accounts,
                                dispatcher_region=self.region,
                            )
                            if binding is None:
                                continue
                        strategies = self._iter_matching_strategies(
                            user_id=user_id,
                            payload=payload,
                        )
                        for strategy in strategies:
                            requested_notional = (
                                float(strategy.target_quote_amount)
                                if strategy is not None
                                else float(payload.get("target_quote_amount", 15.0))
                            )
                            task_record = self._create_database_task(
                                user_id=user_id,
                                message_id=message_id,
                                payload=payload,
                                strategy=strategy,
                                requested_notional=requested_notional,
                                buy_account_id=(
                                    int(getattr(binding["buy_account"], "id"))
                                    if accounts is not None
                                    else None
                                ),
                                sell_account_id=(
                                    int(getattr(binding["sell_account"], "id"))
                                    if accounts is not None
                                    else None
                                ),
                            )
                            strategy_id = None if strategy is None else int(strategy.id)
                            decision = None
                            if self.control_guard is not None:
                                decision = await self.control_guard.evaluate(
                                    user_id=user_id,
                                    symbol=str(payload["symbol"]),
                                    exchange=str(payload["buy_exchange"]),
                                    requested_notional=requested_notional,
                                    strategy_id=strategy_id,
                                )
                            if decision is not None and not decision.allowed:
                                if self.event_router is not None:
                                    await self.event_router.dispatch(
                                        _build_control_rule_event(
                                            event_type="control.rule.blocked",
                                            service="dispatcher",
                                            region=self.region,
                                            symbol=payload.get("symbol"),
                                            exchange=payload.get("buy_exchange"),
                                            user_id=user_id,
                                            source_message_id=message_id,
                                            requested_notional=requested_notional,
                                            approved_notional=decision.approved_notional,
                                            reason=decision.reason,
                                        )
                                    )
                                if task_record is not None:
                                    self.task_repository.mark_blocked(
                                        task_record.task_uuid,
                                        reason=decision.reason or "blocked",
                                    )
                                continue
                            task_uuid = (
                                task_record.task_uuid
                                if task_record is not None
                                else uuid4().hex
                            )
                            task_payload = build_node_execution_task_payload(
                                payload,
                                user_id=user_id,
                                source_message_id=message_id,
                                task_uuid=task_uuid,
                                strategy_config_id=(
                                    str(strategy_id) if strategy_id is not None else None
                                ),
                                buy_account_id=(
                                    str(int(getattr(binding["buy_account"], "id")))
                                    if accounts is not None
                                    else None
                                ),
                                sell_account_id=(
                                    str(int(getattr(binding["sell_account"], "id")))
                                    if accounts is not None
                                    else None
                                ),
                            )
                            if strategy_id is not None:
                                task_payload["target_quote_amount"] = str(
                                    requested_notional
                                )
                            if (
                                decision is not None
                                and 0 < decision.approved_notional < requested_notional
                            ):
                                if self.event_router is not None:
                                    await self.event_router.dispatch(
                                        _build_control_rule_event(
                                            event_type="control.rule.resized",
                                            service="dispatcher",
                                            region=self.region,
                                            symbol=payload.get("symbol"),
                                            exchange=payload.get("buy_exchange"),
                                            user_id=user_id,
                                            source_message_id=message_id,
                                            requested_notional=requested_notional,
                                            approved_notional=decision.approved_notional,
                                            reason=decision.reason,
                                        )
                                    )
                                task_payload["target_quote_amount"] = str(
                                    decision.approved_notional
                                )
                            await self.task_publisher.publish(
                                node_id=node_id,
                                task_payload=task_payload,
                            )
                            if task_record is not None:
                                self.task_repository.mark_dispatched(
                                    task_uuid,
                                    worker_node_id=node_id,
                                )
                    self.last_id = message_id
                    processed += 1
            iteration += 1
        return processed
