import asyncio

from app.admin.control_plane import (
    ControlPlane,
    LimitRule,
    PlatformSwitch,
    build_control_plane,
)
from app.admin.control_store import LimitRuleRecord, PlatformSwitchRecord
from app.admin.notifier import AnnouncementMessage, AnnouncementNotifier


def test_control_plane_returns_smallest_allowed_notional():
    plane = ControlPlane(
        switches=[],
        limit_rules=[
            LimitRule(scope="platform", scope_id="global", limit_value=100000.0),
            LimitRule(scope="user", scope_id="42", limit_value=1200.0),
            LimitRule(scope="strategy", scope_id="7", limit_value=800.0),
        ],
    )

    decision = plane.evaluate_open_request(
        user_id=42,
        strategy_id=7,
        symbol="BTC/USDT",
        exchange="okx",
        requested_notional=1500.0,
    )

    assert decision.allowed is True
    assert decision.approved_notional == 800.0


def test_control_plane_blocks_when_only_reduce_mode_is_active():
    plane = ControlPlane(
        switches=[
            PlatformSwitch(
                key="platform.reduce_only",
                enabled=True,
                scope="platform",
                scope_id="global",
            )
        ],
        limit_rules=[],
    )

    decision = plane.evaluate_open_request(
        user_id=1,
        strategy_id=1,
        symbol="ETH/USDT",
        exchange="binance",
        requested_notional=100.0,
    )

    assert decision.allowed is False
    assert decision.reason == "reduce_only"


def test_build_control_plane_converts_store_records_into_runtime_rules():
    plane = build_control_plane(
        limit_rules=[
            LimitRuleRecord(
                rule_id="strategy-7-cap",
                scope_type="strategy",
                scope_id="7",
                limit_type="max_notional",
                limit_value=500.0,
                enabled=True,
                priority=100,
            )
        ],
        switches=[
            PlatformSwitchRecord(
                switch_key="platform.reduce_only",
                scope_type="platform",
                scope_id="global",
                enabled=False,
            )
        ],
    )

    decision = plane.evaluate_open_request(
        user_id=42,
        strategy_id=7,
        symbol="BTC/USDT",
        exchange="okx",
        requested_notional=1000.0,
    )

    assert isinstance(plane, ControlPlane)
    assert decision.allowed is True
    assert decision.approved_notional == 500.0


def test_announcement_notifier_queues_every_channel():
    notifier = AnnouncementNotifier()

    result = asyncio.run(
        notifier.publish(
            AnnouncementMessage(
                title="维护通知",
                content="今晚 23:00 开始风控演练",
                channels=["site", "feishu"],
            )
        )
    )

    assert result == {"site": "skipped", "feishu": "skipped"}
