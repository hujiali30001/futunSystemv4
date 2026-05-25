from dataclasses import dataclass

from app.trading.executor import ExecutionResult


@dataclass(slots=True)
class RepairPlan:
    action: str
    reason: str


class RiskManager:
    def build_repair_plan(self, result: ExecutionResult) -> RepairPlan:
        if result.status == "OPEN_PARTIAL":
            return RepairPlan(action="AUTO_HEDGE_REPAIRING", reason="one_leg_failed")
        return RepairPlan(action="NONE", reason="fully_hedged")
