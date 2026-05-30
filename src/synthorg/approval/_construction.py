# module-kind: code
"""Approval feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.approval.state import ApprovalStateSlice
from synthorg.engine.review_gate import ReviewGateService

if TYPE_CHECKING:
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the approval slice (store + review gate).

    The review gate transitions tasks from IN_REVIEW on approval; it is built
    whenever a task engine exists so the fail-fast self-review / missing-task
    preflight runs in task-engine-only deployments (decision recording
    degrades to a WARNING-level no-op when persistence is absent).
    """
    app_state.swap_slice(ApprovalStateSlice.model_construct(store=deps.approval_store))
    task_engine = deps.phase1.task_engine
    if task_engine is not None:
        review_gate = ReviewGateService(
            task_engine=task_engine,
            persistence=deps.persistence,
        )
        app_state.swap_slice(
            app_state.slice(ApprovalStateSlice).model_copy(
                update={"review_gate": review_gate},
            )
        )
