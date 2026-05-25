from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "cross-exchange-arbitrage"
    env: str = "dev"
    database_url: str = "sqlite+aiosqlite:///local.db"
    redis_url: str = "redis://localhost:6379/0"
    enabled_exchanges: List[str] = Field(
        default_factory=lambda: ["okx", "binance", "bybit", "bitget", "gate"]
    )
    enabled_regions: List[str] = Field(default_factory=lambda: ["default"])
    default_region: str = "default"
    redis_opportunity_key: str = "arb:zset:open"
    redis_close_key: str = "arb:zset:close"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
