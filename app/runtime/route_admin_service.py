import json

from aiohttp import ContentTypeError, web
from redis.asyncio import Redis

from app.runtime.redis_flow import UserNodeRouteStore
from app.runtime.runtime_events import RuntimeEvent
from app.runtime.worker_config import (
    WorkerSettings,
    get_alert_settings,
    get_worker_settings,
)
from app.runtime.worker_service import build_event_router


def default_redis_factory(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True)


def _check_bearer(request: web.Request, token: str) -> bool:
    auth = request.headers.get("Authorization", "")
    return bool(token) and auth == f"Bearer {token}"


def _error_response(message: str, *, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _validate_user_id(user_id: str) -> str | None:
    value = str(user_id).strip()
    return value or None


async def _emit_event(event_router, event: RuntimeEvent) -> None:
    if event_router is None:
        return
    try:
        await event_router.dispatch(event)
    except Exception:
        return


async def _emit_unauthorized_event(event_router, request: web.Request) -> None:
    await _emit_event(
        event_router,
        RuntimeEvent(
            event_type="route.admin.unauthorized",
            level="WARNING",
            service="route-admin",
            region="main",
            message="route admin request unauthorized",
            payload={"path": request.path, "source": "http_api"},
        ),
    )


def build_route_admin_app(
    *, route_store, admin_token: str, event_router=None
) -> web.Application:
    app = web.Application()

    async def healthz(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def list_routes(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            await _emit_unauthorized_event(event_router, request)
            return _error_response("unauthorized", status=401)
        try:
            return web.json_response({"routes": await route_store.list_routes()})
        except Exception:
            return _error_response("redis unavailable", status=503)

    async def get_route(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            await _emit_unauthorized_event(event_router, request)
            return _error_response("unauthorized", status=401)
        user_id = _validate_user_id(request.match_info.get("user_id", ""))
        if user_id is None:
            return _error_response("user_id required", status=400)
        try:
            node_id = await route_store.get_user_node(user_id)
        except Exception:
            return _error_response("redis unavailable", status=503)
        if node_id is None:
            return _error_response("not found", status=404)
        return web.json_response({"user_id": user_id, "node_id": node_id})

    async def put_route(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            await _emit_unauthorized_event(event_router, request)
            return _error_response("unauthorized", status=401)
        user_id = _validate_user_id(request.match_info.get("user_id", ""))
        if user_id is None:
            return _error_response("user_id required", status=400)
        try:
            body = await request.json()
        except (ContentTypeError, json.JSONDecodeError):
            return _error_response("invalid json", status=400)
        node_id = str(body.get("node_id", "")).strip()
        if not node_id:
            return _error_response("node_id required", status=400)
        try:
            await route_store.set_user_node(user_id, node_id)
        except Exception:
            return _error_response("redis unavailable", status=503)
        await _emit_event(
            event_router,
            RuntimeEvent(
                event_type="route.admin.updated",
                level="INFO",
                service="route-admin",
                region="main",
                message="route updated",
                payload={"user_id": user_id, "node_id": node_id, "source": "http_api"},
            ),
        )
        return web.json_response({"ok": True, "user_id": user_id, "node_id": node_id})

    async def delete_route(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            await _emit_unauthorized_event(event_router, request)
            return _error_response("unauthorized", status=401)
        user_id = _validate_user_id(request.match_info.get("user_id", ""))
        if user_id is None:
            return _error_response("user_id required", status=400)
        try:
            await route_store.delete_user_node(user_id)
        except Exception:
            return _error_response("redis unavailable", status=503)
        await _emit_event(
            event_router,
            RuntimeEvent(
                event_type="route.admin.deleted",
                level="INFO",
                service="route-admin",
                region="main",
                message="route deleted",
                payload={"user_id": user_id, "node_id": None, "source": "http_api"},
            ),
        )
        return web.json_response({"ok": True, "user_id": user_id})

    app.router.add_get("/healthz", healthz)
    app.router.add_get("/routes", list_routes)
    app.router.add_get("/routes/{user_id}", get_route)
    app.router.add_put("/routes/{user_id}", put_route)
    app.router.add_delete("/routes/{user_id}", delete_route)
    return app


def build_runtime_route_admin_app(
    settings: WorkerSettings,
    *,
    redis_factory=default_redis_factory,
    event_router=None,
) -> web.Application:
    redis_client = redis_factory(settings.redis_url)
    app = build_route_admin_app(
        route_store=UserNodeRouteStore(redis_client),
        admin_token=settings.route_admin_token,
        event_router=event_router,
    )

    async def close_redis(_app: web.Application) -> None:
        await redis_client.aclose()

    app.on_cleanup.append(close_redis)
    return app


def main() -> None:
    settings = get_worker_settings()
    if not settings.route_admin_enabled:
        raise RuntimeError("route admin service is disabled")
    if not settings.route_admin_token:
        raise RuntimeError("ROUTE_ADMIN_TOKEN is required")
    app = build_runtime_route_admin_app(
        settings,
        event_router=build_event_router(get_alert_settings()),
    )
    web.run_app(
        app,
        host=settings.route_admin_bind_host,
        port=settings.route_admin_port,
    )


if __name__ == "__main__":
    main()
