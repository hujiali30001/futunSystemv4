import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.admin.control_plane import build_control_plane
from app.admin.control_store import ControlPlaneStore
from app.db.task_repository import ArbitrageTaskCreate
from app.runtime.redis_flow import build_node_execution_task_payload
from app.runtime.runtime_events import RuntimeEvent
from app.trading.executor import ExecutionResult
from app.trading.risk_manager import RiskManager


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
        if buy_exchange == sell_exchange:
            raise ExecutorPreflightError(
                "executor_preflight_same_exchange",
                "buy_exchange and sell_exchange must differ",
            )

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
    ) -> None:
        active_symbols = symbols or ([symbol] if symbol is not None else [])
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
                            self.task_repository.mark_executing(
                                task_uuid,
                                worker_node_id=self.region,
                            )
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
                        if (
                            task_uuid is not None
                            and self.task_repository is not None
                            and execution_status is not None
                        ):
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
                            lifecycle_status = (
                                "SUCCEEDED"
                                if execution_status == "OPEN_HEDGED"
                                else "FAILED"
                            )
                            self.task_repository.mark_execution_result(
                                task_uuid,
                                lifecycle_status=lifecycle_status,
                                execution_status=execution_status,
                                filled_exchanges=filled_exchanges,
                                failed_exchanges=failed_exchanges,
                                repair_action=repair_plan.action,
                                repair_reason=repair_plan.reason,
                            )
                            self.last_id = message_id
                            processed += 1
                            if lifecycle_status == "FAILED":
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
