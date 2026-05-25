from datetime import datetime, timezone


class AuditLogger:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def record(self, event_type: str, payload: dict) -> None:
        self.entries.append(
            {
                "event_type": event_type,
                "payload": payload,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
