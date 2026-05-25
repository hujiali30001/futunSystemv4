import json

from app.runtime.runtime_events import RuntimeEvent


def test_runtime_event_to_json_contains_core_fields():
    event = RuntimeEvent(
        event_type="scanner.iteration.failed",
        level="ERROR",
        service="scanner",
        message="scanner iteration failed",
        symbol="BTC/USDT",
        exchange="okx",
        payload={"error": "timeout"},
    )

    data = json.loads(event.to_json())

    assert data["event_type"] == "scanner.iteration.failed"
    assert data["level"] == "ERROR"
    assert data["service"] == "scanner"
    assert data["symbol"] == "BTC/USDT"
    assert data["exchange"] == "okx"
    assert data["payload"]["error"] == "timeout"
    assert data["created_at"].endswith("+00:00")
