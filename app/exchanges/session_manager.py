from dataclasses import dataclass, field
from typing import Any


def build_proxy_urls(
    *,
    proxy_type: str,
    host: str,
    port: int,
    username: str | None = None,
    password: str | None = None,
) -> dict[str, str]:
    auth = f"{username}:{password}@" if username and password else ""
    prefix = "socks5" if proxy_type.startswith("socks") else "http"
    url = f"{prefix}://{auth}{host}:{port}"
    return {"http": url, "https": url}


@dataclass(slots=True)
class ExchangeCredentials:
    api_key: str
    secret: str
    password: str | None = None


@dataclass(slots=True)
class ExchangeAccountSession:
    exchange: str
    env_mode: str
    proxies: dict[str, str]
    markets_loaded: bool = False
    markets: dict[str, Any] = field(default_factory=dict)
    client: Any = field(default=None)
    closed: bool = False

    async def mark_ready(self) -> None:
        if self.client is not None and hasattr(self.client, "load_markets"):
            self.markets = await self.client.load_markets()
        self.markets_loaded = True

    async def close(self) -> None:
        if self.closed:
            return

        client = self.client
        try:
            if client is not None and hasattr(client, "close"):
                await client.close()
        finally:
            self.closed = True
            self.client = None
            self.markets = {}
            self.markets_loaded = False


class ExchangeClientFactory:
    def __init__(self, ccxt_module: Any | None = None) -> None:
        self.ccxt_module = ccxt_module

    def create_session(
        self,
        *,
        exchange: str,
        env_mode: str,
        proxies: dict[str, str],
        credentials: ExchangeCredentials | None = None,
    ) -> ExchangeAccountSession:
        ccxt_module = self.ccxt_module or self._load_ccxt_module()
        exchange_class = getattr(ccxt_module, exchange)
        config: dict[str, Any] = {
            "enableRateLimit": True,
            "proxies": proxies,
        }
        if credentials is not None:
            config["apiKey"] = credentials.api_key
            config["secret"] = credentials.secret
            if credentials.password:
                config["password"] = credentials.password

        client = exchange_class(config)
        _use_demo = env_mode == "testnet" and exchange in {"bybit", "binance"} and hasattr(client, "enable_demo_trading")
        if _use_demo:
            client.enable_demo_trading(True)
        elif env_mode == "testnet" and hasattr(client, "set_sandbox_mode"):
            client.set_sandbox_mode(True)
        return ExchangeAccountSession(
            exchange=exchange,
            env_mode=env_mode,
            proxies=proxies,
            client=client,
        )

    @staticmethod
    def _load_ccxt_module() -> Any:
        import ccxt.async_support as ccxt_async

        return ccxt_async
