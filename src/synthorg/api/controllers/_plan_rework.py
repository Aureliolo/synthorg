# module-kind: orchestrator
"""Re-plan an undispatched plan against an operator's change request.

"Send this back for revision" is a demand for another planning pass, and until
this module nothing performed one: the plan flipped to DRAFT, the note was
recorded on an audit event, and the service docstring deferred "turning it into
a concrete replan" to a wiring layer that did not exist. A live run left a plan
sitting at DRAFT carrying assumptions naming files that were never written,
with no route back to a corrected plan except the operator hand-authoring the
whole item list through ``POST /plans/{id}/replan``.

The pass is in-place rather than superseding because the statuses a change
request accepts (:data:`REWORKABLE_STATUSES`, DRAFT and PENDING_REVIEW) are
exactly the ones where nothing has dispatched: there is no running work to
retire and no successor to point a project at. It runs inline in the request
for the same reason the chat turn does: it is one LLM-bound call whose result
is the response, and parking it in the background would need a state for
"being re-planned" that the plan machine deliberately does not have.

The operator's note leads the brief and the panel's outstanding findings
follow it, so a plan corrected here answers both the human and the reviewers
that the human is overriding.
"""

from typing import NamedTuple

from synthorg.api.controllers._plan_input_validation import (
    reject_undecidable_graph,
    reject_unroutable_owners,
)
from synthorg.api.services._plan_revision import require_reworkable
from synthorg.api.state import AppState
from synthorg.core.agent import AgentIdentity
from synthorg.core.domain_errors import (
    ConflictError,
    ServiceUnavailableError,
    ValidationError,
)
from synthorg.core.plan import Plan, PlanItem, PlanPremises
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.context import (
    DecompositionContext,
    roster_from_agents,
)
from synthorg.engine.decomposition.plan_mapping import items_from_decomposition
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.plan_review.revision_brief import build_revision_brief
from synthorg.engine.state import EngineStateSlice
from synthorg.hr.state import HrStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_PLAN_CHANGES_REPLANNED
from synthorg.persistence.state import project_repository_of
from synthorg.workers.state import RuntimeStateSlice

logger = get_logger(__name__)


class RePlan(NamedTuple):
    """What a re-planning pass produced, for the caller to persist together.

    Attributes:
        items: The revised plan items.
        premises: The assumptions and open questions those items rest on.
            Carried beside them because the items are meaningless against the
            superseded plan's premises: a revision that rebuilds from scratch
            under an assumption that it already exists contradicts itself.
    """

    items: tuple[PlanItem, ...]
    premises: PlanPremises


