# module-kind: code
"""Plan-level checks a submitted decomposition has to survive.

Every check here refuses at PARSE time and raises
:class:`~synthorg.engine.errors.DecompositionError`, which is what makes them
correctable in-session: the agent-session strategy's submit tool turns the
refusal into a tool error the planning agent fixes on its next turn, and the
single-shot strategy feeds it back through its retry ladder. The same fault
found at dispatch has already been approved by an operator who was told nothing
was wrong.

Split from :mod:`synthorg.engine.decomposition.llm_parse` on the same seam as
:mod:`synthorg.engine.decomposition.llm_parse_subtask`: that module owns the
per-subtask half, this one owns the whole-plan half, and what remains is the
transport (tool call, JSON content, markdown fence) they are both reached
through.
"""

from synthorg.core.plan_role_validation import describe_unroutable_role
from synthorg.core.plan_validation import (
    ORDERED_STRUCTURES,
    combine_graph_violations,
    describe_structureless_graph,
    describe_undecidable_criteria,
    describe_unstated_references,
)
from synthorg.core.task_enums import TaskStructure
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import SubtaskDefinition
from synthorg.engine.errors import DecompositionError
from synthorg.observability import get_logger
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_LLM_PARSE_ERROR,
)

logger = get_logger(__name__)


def validate_roles(
    subtasks: tuple[SubtaskDefinition, ...],
    available_roles: tuple[NotBlankStr, ...],
) -> None:
    """Reject an owner the org does not staff.

    Sits with the kind/artifact invariant rather than at dispatch, because a
    correctable :class:`DecompositionError` lets the planning session resubmit
    inside the same session, while an unroutable owner discovered at dispatch
    has already been approved by an operator who was told nothing was wrong.

    Args:
        subtasks: The parsed subtasks.
        available_roles: The roles the org staffs; empty skips the check.

    Raises:
        DecompositionError: When an owner names no staffed role.
    """
    for sub in subtasks:
        detail = describe_unroutable_role(
            entity_id=sub.id,
            required_role=sub.required_role,
            available_roles=available_roles,
        )
        if detail is not None:
            logger.warning(DECOMPOSITION_LLM_PARSE_ERROR, error=detail)
            raise DecompositionError(detail)


def validate_coverage(
    subtasks: tuple[SubtaskDefinition, ...],
    objective_criteria: tuple[NotBlankStr, ...],
) -> None:
    """Reject a plan that advances none of the objective it decomposes.

    ``satisfies`` exists so success-criteria coverage can be CHECKED, and until
    now nothing checked it. The prompt states the contract ("Between them, the
    items must cover every objective criterion"), the schema leaves the field
    out of the required list, and its description invites omission per item
    ("Omit only for pure-support items"). A planner that reads every item as
    pure support therefore produces a plan tagged with nothing, which parses
    cleanly, reads correctly, and answers "which of the objective's criteria
    does this plan address?" with silence.

    Observed on a live decomposition of a 42-criterion objective: all seven
    subtasks came back with ``satisfies=[]``, on the same specification where
    an earlier run of the same planner tagged all seven. So it is variance
    rather than a planner that cannot do it, which is worse: it poisons an
    unpredictable subset of plans rather than failing consistently.

    The check is at PLAN level, not item level, because the field's own
    semantics allow a genuine pure-support item to claim nothing. What cannot
    be true is that every item is pure support: then nothing builds the
    objective.

    What counts is an OVERLAP with the objective's own criteria, not a
    non-empty field. ``satisfies`` carries criterion TEXT, so a plan tagging
    every item with a sentence the objective never states advances exactly as
    much as one tagging nothing, while reading as covered: a non-empty test
    turns the rule into a formality any invented string passes.

    FULL coverage is documented and deliberately NOT enforced. Partial coverage
    is still a plan worth having, while zero coverage has no reading at all,
    and putting a rule the planner keeps re-breaking in front of the retry
    ladder is how the em-dash style rule once took 18 of 25 planning calls.

    Args:
        subtasks: The parsed subtasks.
        objective_criteria: The acceptance criteria of the task being
            decomposed. Empty skips the check, because an objective that
            declares no criteria has no coverage to claim.

    Raises:
        DecompositionError: The objective declares criteria and no subtask
            claims any of them.
    """
    if not objective_criteria:
        return
    stated = set(objective_criteria)
    if any(stated.intersection(sub.satisfies) for sub in subtasks):
        return
    claimed = sorted({claim for sub in subtasks for claim in sub.satisfies})
    detail = (
        f"No subtask's 'satisfies' names any of the objective's "
        f"{len(objective_criteria)} acceptance criteria, so this plan "
        f"advances none of the objective and coverage cannot be checked"
        f"{_names(claimed)}. Tag each item with the objective criteria it "
        f"advances, copied verbatim; a genuine pure-support item may claim "
        f"none, but they cannot all be pure support."
    )
    logger.warning(DECOMPOSITION_LLM_PARSE_ERROR, error=detail)
    raise DecompositionError(detail)


def _names(claimed: list[str]) -> str:
    """Say what the plan claimed instead, when it claimed anything.

    A plan tagged with nothing and one tagged with invented criteria are
    different mistakes with different fixes, and the second is the one a
    planner cannot see: its items look tagged. Quoting what it wrote is what
    lets the next turn compare the two lists rather than guess.

    Args:
        claimed: Every criterion the plan claimed, deduplicated and ordered.

    Returns:
        A clause naming the claims, or the empty string when there are none.
    """
    if not claimed:
        return ""
    quoted = ", ".join(repr(claim) for claim in claimed)
    return f"; the plan claims {quoted}, which the objective does not state"


def validate_graph(
    subtasks: tuple[SubtaskDefinition, ...],
    structure: TaskStructure,
) -> None:
    """Reject a plan whose graph contradicts what the plan says about itself.

    Both checks are correctable in-session for the same reason the roster
    check is: the planner can restate the dependencies it meant. Discovered
    at dispatch instead, the plan has already been approved by an operator
    who was shown an ordering that does not exist.

    Args:
        subtasks: The parsed subtasks, in plan order.
        structure: The structure the planner declared.

    Raises:
        DecompositionError: When an ordered structure carries no edges, an
            item names another it declares no dependency on, or an item's own
            gate demands evidence the plan produces after it.
    """
    detail = describe_structureless_graph(
        declared_sequential=structure in ORDERED_STRUCTURES,
        units=subtasks,
    )
    if detail is None:
        # Every violation, not the first. A session that regenerates its whole
        # plan on each rejection cannot converge while it is told one at a
        # time: it resolves the pair it was given and manufactures another. A
        # live run spent all twelve turns that way, seven submissions rejected
        # by this one rule on seven different pairs, and returned no plan.
        detail = combine_graph_violations(
            (
                *describe_unstated_references(subtasks),
                *describe_undecidable_criteria(subtasks),
            )
        )
    if detail is None:
        return
    logger.warning(DECOMPOSITION_LLM_PARSE_ERROR, error=detail)
    raise DecompositionError(detail)


__all__ = ["validate_coverage", "validate_graph", "validate_roles"]
