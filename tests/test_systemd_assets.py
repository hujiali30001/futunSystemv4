from pathlib import Path

from app.runtime.systemd_assets import render_systemd_unit, render_worker_env_example


def test_render_systemd_unit_contains_expected_execstart_for_scanner():
    content = render_systemd_unit(role="scanner")

    assert "Description=FuRun spot scanner worker" in content
    assert "EnvironmentFile=/home/ubuntu/furunsystemv4/current/.env.worker" in content
    assert (
        "ExecStart=/home/ubuntu/furunsystemv4/current/.venv/bin/python "
        "-m app.runtime.worker_service --role scanner"
    ) in content
    assert "Restart=always" in content


def test_render_systemd_unit_contains_expected_execstart_for_dispatcher_and_executor():
    dispatcher_content = render_systemd_unit(role="dispatcher")
    executor_content = render_systemd_unit(role="executor")

    assert "Description=FuRun spot dispatcher worker" in dispatcher_content
    assert (
        "ExecStart=/home/ubuntu/furunsystemv4/current/.venv/bin/python "
        "-m app.runtime.worker_service --role dispatcher"
    ) in dispatcher_content
    assert "Description=FuRun spot executor worker" in executor_content
    assert (
        "ExecStart=/home/ubuntu/furunsystemv4/current/.venv/bin/python "
        "-m app.runtime.worker_service --role executor"
    ) in executor_content


def test_render_systemd_unit_contains_route_admin_execstart():
    content = render_systemd_unit(role="route-admin")

    assert "Description=FuRun route admin service" in content
    assert "EnvironmentFile=/home/ubuntu/furunsystemv4/current/.env.worker" in content
    assert (
        "ExecStart=/home/ubuntu/furunsystemv4/current/.venv/bin/python "
        "-m app.runtime.route_admin_service"
    ) in content
    assert "Restart=always" in content


def test_render_systemd_unit_supports_control_admin():
    content = render_systemd_unit(role="control-admin")

    assert "Description=FuRun control admin service" in content
    assert "EnvironmentFile=/home/ubuntu/furunsystemv4/current/.env.worker" in content
    assert (
        "ExecStart=/home/ubuntu/furunsystemv4/current/.venv/bin/python "
        "-m app.runtime.control_admin_service"
    ) in content
    assert "Restart=always" in content


def test_render_worker_env_example_contains_core_runtime_keys():
    content = render_worker_env_example()

    assert "REDIS_URL=redis://127.0.0.1:6379/0" in content
    assert "SPOT_SYMBOL=BTC/USDT" in content
    assert "SPOT_EXCHANGES=okx,binance,bybit,bitget,gate" in content
    assert "OKX_API_KEY=" in content
    assert "OKX_PROXY_HOST=" in content


def test_render_worker_env_example_contains_node_role_fields():
    content = render_worker_env_example()

    assert "WORKER_ROLE=scanner" in content
    assert "WORKER_REGION=main" in content
    assert "NODE_ID=main" in content
    assert "DISPATCH_USER_IDS=42,99" in content
    assert "DISPATCH_SOURCE_STREAM=stream:spot_opps" in content
    assert "EXECUTOR_STREAM_KEY=stream:spot_exec_tasks:main" in content


def test_render_worker_env_example_contains_route_admin_fields():
    content = render_worker_env_example()

    assert "ROUTE_ADMIN_ENABLED=0" in content
    assert "ROUTE_ADMIN_BIND_HOST=127.0.0.1" in content
    assert "ROUTE_ADMIN_PORT=8787" in content
    assert "ROUTE_ADMIN_TOKEN=" in content


def test_render_worker_env_example_contains_control_admin_fields():
    content = render_worker_env_example()

    assert "CONTROL_ADMIN_ENABLED=0" in content
    assert "CONTROL_ADMIN_BIND_HOST=127.0.0.1" in content
    assert "CONTROL_ADMIN_PORT=8788" in content
    assert "CONTROL_ADMIN_TOKEN=" in content


def test_render_worker_env_example_contains_database_fields():
    content = render_worker_env_example()

    assert "DATABASE_ENABLED=0" in content
    assert "DATABASE_URL=sqlite:///./furun.db" in content


def test_dispatcher_and_executor_units_exist_and_target_expected_roles():
    dispatcher_unit = Path("deploy/systemd/furun-spot-dispatcher.service")
    executor_unit = Path("deploy/systemd/furun-spot-executor.service")

    assert dispatcher_unit.exists()
    assert executor_unit.exists()
    assert "--role dispatcher" in dispatcher_unit.read_text(encoding="utf-8")
    assert "--role executor" in executor_unit.read_text(encoding="utf-8")


def test_route_admin_unit_exists_and_targets_route_admin_service():
    route_admin_unit = Path("deploy/systemd/furun-route-admin.service")

    assert route_admin_unit.exists()
    content = route_admin_unit.read_text(encoding="utf-8")
    assert "Description=FuRun route admin service" in content
    assert "-m app.runtime.route_admin_service" in content


def test_control_admin_unit_exists_and_targets_control_admin_service():
    control_admin_unit = Path("deploy/systemd/furun-control-admin.service")

    assert control_admin_unit.exists()
    content = control_admin_unit.read_text(encoding="utf-8")
    assert "Description=FuRun control admin service" in content
    assert "-m app.runtime.control_admin_service" in content


def test_env_worker_example_file_contains_database_fields():
    env_example = Path("deploy/systemd/.env.worker.example")

    assert env_example.exists()
    content = env_example.read_text(encoding="utf-8")
    assert "DATABASE_ENABLED=0" in content
    assert "DATABASE_URL=sqlite:///./furun.db" in content


def test_requirements_include_postgres_driver():
    requirements = Path("requirements.txt")

    assert requirements.exists()
    content = requirements.read_text(encoding="utf-8")
    assert "psycopg2-binary" in content


def test_render_worker_env_example_contains_binance_and_bybit_credentials():
    content = render_worker_env_example()

    assert "BINANCE_API_KEY=" in content
    assert "BINANCE_SECRET=" in content
    assert "BINANCE_PASSWORD=" in content
    assert "BYBIT_API_KEY=" in content
    assert "BYBIT_SECRET=" in content
    assert "BYBIT_PASSWORD=" in content
