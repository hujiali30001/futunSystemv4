from dataclasses import dataclass


@dataclass(slots=True)
class TaskRoute:
    task_id: str
    user_id: int
    home_region: str
    fallback_region: str


@dataclass(slots=True)
class ResolvedRoute:
    primary_region: str
    fallback_region: str


def route_task(task: TaskRoute) -> ResolvedRoute:
    return ResolvedRoute(
        primary_region=task.home_region,
        fallback_region=task.fallback_region,
    )
