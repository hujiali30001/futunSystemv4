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
    assert "WORKER_REGION=repair-canary" in script_text
    assert "NODE_ID=repair-canary" in script_text
    assert "redis-cli XADD" in script_text
    assert "CANARY_EXECUTOR_STREAM" in script_text
    assert "redis-cli XADD stream:spot_exec_tasks:main '*'" not in script_text


def test_repair_systemd_canary_script_cleans_legacy_residue_and_verifies_silence():
    script_path = Path(
        r"d:\old\FuRunSystemV4\deploy\systemd\sync_and_validate_repair_systemd_dual_service.py"
    )
    script_text = script_path.read_text(encoding="utf-8")

    assert 'delete(ArbitrageTask).where(ArbitrageTask.task_uuid == "{CANARY_TASK_UUID}")' in script_text
    assert 'MAIN_EXECUTOR_STREAM = "stream:spot_exec_tasks:main"' in script_text
    assert 'MAIN_REPAIR_STREAM = "stream:repair_tasks:main"' in script_text
    assert 'CANARY_EXECUTOR_STREAM = "stream:spot_exec_tasks:repair-canary"' in script_text
    assert 'CANARY_REPAIR_STREAM = "stream:repair_tasks:repair-canary"' in script_text
    assert "def collect_remote_stream_entries(stream_key: str, count: int = 50) -> str:" in script_text
    assert "def delete_remote_stream_entries(stream_key: str, message_ids: list[str]) -> str:" in script_text
    assert "def extract_matching_message_ids(xrevrange_output: str, *, task_uuid: str) -> list[str]:" in script_text
    assert '"cleanup_result": cleanup_result' in script_text
    assert '"post_cleanup_main_executor": post_cleanup_main_executor' in script_text
    assert '"post_cleanup_main_repair": post_cleanup_main_repair' in script_text
    assert '"post_cleanup_canary_executor": post_cleanup_canary_executor' in script_text
    assert '"post_cleanup_canary_repair": post_cleanup_canary_repair' in script_text
    assert '"executor_silence_logs": executor_silence_logs' in script_text
    assert 'redis-cli XREVRANGE {CANARY_REPAIR_STREAM} + - COUNT 5' in script_text
    assert "date '+%Y-%m-%d %H:%M:%S %z'" in script_text
    assert "grep 'repair-systemd-canary-1' || true" in script_text
