from app.market.opportunity import (
    ArbitrageOpportunity,
    SpotOpportunity,
    arbitrage_opportunity_to_payload,
)


class MarketOpportunityPublisher:
    def __init__(self, redis_client, *, zset_key: str, stream_key: str) -> None:
        self.redis_client = redis_client
        self.zset_key = zset_key
        self.stream_key = stream_key

    async def publish(self, opportunity: SpotOpportunity) -> None:
        await self.redis_client.zadd(
            self.zset_key,
            {opportunity.redis_member: opportunity.spread_bps},
        )
        await self.redis_client.xadd(
            self.stream_key,
            {
                "symbol": opportunity.symbol,
                "buy_exchange": opportunity.buy_exchange,
                "sell_exchange": opportunity.sell_exchange,
                "buy_ask": str(opportunity.buy_ask),
                "sell_bid": str(opportunity.sell_bid),
                "spread_bps": str(opportunity.spread_bps),
                "redis_member": opportunity.redis_member,
                "timestamp": str(opportunity.timestamp),
                "effective_buy_price": str(opportunity.effective_buy_price),
                "effective_sell_price": str(opportunity.effective_sell_price),
                "target_quote_amount": str(opportunity.target_quote_amount),
                "buy_depth_levels_used": str(opportunity.buy_depth_levels_used),
                "sell_depth_levels_used": str(opportunity.sell_depth_levels_used),
            },
        )


class ArbitrageOpportunityPublisher:
    OPEN_ZSET_KEY = "arb:zset:open"
    CLOSE_ZSET_KEY = "arb:zset:close"
    STREAM_KEY = "stream:opportunities"

    def __init__(self, redis_client) -> None:
        self.redis_client = redis_client

    async def publish(self, opportunity: ArbitrageOpportunity) -> None:
        zset_key, score = self._zset_target(opportunity)
        await self.redis_client.zadd(
            zset_key,
            {opportunity.redis_member: score},
        )
        payload = {
            key: str(value)
            for key, value in arbitrage_opportunity_to_payload(opportunity).items()
        }
        await self.redis_client.xadd(
            self.STREAM_KEY,
            payload,
        )

    def _zset_target(self, opportunity: ArbitrageOpportunity) -> tuple[str, float]:
        if opportunity.opportunity_type == "OPEN":
            return self.OPEN_ZSET_KEY, opportunity.open_spread_bps
        if opportunity.opportunity_type == "CLOSE":
            return self.CLOSE_ZSET_KEY, opportunity.close_spread_bps
        raise ValueError(f"unsupported opportunity_type: {opportunity.opportunity_type}")


class UserNodeRouteStore:
    ROUTE_INDEX_KEY = "route:user_node:index"
    ROUTE_KEY_PREFIX = "route:user_node:"

    def __init__(self, redis_client) -> None:
        self.redis_client = redis_client

    @staticmethod
    def route_key(user_id: str) -> str:
        return f"route:user_node:{user_id}"

    async def get_user_node(self, user_id: str) -> str | None:
        return await self.redis_client.get(self.route_key(user_id))

    async def set_user_node(self, user_id: str, node_id: str) -> bool:
        await self.redis_client.set(self.route_key(user_id), node_id)
        await self.redis_client.sadd(self.ROUTE_INDEX_KEY, user_id)
        return True

    async def delete_user_node(self, user_id: str) -> int:
        await self.redis_client.srem(self.ROUTE_INDEX_KEY, user_id)
        return await self.redis_client.delete(self.route_key(user_id))

    async def list_routes(self) -> dict[str, str]:
        routes: dict[str, str] = {}
        for user_id in sorted(await self.redis_client.smembers(self.ROUTE_INDEX_KEY)):
            node_id = await self.get_user_node(str(user_id))
            if node_id is not None:
                routes[str(user_id)] = node_id
        return routes

    async def sync_routes(self, routes: dict[str, str]) -> int:
        synced = 0
        for user_id, node_id in routes.items():
            await self.set_user_node(user_id, node_id)
            synced += 1
        return synced

    async def sync_default_routes(self, routes: dict[str, str]) -> int:
        synced = 0
        for user_id, node_id in routes.items():
            if await self.get_user_node(user_id) is None:
                await self.set_user_node(user_id, node_id)
                synced += 1
        return synced

    async def iter_route_user_ids(self):
        cursor = 0
        while True:
            cursor, keys = await self.redis_client.scan(
                cursor=cursor,
                match=f"{self.ROUTE_KEY_PREFIX}*",
                count=100,
            )
            for key in keys:
                key_text = str(key)
                if key_text == self.ROUTE_INDEX_KEY:
                    continue
                if not key_text.startswith(self.ROUTE_KEY_PREFIX):
                    continue
                user_id = key_text[len(self.ROUTE_KEY_PREFIX) :].strip()
                if user_id:
                    yield user_id
            if cursor == 0:
                break

    async def backfill_route_index(self, dry_run: bool = False) -> dict[str, object]:
        found = 0
        newly_indexed = 0
        already_indexed = 0
        skipped = 0
        current_index = {
            str(user_id) for user_id in await self.redis_client.smembers(self.ROUTE_INDEX_KEY)
        }
        async for user_id in self.iter_route_user_ids():
            node_id = await self.get_user_node(user_id)
            if not node_id:
                skipped += 1
                continue

            found += 1
            if user_id in current_index:
                already_indexed += 1
                continue

            if not dry_run:
                await self.redis_client.sadd(self.ROUTE_INDEX_KEY, user_id)
                current_index.add(user_id)
            newly_indexed += 1
        return {
            "found": found,
            "newly_indexed": newly_indexed,
            "already_indexed": already_indexed,
            "skipped": skipped,
            "dry_run": dry_run,
        }


