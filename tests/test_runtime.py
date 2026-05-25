from main import main
from app.runtime.bootstrap import build_runtime
from app.runtime.router import TaskRoute, route_task


def test_route_task_uses_home_region():
    route = route_task(
        TaskRoute(
            task_id="task-1",
            user_id=7,
            home_region="sg",
            fallback_region="hk",
        )
    )

    assert route.primary_region == "sg"
    assert route.fallback_region == "hk"


def test_build_runtime_uses_default_region_when_region_missing():
    app = build_runtime(service_name="scanner")

    assert app.service_name == "scanner"
    assert app.region == "default"


def test_main_parses_service_and_region_arguments():
    app = main(["--service", "trader", "--region", "hk"])

    assert app.service_name == "trader"
    assert app.region == "hk"
