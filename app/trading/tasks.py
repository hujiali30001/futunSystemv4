from dataclasses import dataclass, field


@dataclass(slots=True)
class ExecutionLeg:
    exchange: str
    side: str
    order_type: str
    amount: float
    price: float | None = None


@dataclass(slots=True)
class ExecutionTask:
    task_id: str
    symbol: str
    open_legs: list[ExecutionLeg] = field(default_factory=list)
