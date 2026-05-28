import base64
import hashlib
import os
from dataclasses import dataclass

from cryptography.fernet import Fernet

from app.exchanges.session_manager import ExchangeCredentials, build_proxy_urls
from app.runtime.live_workers import _normalize_account_region, _parse_market_type_scope


class ExecutorAccountTruthError(RuntimeError):
    def __init__(self, reason: str, *, user_id: str, exchange: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.user_id = user_id
        self.exchange = exchange


@dataclass(slots=True)
class ResolvedExecutionAccount:
    account_id: int
    exchange: str
    credentials: ExchangeCredentials
    proxies: dict[str, str]


def _derive_fernet_key(secret: str) -> bytes:
    raw = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(raw)


class SecretCipher:
    def __init__(self, encryption_key: str) -> None:
        self._fernet = Fernet(_derive_fernet_key(encryption_key))

    def encrypt(self, plaintext: str | None) -> str | None:
        if plaintext is None:
            return None
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str | None) -> str | None:
        if ciphertext is None:
            return None
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except Exception:
            return ciphertext


class PassthroughSecretCipher:
    def decrypt(self, ciphertext: str | None) -> str | None:
        return ciphertext


class ExecutorAccountTruthResolver:
    def __init__(self, *, secret_cipher=None) -> None:
        self.secret_cipher = secret_cipher or PassthroughSecretCipher()

    def resolve_bound_accounts(
        self,
        *,
        accounts: list[object],
        user_id: str,
        buy_account_id: str,
        sell_account_id: str,
        buy_exchange: str,
        sell_exchange: str,
        env_mode: str,
        region: str,
    ) -> dict[str, ResolvedExecutionAccount]:
        del env_mode
        return {
            buy_exchange: self._load_bound_account(
                accounts=accounts,
                user_id=user_id,
                exchange=buy_exchange,
                account_id=buy_account_id,
                region=region,
            ),
            sell_exchange: self._load_bound_account(
                accounts=accounts,
                user_id=user_id,
                exchange=sell_exchange,
                account_id=sell_account_id,
                region=region,
            ),
        }

    def resolve_accounts(
        self,
        *,
        accounts: list[object],
        user_id: str,
        buy_exchange: str,
        sell_exchange: str,
        env_mode: str,
        region: str,
    ) -> dict[str, ResolvedExecutionAccount]:
        del env_mode
        return {
            buy_exchange: self._resolve_single_exchange(
                accounts=accounts,
                user_id=user_id,
                exchange=buy_exchange,
                region=region,
            ),
            sell_exchange: self._resolve_single_exchange(
                accounts=accounts,
                user_id=user_id,
                exchange=sell_exchange,
                region=region,
            ),
        }

    def _load_bound_account(
        self,
        *,
        accounts: list[object],
        user_id: str,
        exchange: str,
        account_id: str,
        region: str,
    ) -> ResolvedExecutionAccount:
        normalized_region = _normalize_account_region(region)
        for account in accounts:
            if str(getattr(account, "id", "")) != str(account_id):
                continue
            if str(getattr(account, "exchange", "")) != exchange:
                break
            if not getattr(account, "is_auto_trade_enabled", True):
                break
            if not _parse_market_type_scope(getattr(account, "market_type_scope", None)):
                break
            account_region = _normalize_account_region(
                getattr(account, "account_region", None)
            )
            if account_region not in {"default", normalized_region}:
                break
            return ResolvedExecutionAccount(
                account_id=int(getattr(account, "id")),
                exchange=exchange,
                credentials=ExchangeCredentials(
                    api_key=self._decrypt(
                        getattr(account, "api_key_ciphertext", None),
                        user_id=user_id,
                        exchange=exchange,
                    ),
                    secret=self._decrypt(
                        getattr(account, "secret_ciphertext", None),
                        user_id=user_id,
                        exchange=exchange,
                    ),
                    password=self._decrypt(
                        getattr(account, "passphrase_ciphertext", None),
                        user_id=user_id,
                        exchange=exchange,
                    ),
                ),
                proxies=self._build_proxies(account, user_id=user_id, exchange=exchange),
            )

        raise ExecutorAccountTruthError(
            "executor_account_binding_not_found",
            user_id=user_id,
            exchange=exchange,
            detail=(
                f"no executable bound account for exchange={exchange} "
                f"account_id={account_id}"
            ),
        )

    def _resolve_single_exchange(
        self,
        *,
        accounts: list[object],
        user_id: str,
        exchange: str,
        region: str,
    ) -> ResolvedExecutionAccount:
        normalized_region = _normalize_account_region(region)
        eligible = []
        for account in accounts:
            if str(getattr(account, "exchange", "")) != exchange:
                continue
            if not getattr(account, "is_auto_trade_enabled", True):
                continue
            if not _parse_market_type_scope(getattr(account, "market_type_scope", None)):
                continue
            account_region = _normalize_account_region(
                getattr(account, "account_region", None)
            )
            if account_region not in {"default", normalized_region}:
                continue
            eligible.append(account)

        if not eligible:
            raise ExecutorAccountTruthError(
                "executor_account_not_found",
                user_id=user_id,
                exchange=exchange,
                detail=f"no executable account for exchange={exchange}",
            )

        selected = sorted(eligible, key=lambda item: int(getattr(item, "id", 0)))[0]
        return ResolvedExecutionAccount(
            account_id=int(getattr(selected, "id")),
            exchange=exchange,
            credentials=ExchangeCredentials(
                api_key=self._decrypt(
                    getattr(selected, "api_key_ciphertext", None),
                    user_id=user_id,
                    exchange=exchange,
                ),
                secret=self._decrypt(
                    getattr(selected, "secret_ciphertext", None),
                    user_id=user_id,
                    exchange=exchange,
                ),
                password=self._decrypt(
                    getattr(selected, "passphrase_ciphertext", None),
                    user_id=user_id,
                    exchange=exchange,
                ),
            ),
            proxies=self._build_proxies(selected, user_id=user_id, exchange=exchange),
        )

    def _decrypt(
        self,
        ciphertext: str | None,
        *,
        user_id: str,
        exchange: str,
    ) -> str | None:
        if ciphertext is None:
            return None
        try:
            return self.secret_cipher.decrypt(ciphertext)
        except Exception as exc:
            raise ExecutorAccountTruthError(
                "executor_account_decrypt_failed",
                user_id=user_id,
                exchange=exchange,
                detail=str(exc),
            ) from exc

    def _build_proxies(
        self,
        account: object,
        *,
        user_id: str,
        exchange: str,
    ) -> dict[str, str]:
        proxy = getattr(account, "proxy", None)
        if proxy is None:
            return {}

        password = self._decrypt(
            getattr(proxy, "password_ciphertext", None),
            user_id=user_id,
            exchange=exchange,
        )
        return build_proxy_urls(
            proxy_type=str(getattr(proxy, "proxy_type")),
            host=str(getattr(proxy, "host")),
            port=int(getattr(proxy, "port")),
            username=getattr(proxy, "username", None),
            password=password,
        )
