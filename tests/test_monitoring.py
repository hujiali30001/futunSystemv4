from app.audit.logger import AuditLogger
from app.monitoring.metrics import MetricsRegistry


def test_metrics_and_audit_capture_admin_actions():
    metrics = MetricsRegistry()
    audit = AuditLogger()

    metrics.increment("tasks.rejected_by_limits")
    audit.record("admin.limit.updated", {"scope": "user", "scope_id": "42"})

    assert metrics.counters["tasks.rejected_by_limits"] == 1
    assert audit.entries[0]["event_type"] == "admin.limit.updated"
