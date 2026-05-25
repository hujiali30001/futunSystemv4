import json
from dataclasses import asdict

from aiohttp import ContentTypeError, web
from redis.asyncio import Redis

from app.admin.control_store import (
    AnnouncementRecord,
    ControlPlaneStore,
    LimitRuleRecord,
    PlatformSwitchRecord,
)
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
            event_type="control.admin.unauthorized",
            level="WARNING",
            service="control-admin",
            region="main",
            message="control admin request unauthorized",
            payload={"path": request.path, "source": "http_api"},
        ),
    )


async def _load_json(request: web.Request) -> dict:
    try:
        body = await request.json()
    except (ContentTypeError, json.JSONDecodeError):
        raise ValueError("invalid json") from None
    if not isinstance(body, dict):
        raise ValueError("json body must be an object")
    return body


def _required_text(body: dict, key: str) -> str:
    value = str(body.get(key, "")).strip()
    if not value:
        raise ValueError(f"{key} required")
    return value


def _parse_switch_id(switch_id: str) -> tuple[str, str, str]:
    parts = str(switch_id).split(":", 2)
    if len(parts) != 3 or not all(part.strip() for part in parts):
        raise ValueError("switch_id must use switch_key:scope_type:scope_id")
    return parts[0].strip(), parts[1].strip(), parts[2].strip()


