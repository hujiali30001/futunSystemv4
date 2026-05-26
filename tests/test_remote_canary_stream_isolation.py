from pathlib import Path


def test_repair_systemd_canary_uses_dedicated_streams_and_not_main_stream_injection():
    script_path = Path(
        r"d:\old\FuRunSystemV4\deploy\systemd\sync_and_validate_repair_systemd_dual_service.py"
    )
    script_text = script_path.read_text(encoding="utf-8")

    assert "stream:spot_exec_tasks:repair-canary" in script_text
    assert "stream:repair_tasks:repair-canary" in script_text
    assert "EXECUTOR_STREAM_KEY=stream:spot_exec_tasks:repair-canary" in script_text
    assert "REPAIR_STREAM_KEY=stream:repair_tasks:repair-canary" in script_text
    assert "redis-cli XADD stream:spot_exec_tasks:repair-canary '*'" in script_text
    assert "redis-cli XADD stream:spot_exec_tasks:main '*'" not in script_text
