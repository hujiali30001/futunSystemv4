import argparse
import asyncio
import json

from redis.asyncio import Redis

from app.runtime.redis_flow import UserNodeRouteStore
from app.runtime.worker_config import get_worker_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    backfill_parser = subparsers.add_parser("backfill-index")
    backfill_parser.add_argument("--dry-run", action="store_true")
    return parser


async def run_backfill_index(route_store: UserNodeRouteStore, *, dry_run: bool) -> int:
    result = await route_store.backfill_route_index(dry_run=dry_run)
    result["ok"] = True
    print(json.dumps(result, ensure_ascii=False))
    return 0


def build_redis_client(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True)


async def _run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_worker_settings()
    redis_client = build_redis_client(settings.redis_url)
    try:
        route_store = UserNodeRouteStore(redis_client)
        if args.command == "backfill-index":
            return await run_backfill_index(route_store, dry_run=args.dry_run)
    finally:
        await redis_client.aclose()
    return 1


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(asyncio.run(_run(argv)))


if __name__ == "__main__":
    main()