def build_control_admin_app(
    *, store: ControlPlaneStore, admin_token: str, event_router=None
) -> web.Application:
    app = web.Application()

    async def healthz(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def list_limits(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            await _emit_unauthorized_event(event_router, request)
            return _error_response("unauthorized", status=401)
        try:
            limits = [asdict(rule) for rule in await store.list_limit_rules()]
        except Exception:
            return _error_response("redis unavailable", status=503)
        return web.json_response({"limits": limits})

    async def get_limit(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            await _emit_unauthorized_event(event_router, request)
            return _error_response("unauthorized", status=401)
        rule_id = str(request.match_info.get("rule_id", "")).strip()
        if not rule_id:
            return _error_response("rule_id required", status=400)
        try:
            limits = await store.list_limit_rules()
        except Exception:
            return _error_response("redis unavailable", status=503)
        for rule in limits:
            if rule.rule_id == rule_id:
                return web.json_response({"limit": asdict(rule)})
        return _error_response("not found", status=404)

    async def put_limit(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            await _emit_unauthorized_event(event_router, request)
            return _error_response("unauthorized", status=401)
        rule_id = str(request.match_info.get("rule_id", "")).strip()
        if not rule_id:
            return _error_response("rule_id required", status=400)
        try:
            body = await _load_json(request)
            record = LimitRuleRecord(
                rule_id=rule_id,
                scope_type=_required_text(body, "scope_type"),
                scope_id=_required_text(body, "scope_id"),
                limit_type=_required_text(body, "limit_type"),
                limit_value=float(body["limit_value"]),
                enabled=bool(body.get("enabled", True)),
                priority=int(body.get("priority", 0)),
                symbol=body.get("symbol"),
                exchange=body.get("exchange"),
                strategy_id=body.get("strategy_id"),
            )
        except KeyError:
            return _error_response("limit_value required", status=400)
        except ValueError as exc:
            return _error_response(str(exc), status=400)
        try:
            await store.put_limit_rule(record)
        except Exception:
            return _error_response("redis unavailable", status=503)
        await _emit_event(
            event_router,
            RuntimeEvent(
                event_type="control.admin.limit.updated",
                level="INFO",
                service="control-admin",
                region="main",
                message="control limit updated",
                payload={"rule_id": rule_id, "source": "http_api"},
            ),
        )
        return web.json_response({"ok": True, "limit": asdict(record)})

    async def delete_limit(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            await _emit_unauthorized_event(event_router, request)
            return _error_response("unauthorized", status=401)
        rule_id = str(request.match_info.get("rule_id", "")).strip()
        if not rule_id:
            return _error_response("rule_id required", status=400)
        try:
            await store.delete_limit_rule(rule_id)
        except Exception:
            return _error_response("redis unavailable", status=503)
        await _emit_event(
            event_router,
            RuntimeEvent(
                event_type="control.admin.limit.deleted",
                level="INFO",
                service="control-admin",
                region="main",
                message="control limit deleted",
                payload={"rule_id": rule_id, "source": "http_api"},
            ),
        )
        return web.json_response({"ok": True, "rule_id": rule_id})

    async def list_switches(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            await _emit_unauthorized_event(event_router, request)
            return _error_response("unauthorized", status=401)
        try:
            switches = [asdict(record) for record in await store.list_switches()]
        except Exception:
            return _error_response("redis unavailable", status=503)
        return web.json_response({"switches": switches})

    async def get_switch(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            await _emit_unauthorized_event(event_router, request)
            return _error_response("unauthorized", status=401)
        switch_id = str(request.match_info.get("switch_id", "")).strip()
        if not switch_id:
            return _error_response("switch_id required", status=400)
        try:
            switches = await store.list_switches()
        except Exception:
            return _error_response("redis unavailable", status=503)
        for switch in switches:
            current_id = (
                f"{switch.switch_key}:{switch.scope_type}:{switch.scope_id}"
            )
            if current_id == switch_id:
                return web.json_response({"switch": asdict(switch)})
        return _error_response("not found", status=404)

    async def put_switch(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            await _emit_unauthorized_event(event_router, request)
            return _error_response("unauthorized", status=401)
        switch_id = str(request.match_info.get("switch_id", "")).strip()
        if not switch_id:
            return _error_response("switch_id required", status=400)
        try:
            body = await _load_json(request)
            switch_key, scope_type, scope_id = _parse_switch_id(switch_id)
            record = PlatformSwitchRecord(
                switch_key=switch_key,
                scope_type=scope_type,
                scope_id=scope_id,
                enabled=bool(body.get("enabled", True)),
            )
        except ValueError as exc:
            return _error_response(str(exc), status=400)
        try:
            await store.put_switch(record)
        except Exception:
            return _error_response("redis unavailable", status=503)
        await _emit_event(
            event_router,
            RuntimeEvent(
                event_type="control.admin.switch.updated",
                level="INFO",
                service="control-admin",
                region="main",
                message="control switch updated",
                payload={"switch_id": switch_id, "source": "http_api"},
            ),
        )
        return web.json_response({"ok": True, "switch": asdict(record)})

    async def delete_switch(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            await _emit_unauthorized_event(event_router, request)
            return _error_response("unauthorized", status=401)
        switch_id = str(request.match_info.get("switch_id", "")).strip()
        if not switch_id:
            return _error_response("switch_id required", status=400)
        try:
            switch_key, scope_type, scope_id = _parse_switch_id(switch_id)
            await store.delete_switch(
                switch_key,
                scope_type=scope_type,
                scope_id=scope_id,
            )
        except ValueError as exc:
            return _error_response(str(exc), status=400)
        except Exception:
            return _error_response("redis unavailable", status=503)
        await _emit_event(
            event_router,
            RuntimeEvent(
                event_type="control.admin.switch.deleted",
                level="INFO",
                service="control-admin",
                region="main",
                message="control switch deleted",
                payload={"switch_id": switch_id, "source": "http_api"},
            ),
        )
        return web.json_response({"ok": True, "switch_id": switch_id})

    async def list_announcements(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            await _emit_unauthorized_event(event_router, request)
            return _error_response("unauthorized", status=401)
        try:
            announcements = [
                asdict(record) for record in await store.list_announcements()
            ]
        except Exception:
            return _error_response("redis unavailable", status=503)
        return web.json_response({"announcements": announcements})

    async def get_announcement(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            await _emit_unauthorized_event(event_router, request)
            return _error_response("unauthorized", status=401)
        announcement_id = str(request.match_info.get("announcement_id", "")).strip()
        if not announcement_id:
            return _error_response("announcement_id required", status=400)
        try:
            announcements = await store.list_announcements()
        except Exception:
            return _error_response("redis unavailable", status=503)
        for announcement in announcements:
            if announcement.announcement_id == announcement_id:
                return web.json_response({"announcement": asdict(announcement)})
        return _error_response("not found", status=404)

    async def create_announcement(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            await _emit_unauthorized_event(event_router, request)
            return _error_response("unauthorized", status=401)
        try:
            body = await _load_json(request)
            record = AnnouncementRecord(
                announcement_id=_required_text(body, "announcement_id"),
                title=_required_text(body, "title"),
                content=_required_text(body, "content"),
                priority=int(body.get("priority", 0)),
                is_pinned=bool(body.get("is_pinned", False)),
                audience_type=_required_text(body, "audience_type"),
                audience_filter=dict(body.get("audience_filter", {})),
                channels=list(body.get("channels", [])),
                status=_required_text(body, "status"),
            )
        except ValueError as exc:
            return _error_response(str(exc), status=400)
        try:
            await store.put_announcement(record)
        except Exception:
            return _error_response("redis unavailable", status=503)
        await _emit_event(
            event_router,
            RuntimeEvent(
                event_type="control.admin.announcement.created",
                level="INFO",
                service="control-admin",
                region="main",
                message="control announcement created",
                payload={
                    "announcement_id": record.announcement_id,
                    "source": "http_api",
                },
            ),
        )
        return web.json_response({"ok": True, "announcement": asdict(record)})

    async def update_announcement(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            await _emit_unauthorized_event(event_router, request)
            return _error_response("unauthorized", status=401)
        announcement_id = str(request.match_info.get("announcement_id", "")).strip()
        if not announcement_id:
            return _error_response("announcement_id required", status=400)
        try:
            body = await _load_json(request)
            record = AnnouncementRecord(
                announcement_id=announcement_id,
                title=_required_text(body, "title"),
                content=_required_text(body, "content"),
                priority=int(body.get("priority", 0)),
                is_pinned=bool(body.get("is_pinned", False)),
                audience_type=_required_text(body, "audience_type"),
                audience_filter=dict(body.get("audience_filter", {})),
                channels=list(body.get("channels", [])),
                status=_required_text(body, "status"),
            )
        except ValueError as exc:
            return _error_response(str(exc), status=400)
        try:
            await store.put_announcement(record)
        except Exception:
            return _error_response("redis unavailable", status=503)
        await _emit_event(
            event_router,
            RuntimeEvent(
                event_type="control.admin.announcement.updated",
                level="INFO",
                service="control-admin",
                region="main",
                message="control announcement updated",
                payload={
                    "announcement_id": announcement_id,
                    "source": "http_api",
                },
            ),
        )
        return web.json_response({"ok": True, "announcement": asdict(record)})

    async def delete_announcement(request: web.Request) -> web.Response:
        if not _check_bearer(request, admin_token):
            await _emit_unauthorized_event(event_router, request)
            return _error_response("unauthorized", status=401)
        announcement_id = str(request.match_info.get("announcement_id", "")).strip()
        if not announcement_id:
            return _error_response("announcement_id required", status=400)
        try:
            await store.delete_announcement(announcement_id)
        except Exception:
            return _error_response("redis unavailable", status=503)
        await _emit_event(
            event_router,
            RuntimeEvent(
                event_type="control.admin.announcement.deleted",
                level="INFO",
                service="control-admin",
                region="main",
                message="control announcement deleted",
                payload={"announcement_id": announcement_id, "source": "http_api"},
            ),
        )
        return web.json_response({"ok": True, "announcement_id": announcement_id})

    app.router.add_get("/healthz", healthz)
    app.router.add_get("/control/limits", list_limits)
    app.router.add_get("/control/limits/{rule_id}", get_limit)
    app.router.add_put("/control/limits/{rule_id}", put_limit)
    app.router.add_delete("/control/limits/{rule_id}", delete_limit)
    app.router.add_get("/control/switches", list_switches)
    app.router.add_get("/control/switches/{switch_id:.+}", get_switch)
    app.router.add_put("/control/switches/{switch_id:.+}", put_switch)
    app.router.add_delete("/control/switches/{switch_id:.+}", delete_switch)
    app.router.add_get("/announcements", list_announcements)
    app.router.add_get("/announcements/{announcement_id}", get_announcement)
    app.router.add_post("/announcements", create_announcement)
    app.router.add_put("/announcements/{announcement_id}", update_announcement)
    app.router.add_delete("/announcements/{announcement_id}", delete_announcement)
    return app


def build_runtime_control_admin_app(
    settings: WorkerSettings,
    *,
    redis_factory=default_redis_factory,
    event_router=None,
) -> web.Application:
    redis_client = redis_factory(settings.redis_url)
    app = build_control_admin_app(
        store=ControlPlaneStore(redis_client),
        admin_token=settings.control_admin_token,
        event_router=event_router,
    )

    async def close_redis(_app: web.Application) -> None:
        await redis_client.aclose()

    app.on_cleanup.append(close_redis)
    return app


def main() -> None:
    settings = get_worker_settings()
    if not settings.control_admin_enabled:
        raise RuntimeError("control admin service is disabled")
    if not settings.control_admin_token:
        raise RuntimeError("CONTROL_ADMIN_TOKEN is required")
    app = build_runtime_control_admin_app(
        settings,
        event_router=build_event_router(get_alert_settings()),
    )
    web.run_app(
        app,
        host=settings.control_admin_bind_host,
        port=settings.control_admin_port,
    )


if __name__ == "__main__":
    main()
