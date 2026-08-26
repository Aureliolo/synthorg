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

from synthorg.core.criterion_match import describe_unnamed_claims, matched_criteria
from synthorg.core.plan_reference_validation import describe_unstated_references
from synthorg.core.plan_role_validation import describe_unroutable_role
from synthorg.core.plan_validation import (
    ORDERED_STRUCTURES,
    combine_graph_violations,
    describe_structureless_graph,
    describe_undecidable_criteria,
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
    """Reject a plan whose ``satisfies`` cannot be read against the objective.

    Two rules, and they are different claims about the same field.

    **The plan advances something.** ``satisfies`` exists so success-criteria
    coverage can be CHECKED, and for a while nothing checked it. The prompt
    states the contract, the schema leaves the field out of the required list,
    and its description invites omission per item ("Omit only for pure-support
    items"). A planner that reads every item as pure support therefore produces
    a plan tagged with nothing, which parses cleanly, reads correctly, and
    answers "which of the objective's criteria does this plan address?" with
    silence. Observed on a live decomposition of a 42-criterion objective: all
    seven subtasks came back with ``satisfies=[]``, on the same specification
    where an earlier run of the same planner tagged all seven. So it is
    variance rather than a planner that cannot do it, which is worse: it
    poisons an unpredictable subset of plans rather than failing consistently.
    That rule is at PLAN level, because the field's own semantics allow a
    genuine pure-support item to claim nothing; what cannot be true is that
    every item is, since then nothing builds the objective.

    **Each claim names something.** A claim carrying a sentence the objective
    never states reads as coverage on every surface that shows the field and is
    coverage to none of them. A recorded sweep dropped 143 such claims at
    scoring time, which deflated the ratio it was measuring at both ends; the
    boundary that WRITES a claim is where that has a fix, because here the
    planning session still has a turn left to correct it. An item may still
    claim NOTHING: that rule is about invention, not omission.

    FULL coverage is documented and deliberately NOT enforced. Partial coverage
    is still a plan worth having, while zero coverage has no reading at all,
    and putting a rule the planner keeps re-breaking in front of the retry
    ladder is how the em-dash style rule once took 18 of 25 planning calls.
    Naming a criterion verbatim is a different order of demand: the list to
    copy from is in the message, and the refusal quotes it back.

    Both violations are reported together, for the reason
    :func:`validate_graph` records: a session that regenerates its whole plan
    on each rejection cannot converge while it is told one at a time.

    Args:
        subtasks: The parsed subtasks.
        objective_criteria: The criteria this level is answerable for. Empty
            skips both rules, because an objective that declares no criteria
            has no coverage to claim and neither has a subtree whose parent
            claimed none.

    Raises:
        DecompositionError: The level declares criteria and either no subtask
            claims any of them, or a subtask claims one they do not include.
    """
    if not objective_criteria:
        return
    detail = combine_graph_violations(
        (
            *_uncovered_objective(subtasks, objective_criteria),
            *describe_unnamed_claims(subtasks, objective=objective_criteria),
        )
    )
    if detail is None:
        return
    logger.warning(DECOMPOSITION_LLM_PARSE_ERROR, error=detail)
    raise DecompositionError(detail)


def _uncovered_objective(
    subtasks: tuple[SubtaskDefinition, ...],
    objective_criteria: tuple[NotBlankStr, ...],
) -> tuple[str, ...]:
    """Describe a plan whose every item is pure support.

    Args:
        subtasks: The parsed subtasks.
        objective_criteria: The criteria this level is answerable for.

    Returns:
        One message, or empty when at least one item advances the objective.
    """
    if any(
        matched_criteria(sub.satisfies, objective=objective_criteria)
        for sub in subtasks
    ):
        return ()
    detail = (
        f"No subtask's 'satisfies' names any of the objective's "
        f"{len(objective_criteria)} acceptance criteria, so this plan "
        f"advances none of the objective and coverage cannot be checked. Tag "
        f"each item with the objective criteria it advances, copied verbatim; "
        f"a genuine pure-support item may claim none, but they cannot all be "
        f"pure support"
    )
    return (detail,)


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