async def replan_for_change_request(
    app_state: AppState,
    existing: Plan,
    *,
    note: str | None,
) -> RePlan:
    """Re-plan *existing* against the operator's *note*.

    Args:
        app_state: Application state, carrying the planner and the roster.
        existing: The plan the operator sent back.
        note: The operator's rationale. Leads the brief when present.

    Returns:
        The re-planned items and the premises they rest on.

    Raises:
        ConflictError: The plan is terminal, or its objective task is gone so
            there is nothing left to plan against.
        ValidationError: Neither the operator nor the panel said anything, so
            there is nothing to re-plan against and a pass would spend a
            planning call to reproduce the same plan.
        ServiceUnavailableError: No planner or no task engine is running, so
            the org cannot honour the change request. Refused loudly, and
            before any write, rather than leaving the plan parked for a
            revision nobody will perform: that silent park is the defect this
            module exists to remove.
    """
    require_reworkable(existing)
    try:
        brief = build_revision_brief(review=existing.review, note=note)
    except ValueError as exc:
        msg = (
            "Say what should change: this plan carries no outstanding review "
            "findings, so a change request with no note has nothing to re-plan "
            "against."
        )
        raise ValidationError(msg) from exc

    # Capability before data: "the org cannot re-plan at all" is a different
    # answer from "this plan has nothing left to re-plan", and resolving the
    # planner first is what keeps a deployment with no planner from reporting
    # the second.
    decomposition = _decomposition(app_state)
    parent = await _objective_task(app_state, existing)
    briefed = parent.model_copy(
        update={"description": f"{parent.description}\n\n{brief}"}
    )
    # Plan AS the initiative's existing owner. Without an identity the
    # agent-session strategy declines to the single-shot fallback decomposer,
    # so a rework would be planned by a different mechanism than the plan it
    # revises: a live rework fell back this way and its three parse retries
    # exhausted against output the fallback prompt could not get as JSON.
    result = await decomposition.decompose_task(
        briefed,
        DecompositionContext(
            owner_identity=await _initiative_owner(app_state, existing),
            available_roles=await _roster(app_state),
        ),
    )
    items = items_from_decomposition(result)
    await reject_unroutable_owners(app_state, items)
    reject_undecidable_graph(items, task_structure=result.plan.task_structure)
    logger.info(
        API_PLAN_CHANGES_REPLANNED,
        plan_id=str(existing.id),
        item_count=len(items),
        has_note=note is not None and bool(note.strip()),
        addressed_review=existing.review is not None,
    )
    return RePlan(
        items=items,
        premises=PlanPremises(
            assumptions=result.plan.assumptions,
            open_questions=result.plan.open_questions,
        ),
    )


def _decomposition(app_state: AppState) -> DecompositionService:
    """Resolve the planner, refusing when none is configured.

    Returns:
        The decomposition service.

    Raises:
        ServiceUnavailableError: No coordinator is wired.
    """
    coordinator = app_state.slice(RuntimeStateSlice).coordinator
    if coordinator is None:
        msg = (
            "No planner is configured, so this change request cannot be "
            "re-planned. Bind coordination.decomposition_model."
        )
        raise ServiceUnavailableError(msg)
    return coordinator.decomposition_service


async def _objective_task(app_state: AppState, plan: Plan) -> Task:
    """Fetch the objective task the plan decomposes.

    Returns:
        The objective task.

    Raises:
        ServiceUnavailableError: The task engine is not running, so the
            objective cannot be read and the org cannot re-plan.
        ConflictError: The objective task no longer exists, so there is
            nothing left to re-plan against.
    """
    engine = app_state.slice(EngineStateSlice).task_engine
    if engine is None:
        msg = "The task engine is not running, so this plan cannot be re-planned"
        raise ServiceUnavailableError(msg)
    parent = await engine.get_task(plan.parent_task_id)
    if parent is None:
        msg = (
            f"Plan {plan.id} names an objective task that no longer exists, "
            "so there is nothing to re-plan against"
        )
        raise ConflictError(msg)
    return parent


async def _initiative_owner(app_state: AppState, plan: Plan) -> AgentIdentity | None:
    """Resolve the agent already accountable for *plan*'s initiative.

    Read from the project's durable lead rather than staffed afresh: the
    initiative already has an owner, and picking a second one would plan the
    revision as somebody who does not own the work.

    Returns:
        The lead's identity, or ``None`` when the project names no lead or the
        lead no longer resolves. ``None`` degrades planning to the single-shot
        decomposer, which is worse but still plans, so it is not worth
        refusing an operator's change request over.
    """
    projects = project_repository_of(app_state)
    registry = app_state.slice(HrStateSlice).agent_registry
    if projects is None or registry is None:
        return None
    project = await projects.get(plan.project)
    if project is None or project.lead is None:
        return None
    return await registry.get(project.lead)


async def _roster(app_state: AppState) -> tuple[NotBlankStr, ...]:
    """Read the roles the org staffs right now.

    Returns:
        The active roster's roles, empty when no registry is wired.
    """
    registry = app_state.slice(HrStateSlice).agent_registry
    if registry is None:
        return ()
    return roster_from_agents(await registry.list_active())
