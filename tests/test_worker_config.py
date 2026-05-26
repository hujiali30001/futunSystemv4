from app.runtime.worker_config import (
    AlertSettings,
    WorkerSettings,
    load_exchange_credential_from_env,
    load_exchange_credentials_from_env,
    load_exchange_proxies_from_env,
)


def test_worker_settings_parse_csv_exchange_list():
    settings = WorkerSettings(
        redis_url="redis://127.0.0.1:6379/0",
        spot_exchanges="okx, binance ,bybit,bitget,gate",
        worker_role="scanner",
    )

    assert settings.spot_exchanges == ["okx", "binance", "bybit", "bitget", "gate"]
    assert settings.spot_symbol == "BTC/USDT"
    assert settings.scanner_poll_interval_seconds == 1.0
    assert settings.consumer_block_ms == 1000


def test_worker_settings_parse_csv_exchange_list_from_env(monkeypatch):
    monkeypatch.setenv("SPOT_EXCHANGES", "okx,binance,bybit,bitget,gate")

    settings = WorkerSettings()

    assert settings.spot_exchanges == ["okx", "binance", "bybit", "bitget", "gate"]


def test_worker_settings_parse_spot_symbols_csv():
    settings = WorkerSettings(
        spot_symbol="BTC/USDT",
        spot_symbols="BTC/USDT, ETH/USDT ,SOL/USDT",
        orderbook_depth_limit=5,
        target_quote_amount=100.0,
    )

    assert settings.spot_symbols == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    assert settings.orderbook_depth_limit == 5
    assert settings.target_quote_amount == 100.0


def test_worker_settings_fallback_to_single_spot_symbol_when_spot_symbols_missing():
    settings = WorkerSettings(spot_symbol="BTC/USDT")

    assert settings.active_spot_symbols == ["BTC/USDT"]


def test_worker_settings_support_dispatcher_and_executor_roles():
    settings = WorkerSettings(
        worker_role="dispatcher",
        node_id="main",
        dispatch_source_stream="stream:spot_opps",
        executor_stream_key="stream:spot_exec_tasks:main",
        dispatch_user_ids="42,99",
    )

    assert settings.worker_role == "dispatcher"
    assert settings.node_id == "main"
    assert settings.dispatch_source_stream == "stream:spot_opps"
    assert settings.executor_stream_key == "stream:spot_exec_tasks:main"
    assert settings.dispatch_user_ids == ["42", "99"]
    assert settings.resolved_executor_stream_key == "stream:spot_exec_tasks:main"


def test_worker_settings_accept_arb_dispatcher_role():
    settings = WorkerSettings(worker_role="arb_dispatcher")

    assert settings.worker_role == "arb_dispatcher"


def test_worker_settings_accept_arb_executor_role():
    settings = WorkerSettings(worker_role="arb_executor")

    assert settings.worker_role == "arb_executor"


def test_worker_settings_parse_user_node_routes_csv():
    settings = WorkerSettings(
        user_node_routes="42:node-a, 99 : node-b",
    )

    assert settings.user_node_routes == {
        "42": "node-a",
        "99": "node-b",
    }


def test_worker_settings_parse_route_admin_fields():
    settings = WorkerSettings(
        route_admin_enabled=True,
        route_admin_bind_host="127.0.0.1",
        route_admin_port=8787,
        route_admin_token="secret-token",
    )

    assert settings.route_admin_enabled is True
    assert settings.route_admin_bind_host == "127.0.0.1"
    assert settings.route_admin_port == 8787
    assert settings.route_admin_token == "secret-token"


def test_worker_settings_parse_control_admin_fields():
    settings = WorkerSettings(
        control_admin_enabled=True,
        control_admin_bind_host="127.0.0.1",
        control_admin_port=8790,
        control_admin_token="top-secret",
    )

    assert settings.control_admin_enabled is True
    assert settings.control_admin_bind_host == "127.0.0.1"
    assert settings.control_admin_port == 8790
    assert settings.control_admin_token == "top-secret"


