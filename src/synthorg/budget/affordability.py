# module-kind: code
"""Budget affordability protocol for the coordination replan gate.

A narrow structural view of the pre-flight budget check the magentic
replan hook consults before triggering a replan. The engine-side hook
and the budget state slice name the dependency through this protocol so
neither imports the heavy concrete :class:`~synthorg.budget.enforcer.BudgetEnforcer`
(which reaches back into the engine layer at module load and would close
a cold-import cycle). ``BudgetEnforcer`` satisfies it structurally.
"""

from typing import Protocol, runtime_checkable

from synthorg.budget.degradation import PreFlightResult


@runtime_checkable
class BudgetAffordabilityChecker(Protocol):
    """Pre-flight budget check used to gate an affordable replan."""

    async def check_can_execute(
        self,
        agent_id: str,
        *,
        provider_name: str | None = None,
        estimated_tokens: int = 0,
    ) -> PreFlightResult:
        """Verify limits allow execution; raise when the budget is exhausted.

        Returns:
            The pre-flight result when execution is permitted.
        """
        ...
