import os
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.exchanges.session_manager import ExchangeCredentials, build_proxy_urls


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    redis_url: str = "redis://127.0.0.1:6379/0"
    env_mode: str = "testnet"
    spot_symbol: str = "BTC/USDT"
    spot_symbols: Annotated[list[str], NoDecode] = Field(default_factory=list)
    spot_exchanges: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["okx", "bitget", "gate"]
    )
    orderbook_depth_limit: int = 5
    target_quote_amount: float = 100.0
    scanner_poll_interval_seconds: float = 1.0
    consumer_block_ms: int = 1000
    worker_role: Literal[
        "scanner",
        "consumer",
        "dispatcher",
        "arb_dispatcher",
        "executor",
        "arb_executor",
        "repair",
    ] = "scanner"
    worker_region: str = "default"
    database_enabled: bool = False
    database_url: str = "sqlite:///./furun.db"
    node_id: str = "default"
    dispatch_user_ids: Annotated[list[str], NoDecode] = Field(default_factory=list)
    user_node_routes: Annotated[dict[str, str], NoDecode] = Field(default_factory=dict)
    dispatch_source_stream: str = "stream:spot_opps"
    executor_stream_key: str | None = None
    repair_stream_key: str | None = None
    route_admin_enabled: bool = False
    route_admin_bind_host: str = "127.0.0.1"
    route_admin_port: int = 8787
    route_admin_token: str = ""
    control_admin_enabled: bool = False
    control_admin_bind_host: str = "127.0.0.1"
    control_admin_port: int = 8788
    control_admin_token: str = ""

    @field_validator("spot_symbols", "spot_exchanges", "dispatch_user_ids", mode="before")
    @classmethod
    def split_csv(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("user_node_routes", mode="before")
    @classmethod
    def parse_user_node_routes(
        cls, value: str | dict[str, str]
    ) -> str | dict[str, str]:
        if isinstance(value, str):
            routes: dict[str, str] = {}
            for item in value.split(","):
                entry = item.strip()
                if not entry:
                    continue
                if ":" not in entry:
                    raise ValueError(
                        "user_node_routes entries must use user_id:node_id format"
                    )
                user_id, node_id = entry.split(":", 1)
                routes[user_id.strip()] = node_id.strip()
            return routes
        return value

    @property
    def active_spot_symbols(self) -> list[str]:
        return self.spot_symbols or [self.spot_symbol]

    @property
    def resolved_executor_stream_key(self) -> str:
        return self.executor_stream_key or f"stream:spot_exec_tasks:{self.node_id}"

    @property
    def resolved_repair_stream_key(self) -> str:
        return self.repair_stream_key or f"stream:repair_tasks:{self.node_id}"

    @property
    def resolved_dispatch_source_stream(self) -> str:
        if (
            self.worker_role == "arb_dispatcher"
            and self.dispatch_source_stream == "stream:spot_opps"
        ):
            return "stream:opportunities"
        return self.dispatch_source_stream


class AlertSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    alerts_enabled: bool = True
    alert_feishu_enabled: bool = False
    alert_feishu_webhook: str | None = None
    alert_email_enabled: bool = False
    alert_email_smtp_host: str = "smtp.qq.com"
    alert_email_smtp_port: int = 465
    alert_email_username: str | None = None
    alert_email_password: str | None = None
    alert_email_to: Annotated[list[str], NoDecode] = Field(default_factory=list)
    alert_success_spread_bps_threshold: float = 0.0
    alert_dedupe_window_seconds: int = 60

    @field_validator("alert_email_to", mode="before")
    @classmethod
    def split_recipients(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache(maxsize=1)
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()


@lru_cache(maxsize=1)
def get_alert_settings() -> AlertSettings:
    return AlertSettings()


def load_exchange_credential_from_env(exchange: str) -> ExchangeCredentials | None:
    prefix = exchange.upper().replace(".", "_")
    api_key = os.getenv(f"{prefix}_API_KEY")
    secret = os.getenv(f"{prefix}_SECRET")
    password = os.getenv(f"{prefix}_PASSWORD")
    if not api_key or not secret:
        return None
    return ExchangeCredentials(api_key=api_key, secret=secret, password=password)


def load_exchange_credentials_from_env(
    exchanges: list[str],
) -> dict[str, ExchangeCredentials]:
    credentials: dict[str, ExchangeCredentials] = {}
    for exchange in exchanges:
        loaded = load_exchange_credential_from_env(exchange)
        if loaded is not None:
            credentials[exchange] = loaded
    return credentials


def load_exchange_proxies_from_env(exchanges: list[str]) -> dict[str, dict[str, str]]:
    proxies_by_exchange: dict[str, dict[str, str]] = {}
    for exchange in exchanges:
        prefix = exchange.upper().replace(".", "_")
        host = os.getenv(f"{prefix}_PROXY_HOST")
        port = os.getenv(f"{prefix}_PROXY_PORT")
        if not host or not port:
            continue
        proxy_type = os.getenv(f"{prefix}_PROXY_TYPE", "http")
        username = os.getenv(f"{prefix}_PROXY_USERNAME")
        password = os.getenv(f"{prefix}_PROXY_PASSWORD")
        proxies_by_exchange[exchange] = build_proxy_urls(
            proxy_type=proxy_type,
            host=host,
            port=int(port),
            username=username,
            password=password,
        )
    return proxies_by_exchange