def test_worker_settings_parse_database_fields():
    settings = WorkerSettings(
        database_enabled=True,
        database_url="sqlite:///./furun.db",
    )

    assert settings.database_enabled is True
    assert settings.database_url == "sqlite:///./furun.db"


def test_alert_settings_parse_values_from_env(monkeypatch):
    monkeypatch.setenv("ALERTS_ENABLED", "1")
    monkeypatch.setenv("ALERT_FEISHU_ENABLED", "1")
    monkeypatch.setenv("ALERT_FEISHU_WEBHOOK", "https://example.test/hook")
    monkeypatch.setenv("ALERT_EMAIL_ENABLED", "1")
    monkeypatch.setenv("ALERT_EMAIL_TO", "alice@qq.com, bob@qq.com")
    monkeypatch.setenv("ALERT_SUCCESS_SPREAD_BPS_THRESHOLD", "88.5")
    monkeypatch.setenv("ALERT_DEDUPE_WINDOW_SECONDS", "120")

    settings = AlertSettings()

    assert settings.alerts_enabled is True
    assert settings.alert_feishu_enabled is True
    assert settings.alert_feishu_webhook == "https://example.test/hook"
    assert settings.alert_email_enabled is True
    assert settings.alert_email_to == ["alice@qq.com", "bob@qq.com"]
    assert settings.alert_success_spread_bps_threshold == 88.5
    assert settings.alert_dedupe_window_seconds == 120


def test_load_exchange_credentials_and_proxies_from_env(monkeypatch):
    monkeypatch.setenv("OKX_API_KEY", "okx-key")
    monkeypatch.setenv("OKX_SECRET", "okx-secret")
    monkeypatch.setenv("OKX_PASSWORD", "okx-pass")
    monkeypatch.setenv("OKX_PROXY_TYPE", "http")
    monkeypatch.setenv("OKX_PROXY_HOST", "127.0.0.1")
    monkeypatch.setenv("OKX_PROXY_PORT", "8080")
    monkeypatch.setenv("OKX_PROXY_USERNAME", "alice")
    monkeypatch.setenv("OKX_PROXY_PASSWORD", "secret")

    single = load_exchange_credential_from_env("okx")
    credentials = load_exchange_credentials_from_env(["okx", "gate"])
    proxies = load_exchange_proxies_from_env(["okx", "gate"])

    assert single is not None
    assert single.api_key == "okx-key"
    assert credentials["okx"].secret == "okx-secret"
    assert "gate" not in credentials
    assert proxies["okx"]["http"] == "http://alice:secret@127.0.0.1:8080"
    assert "gate" not in proxies


def test_load_exchange_credentials_for_binance(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "binance-key")
    monkeypatch.setenv("BINANCE_SECRET", "binance-secret")

    cred = load_exchange_credential_from_env("binance")

    assert cred is not None
    assert cred.api_key == "binance-key"
    assert cred.secret == "binance-secret"


def test_load_exchange_credentials_for_bybit(monkeypatch):
    monkeypatch.setenv("BYBIT_API_KEY", "bybit-key")
    monkeypatch.setenv("BYBIT_SECRET", "bybit-secret")

    cred = load_exchange_credential_from_env("bybit")

    assert cred is not None
    assert cred.api_key == "bybit-key"
    assert cred.secret == "bybit-secret"


def test_load_exchange_credentials_for_all_five(monkeypatch):
    for ex in ["okx", "binance", "bybit", "bitget", "gate"]:
        monkeypatch.setenv(f"{ex.upper()}_API_KEY", f"{ex}-key")
        monkeypatch.setenv(f"{ex.upper()}_SECRET", f"{ex}-secret")

    credentials = load_exchange_credentials_from_env(
        ["okx", "binance", "bybit", "bitget", "gate"]
    )

    assert set(credentials.keys()) == {"okx", "binance", "bybit", "bitget", "gate"}
    for ex in ["okx", "binance", "bybit", "bitget", "gate"]:
        assert credentials[ex].api_key == f"{ex}-key"
