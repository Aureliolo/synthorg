# module-kind: code
"""Build the decomposition tree one run is executed from.

The sweep manipulates one variable, ``max_depth``, and the shape below it is
the planner's own. What the harness fixes is the rule that decides when a
subtask is oversized, and it fixes it so ONE rule binds: a unit still covering
more than one spec requirement splits again. Left at the shipped thresholds,
the artifact and criterion counts would also fire and every unit at every depth
would be oversized, which reaches the cap every time but says nothing about
sizing; opened all the way, nothing would ever split and the sweep would have
no depth to measure. Between them, the requirement floor is what makes "depth
reached" a property of the specification rather than of a threshold.

The depth a tree ACHIEVES is what the report plots. The cap is what a run was
allowed, and a planner that stops splitting at three produces identical trees at
caps four, five and six.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from evals.errors import OracleUnusableError, RecursionDepthPlannerSubstitutedError
from evals.recursion_depth.claims import RequirementId, criterion_for
from evals.recursion_depth.oracle import (
    declared,
    entry_field,
    load_index,
    requirement_entries,
)
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.models import DecompositionResult, SubtaskDefinition
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.observability import get_logger
from synthorg.observability.events.evals import EVALS_RECURSION_TREE_BUILT
from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

#: Opened to their ceilings so the requirement floor is the one rule that
#: decides a split. See the module docstring: this is the manipulation, not a
#: tuning choice, and it is written down here rather than in a YAML an operator
#: might read as a recommendation.
_OPEN_ARTIFACT_THRESHOLD: Final[str] = "20"
_OPEN_CRITERIA_THRESHOLD: Final[str] = "25"

#: Wall-clock ceiling on one planning session, raised well above the product
#: default of 600s.
#:
#: The default is sized for a model that answers directly. Every model a sweep
#: is worth running against reasons first, and a reasoning model's planning
#: turn is slow in proportion to the output budget it is given. On a
#: development run of this harness (not a committed recording, so the figures
#: below are an observation rather than a result anyone can re-read) a pair of
#: runs decomposed the SAME brief in 310s and in over 600s, so at the default
#: one arm completed and the other was killed mid-plan and recorded as an
#: unavailable cell. Losing a whole arm to a timing margin is worse than
#: waiting, because the arm is the comparison the sweep exists to make.
#:
#: This is a bound, not a budget: a planner that finishes sooner costs nothing
#: extra, and the ceiling still exists to stop an unbounded wait on a provider
#: that never answers.
_PLANNING_TIMEOUT_SECONDS: Final[str] = "2400.0"

#: Decomposition self-correction attempts, raised above the product default.
#:
#: A cell whose decomposition fails produces NO tree, and a sweep compares arms
#: pairwise, so one failed plan destroys the comparison the run exists to make
#: rather than costing it a data point. That asymmetry is why the sweep buys
#: more attempts than a production initiative would.
#:
#: Observed on a development run of this harness, not on a committed
#: recording: a plan was refused three times for three DIFFERENT faults (a
#: missing `title`, then a `satisfies` field of the wrong type, then an
#: em-dash the house style bans) while its sibling arm planned cleanly. Each
#: attempt corrected the previous fault, so the planner was converging and
#: simply ran out of budget at the shipped default of two.
_PLANNING_MAX_RETRIES: Final[str] = "6"

#: How many subtasks one level may produce. Above the corroborated 11-to-25
#: coherent-unit ceiling there is no evidence a planner can hold a level
#: together at all, so a level is bounded well inside it and the sweep buys its
#: breadth through depth, which is the variable under test.
_MAX_SUBTASKS: Final[int] = 8


@dataclass(frozen=True)
class SpecBrief:
    """What the planner is told about the specification.

    Attributes:
        spec_id: The specification's identifier.
        title: Its one-line title.
        prose: The whole brief, verbatim.
        requirement_ids: Every requirement id, which is the vocabulary a unit
            claims from.
        titles: Each requirement id mapped to its one-line title, so a unit's
            brief can state what it is answerable for without the agent having
            to find its own claims in forty-odd pages of specification.
    """

    spec_id: str
    title: str
    prose: str
    requirement_ids: tuple[RequirementId, ...]
    titles: Mapping[RequirementId, str]


def load_spec_brief(spec_dir: Path) -> SpecBrief:
    """Read the specification an agent is given.

    The held-out oracle is NOT read here and its node ids never leave
    :mod:`evals.recursion_depth.oracle`: an agent told which test decides a
    requirement builds to the test.

    Args:
        spec_dir: The specification directory.

    Returns:
        The brief.

    Raises:
        OracleUnusableError: The spec's index is malformed. Every read here
            answers with that one type, because the runner decides
            systemic-versus-cell from it: a spec that cannot be read is true of
            every remaining cell, while a bare ``KeyError`` from one unguarded
            key would be filed as a single opaque cell failure and the sweep
            would grind through the rest measuring nothing.
    """
    index = load_index(spec_dir)
    entries = requirement_entries(index, spec_dir=spec_dir)
    prose = (spec_dir / str(declared(index, "brief", spec_dir=spec_dir))).read_text(
        encoding="utf-8"
    )
    ids = tuple(
        RequirementId(entry_field(entry, "id", spec_dir=spec_dir)) for entry in entries
    )
    return SpecBrief(
        spec_id=str(declared(index, "spec_id", spec_dir=spec_dir)),
        title=str(declared(index, "title", spec_dir=spec_dir)),
        prose=prose,
        requirement_ids=ids,
        # Wrapped, because `frozen=True` freezes the ATTRIBUTE and not the
        # dict behind it: the specification is what every unit is judged
        # against, and a holder of this reference could otherwise edit the
        # requirement a leaf was briefed on after the brief was written.
        titles=MappingProxyType(
            {
                RequirementId(entry_field(entry, "id", spec_dir=spec_dir)): entry_field(
                    entry, "title", spec_dir=spec_dir
                )
                for entry in entries
            }
        ),
    )


async def arm_recursion(settings: SettingsService, *, enabled: bool) -> None:
    """Put the decomposition service into the shape this sweep measures.

    Written through the real settings service rather than handed to the
    decomposition service directly, so the sweep exercises the live read the
    product does: an operator enabling recursion applies to the next
    decomposition, and a harness that bypassed that would be measuring a code
    path the deployment does not take.

    Args:
        settings: The booted application's settings service.
        enabled: Whether an oversized subtask is decomposed again.
    """
    await settings.set(
        "coordination",
        "recursive_decomposition_enabled",
        "true" if enabled else "false",
    )
    await settings.set(
        "coordination", "leaf_subtask_threshold", _OPEN_ARTIFACT_THRESHOLD
    )
    await settings.set("coordination", "subtask_max_criteria", _OPEN_CRITERIA_THRESHOLD)
    await settings.set(
        "coordination", "decomposition_timeout_seconds", _PLANNING_TIMEOUT_SECONDS
    )
    await settings.set(
        "coordination", "decomposition_max_retries", _PLANNING_MAX_RETRIES
    )


def objective_task(brief: SpecBrief, *, project: str, created_by: str) -> Task:
    """Build the root task the whole run is decomposed from.

    Args:
        brief: The specification.
        project: The project the run is attributed to.
        created_by: Who filed it.

    Returns:
        The root :class:`Task`.
    """
    return Task(
        id=_root_task_id(brief),
        title=NotBlankStr(brief.title),
        description=NotBlankStr(brief.prose),
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project=NotBlankStr(project),
        created_by=NotBlankStr(created_by),
        status=TaskStatus.CREATED,
        acceptance_criteria=tuple(
            AcceptanceCriterion(description=NotBlankStr(criterion_for(identifier)))
            for identifier in brief.requirement_ids
        ),
    )


def _root_task_id(brief: SpecBrief) -> UUID:
    """Derive the root task id from the specification.

    Returns:
        A stable UUID, so two runs of one spec are attributable to one root.
    """
    return uuid5(NAMESPACE_URL, f"recursion-depth-{brief.spec_id}")


async def build_tree(
    *,
    service: DecompositionService,
    task: Task,
    depth_cap: int,
    workspace_summary: str,
    available_roles: tuple[NotBlankStr, ...],
    owner: AgentIdentity,
) -> DecompositionResult:
    """Decompose *task* down to the cap and return the whole tree.

    Args:
        service: The decomposition service, with recursion already armed.
        task: The root objective.
        depth_cap: The ``max_depth`` this run is allowed.
        workspace_summary: What the workspace holds, so the planner plans
            against the tree rather than against an imagined one.
        available_roles: The roles the roster staffs.
        owner: Who the planning session runs AS. Required rather than
            optional, because the shipped strategy plans as an owner and
            falls back to the single-shot one when it has none: passing
            nothing here does not fail, it quietly measures a different
            planner than the one this experiment is about.

    Returns:
        The decomposition tree.
    """
    result = await service.decompose_task(
        task,
        DecompositionContext(
            max_subtasks=_MAX_SUBTASKS,
            max_depth=depth_cap,
            workspace_summary=workspace_summary,
            available_roles=available_roles,
            owner_identity=owner,
        ),
    )
    _refuse_substituted_planner(result)
    logger.info(
        EVALS_RECURSION_TREE_BUILT,
        depth_cap=depth_cap,
        achieved_depth=result.max_depth_reached,
        leaf_count=len(result.leaf_tasks),
        node_count=len(result.all_tasks),
    )
    return result


def _refuse_substituted_planner(result: DecompositionResult) -> None:
    """Refuse a tree any node of which a substitute planner produced.

    The experiment's premise is that what recursion does to a plan HERE is what
    it does in the product, so the plan has to come from the shipped planner.
    The substitution is silent by design everywhere else: the strategy logs it
    and carries on, because a product that cannot plan as an owner is better
    off with a single-shot plan than with nothing. A measurement is the one
    caller for which that trade is wrong, and it went unnoticed through two
    live recordings.

    Checked per node rather than at the root, because recursion plans each
    level in its own session and only the levels that failed to staff an owner
    substitute; a tree can be part researched and part single-shot, which is
    the shape hardest to notice and the least defensible to plot.

    Args:
        result: The tree.

    Raises:
        RecursionDepthPlannerSubstitutedError: A node names a substitute.
    """
    nodes = _nodes(result)
    substituted = sorted(
        {
            str(node.plan.planning_strategy)
            for node in nodes
            if node.plan.planning_strategy is not None
        }
    )
    if not substituted:
        return
    msg = (
        f"the plan was produced by a substitute planner "
        f"({', '.join(substituted)}) rather than the shipped one, so this "
        f"cell would measure the fallback; staff an owner the planning "
        f"session can run as"
    )
    # Every node here planned and was billed before the substitution was
    # noticed, so the refusal carries what the discarded tree cost.
    raise RecursionDepthPlannerSubstitutedError(msg, sessions=len(nodes))


def _nodes(result: DecompositionResult) -> tuple[DecompositionResult, ...]:
    """Every node of the tree, this level first.

    Returns:
        The nodes.
    """
    return (result, *(node for child in result.children for node in _nodes(child)))


def unit_definitions(
    result: DecompositionResult,
) -> dict[str, SubtaskDefinition]:
    """Map every task in the tree to the planner definition it came from.

    The definition is where a unit's ``satisfies`` claims live: they do not
    travel onto the executable task, and the claims are the survival metric's
    whole vocabulary.

    Keyed on the id rather than zipped by position. A child task's id IS its
    definition's id, and :class:`DecompositionResult` refuses a level where the
    two sets differ, so the id is a bijection WITHIN a level while the ORDER is
    only a property of how the service happens to build the list. Pairing by
    position would attribute one unit's claims to another the day that changes,
    silently and with nothing to notice.

    Across levels the guarantee is a different one and worth naming, because
    this flattens several planning sessions into one map. Ids are canonical
    UUIDs, minted per session and rejected by
    :func:`synthorg.engine.decomposition._ids.subtask_uuid` if they are
    anything else, so a repeat is not a realistic outcome. It is still asserted
    rather than assumed: ``dict`` update is silent on a collision, and the
    consequence would be one unit's ``satisfies`` claims scored against another
    unit's delivery, which is the survival metric computed on the wrong
    denominator with nothing anywhere to notice.

    Args:
        result: The decomposition tree.

    Returns:
        Task id (as a string) to its definition, for every level.

    Raises:
        OracleUnusableError: Two levels minted the same subtask id, so the
            claims cannot be attributed and no measurement taken from them
            would mean anything.
    """
    pairs: dict[str, SubtaskDefinition] = {}
    for node in merge_nodes(result):
        for definition in node.plan.subtasks:
            if definition.id in pairs:
                msg = (
                    f"subtask id {definition.id!r} appears at more than one level "
                    f"of the tree, so its claims cannot be attributed to a unit"
                )
                raise OracleUnusableError(msg)
            pairs[definition.id] = definition
    return pairs


def merge_nodes(result: DecompositionResult) -> tuple[DecompositionResult, ...]:
    """Every level of the tree, deepest first.

    Deepest first because a merge assembles what is already built: a parent
    cannot be integrated before its children exist, and this order is what
    makes the run a single pass rather than a scheduler.

    Args:
        result: The decomposition tree.

    Returns:
        Each level, children before their parent.
    """
    below = tuple(node for child in result.children for node in merge_nodes(child))
    return (*below, result)


__all__ = [
    "SpecBrief",
    "arm_recursion",
    "build_tree",
    "load_spec_brief",
    "merge_nodes",
    "objective_task",
    "unit_definitions",
]
