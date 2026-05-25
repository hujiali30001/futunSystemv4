import json
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(slots=True)
class RuntimeEvent:
    event_type: str
    level: str
    service: str
    message: str
    region: str | None = None
    symbol: str | None = None
    exchange: str | None = None
    exchanges: list[str] | None = None
    payload: dict = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "level": self.level,
            "service": self.service,
            "region": self.region,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "exchanges": self.exchanges,
            "message": self.message,
            "payload": self.payload,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=True, separators=(",", ":"))