class UserNodeRouter(UserNodeRouteStore):
    pass


class NodeExecutionTaskPublisher:
    def __init__(self, redis_client) -> None:
        self.redis_client = redis_client

    async def publish(self, *, node_id: str, task_payload: dict[str, str]) -> str:
        return await self.redis_client.xadd(
            f"stream:spot_exec_tasks:{node_id}",
            task_payload,
        )


class RepairTaskPublisher:
    def __init__(self, redis_client) -> None:
        self.redis_client = redis_client

    async def publish(self, *, node_id: str, task_payload: dict[str, str]) -> str:
        return await self.redis_client.xadd(
            f"stream:repair_tasks:{node_id}",
            task_payload,
        )


def build_node_execution_task_payload(
    payload: dict,
    *,
    user_id: str,
    source_message_id: str,
    task_uuid: str,
    strategy_config_id: str | None = None,
    buy_account_id: str | None = None,
    sell_account_id: str | None = None,
) -> dict[str, str]:
    task_payload = {key: str(value) for key, value in payload.items()}
    task_payload["user_id"] = user_id
    task_payload["source_message_id"] = source_message_id
    task_payload["task_uuid"] = task_uuid
    if strategy_config_id is not None:
        task_payload["strategy_config_id"] = strategy_config_id
    if buy_account_id is not None:
        task_payload["buy_account_id"] = buy_account_id
    if sell_account_id is not None:
        task_payload["sell_account_id"] = sell_account_id
    return task_payload


def build_repair_task_payload(
    payload: dict[str, object],
    *,
    execution_status: str,
    failed_exchanges: list[str],
    repair_action: str,
    repair_reason: str,
    target_exchanges: list[str],
) -> dict[str, str]:
    return {
        "task_uuid": str(payload["task_uuid"]),
        "user_id": str(payload["user_id"]),
        "symbol": str(payload["symbol"]),
        "buy_exchange": str(payload["buy_exchange"]),
        "sell_exchange": str(payload["sell_exchange"]),
        "execution_status": execution_status,
        "failed_exchanges": ",".join(failed_exchanges),
        "repair_action": repair_action,
        "repair_reason": repair_reason,
        "target_exchanges": ",".join(target_exchanges),
        "target_quote_amount": str(payload.get("target_quote_amount", "15.0")),
    }


class RedisOpportunityDispatcher:
    def __init__(self, spot_service) -> None:
        self.spot_service = spot_service

    async def dispatch(
        self,
        payload: dict,
        *,
        execution_accounts_by_exchange: dict | None = None,
        credentials_by_exchange: dict,
        proxies_by_exchange: dict | None = None,
    ) -> object:
        exchanges = [payload["buy_exchange"], payload["sell_exchange"]]
        _ = execution_accounts_by_exchange
        target_quote_amount = float(payload.get("target_quote_amount", 15.0))
        return await self.spot_service.run_task(
            exchanges=exchanges,
            buy_exchange=payload["buy_exchange"],
            sell_exchange=payload["sell_exchange"],
            credentials_by_exchange=credentials_by_exchange,
            symbol=payload["symbol"],
            target_quote_amount=target_quote_amount,
            env_mode="testnet",
            proxies_by_exchange=proxies_by_exchange,
        )
