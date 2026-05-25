from enum import Enum


class EnvironmentMode(str, Enum):
    TESTNET = "testnet"
    MAINNET = "mainnet"


class ScopeType(str, Enum):
    PLATFORM = "platform"
    USER = "user"
    EXCHANGE = "exchange"
    SYMBOL = "symbol"
    STRATEGY = "strategy"


class LimitType(str, Enum):
    TOTAL_NOTIONAL = "total_notional"
    SINGLE_TASK_NOTIONAL = "single_task_notional"
    EXCHANGE_NOTIONAL = "exchange_notional"
    SYMBOL_NOTIONAL = "symbol_notional"
