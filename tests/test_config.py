from config import Settings, get_settings


def test_settings_parse_exchange_and_region_lists():
    settings = Settings(
        database_url="sqlite+aiosqlite:///unit.db",
        redis_url="redis://localhost:6379/0",
        enabled_exchanges=["binance", "okx"],
        enabled_regions=["sg", "hk"],
    )

    assert settings.enabled_exchanges == ["binance", "okx"]
    assert settings.enabled_regions == ["sg", "hk"]


def test_get_settings_returns_cached_instance():
    first = get_settings()
    second = get_settings()

    assert first is second
