# module-kind: code
"""Plan-unit invariants shared by the entity, the DTOs and the engine.

The same three rules are enforced at every boundary a plan unit crosses: the
durable ``PlanItem``, the decomposition ``SubTask`` the engine dispatches, the
edit payload the API accepts, and the decision tool an agent calls. They live
here rather than on any one of those so the rule has one wording and one
definition, and so a new boundary inherits it by importing rather than by
reimplementing.

Each reports the way its callers need: two raise (the caller turns the message
into its own typed failure) and the roster check returns a message, because
decomposition makes it a correctable error the planning session resubmits
against while the operator edit path makes it a validation failure.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from synthorg.core.normalization import normalize_identifier
from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.task_enums import TaskStructure
from synthorg.core.types import NotBlankStr


class DecisionOption(Protocol):
    """What the option invariants read off one option.

    Structural rather than the concrete ``PlanOption``: naming the entity
    here would point this module back at the one that imports it, and the
    invariants read only the identity and the recommendation flag.

    Read-only members, so an implementation is free to narrow them (the
    entity's id is a ``NotBlankStr``); a mutable attribute would be invariant
    and reject exactly the callers this exists to serve.
    """

    @property
    def id(self) -> str:
        """Identity of the option within its decision."""
        ...

    @property
    def recommended(self) -> bool:
        """Whether the owner recommends this option."""
        ...


_MIN_DECISION_OPTIONS: Final[int] = 2


def validate_decision_options(
    *,
    entity_id: str,
    kind: PlanItemKind,
    # A ``Sequence`` rather than a ``tuple``: tuple is invariant, so every
    # caller's own concrete option type would be rejected against the
    # structural one.
    options: Sequence[DecisionOption],
    chosen_option_id: str | None = None,
) -> None:
    """Enforce the WORK-vs-DECISION option invariants shared by items/subtasks.

    A ``WORK`` unit carries no options; a ``DECISION`` offers at least two
    options with unique ids and exactly one recommended, and any recorded
    ``chosen_option_id`` must name one of them.

    Raises:
        ValueError: When a work unit carries options, a decision has fewer than
            two options / not exactly one recommended / duplicate option ids, or
            the chosen option is unknown.
    """
    if kind is PlanItemKind.WORK:
        if options or chosen_option_id is not None:
            msg = f"{entity_id!r} is WORK but carries decision options"
            raise ValueError(msg)
        return
    if len(options) < _MIN_DECISION_OPTIONS:
        msg = f"Decision {entity_id!r} must offer at least two options"
        raise ValueError(msg)
    option_ids = [option.id for option in options]
    if len(option_ids) != len(set(option_ids)):
        msg = f"Decision {entity_id!r} has duplicate option ids"
        raise ValueError(msg)
    if sum(option.recommended for option in options) != 1:
        msg = f"Decision {entity_id!r} needs exactly one recommended option"
        raise ValueError(msg)
    if chosen_option_id is not None and chosen_option_id not in option_ids:
        msg = f"Decision {entity_id!r} chose an unknown option"
        raise ValueError(msg)


class PlanUnit(Protocol):
    """What the graph invariants read off one plan unit.

    Structural for the same reason :class:`DecisionOption` is: the durable
    item and the decomposition subtask both satisfy it, and neither module
    may import the other.
    """

    @property
    def id(self) -> str:
        """Identity of the unit within its plan."""
        ...

    @property
    def title(self) -> str:
        """Human title of the unit."""
        ...

    @property
    def description(self) -> str:
        """What the unit covers."""
        ...

    @property
    def dependencies(self) -> tuple[str, ...]:
        """Ids of the units this one waits for."""
        ...


#: A plan of one item has no graph to contradict.
_MIN_ORDERED_UNITS: Final[int] = 2


#: Structures that promise an ordering. Declaring one and then declaring no
#: dependencies leaves dispatch with a graph that says the opposite.
#:
#: Lives beside the check that reads it, because both boundaries that ask the
#: question (decomposition and the operator's own edit) have to agree on what
#: "ordered" means, and two copies of that answer is one rename from
#: disagreeing.
ORDERED_STRUCTURES: Final[frozenset[TaskStructure]] = frozenset(
    {TaskStructure.SEQUENTIAL, TaskStructure.MIXED}
)


def describe_structureless_graph(
    *,
    declared_sequential: bool,
    units: Sequence[PlanUnit],
) -> str | None:
    """Describe a declared ordering that no edge expresses, or ``None``.

    A plan that declares ``SEQUENTIAL`` or ``MIXED`` and then carries zero
    dependency edges contradicts itself: the structure says the work is
    ordered, the graph says every item may start at once, and dispatch
    believes the graph. Six items with a declared ``mixed`` structure and no
    edges at all went out as one wave, in an order nobody chose.

    Reported rather than raised, so decomposition can turn it into a
    correctable error the planning session resubmits against while an
    operator edit path renders it as a validation failure.

    Args:
        declared_sequential: Whether the plan declared an ordered structure
            (``SEQUENTIAL`` or ``MIXED``).
        units: The plan's units, in plan order.

    Returns:
        A message naming the contradiction, or ``None`` when the plan is
        consistent.
    """
    if not declared_sequential or len(units) < _MIN_ORDERED_UNITS:
        return None
    if any(unit.dependencies for unit in units):
        return None
    return (
        f"the plan declares an ordered structure across {len(units)} items but "
        "declares no dependencies at all, so every item would start at once. "
        "Either declare the dependencies that order them, or declare the "
        "structure as parallel"
    )


class GatedPlanUnit(PlanUnit, Protocol):
    """A plan unit plus the two fields its own gate is judged from.

    Separate from :class:`PlanUnit` because the graph invariants above need
    neither: a protocol that demands what its readers do not use rejects
    callers for nothing.
    """

    @property
    def acceptance_criteria(self) -> tuple[str, ...]:
        """What has to be true for this unit to pass its review gate."""
        ...

    @property
    def expected_artifacts(self) -> tuple[str, ...]:
        """The deliverables this unit itself produces."""
        ...


#: Longest extension treated as naming a file. Past this the dot is prose
#: ("the 3.5 second budget"), and matching on it would report a criterion
#: that merely shares a sentence with another item's deliverable.
_MAX_FILE_EXTENSION: Final[int] = 5


def _artifact_filename(artifact: str) -> str | None:
    """Reduce a declared artifact to the filename a criterion would name it by.

    A deliverable is declared either as a path (``src/index.html``) or as
    prose (``a playable game``). Only the first can be matched exactly, and
    only an exact match is worth acting on: prose overlaps whatever the plan
    is about, so a plan of ten items about one game would report ten times.

    Returns:
        The lowercased basename when the artifact names a file, else ``None``.
    """
    basename = normalize_identifier(
        artifact.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    )
    stem, dot, extension = basename.rpartition(".")
    if not dot or not stem or " " in basename:
        return None
    if not extension.isalnum() or len(extension) > _MAX_FILE_EXTENSION:
        return None
    return basename


def _criterion_tokens(unit: GatedPlanUnit) -> frozenset[str]:
    """Return the filename-shaped tokens the unit's own criteria name.

    Splits on everything a path cannot contain, so ``serves index.html with``
    yields ``index.html`` rather than ``index`` and ``html``.

    Returns:
        Case-folded tokens from every acceptance criterion.
    """
    text = " ".join(unit.acceptance_criteria)
    kept = "".join(c if (c.isalnum() or c in "./\\-_") else " " for c in text)
    return frozenset(
        # The same folding the artifact side uses, so the two can be compared
        # at all: one lowercased and the other case-folded would agree on
        # every ASCII filename and disagree on the rest.
        normalize_identifier(
            token.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
        ).strip(".")
        for token in kept.split()
    )


def _dependency_closure(
    unit: GatedPlanUnit, by_id: dict[str, GatedPlanUnit]
) -> frozenset[str]:
    """Return every unit id *unit* transitively waits for.

    The closure rather than the declared edges, because evidence flows the
    whole way down a chain: an item three hops below still runs first.

    Returns:
        The ids reachable from *unit* through dependencies, excluding itself.
    """
    seen: set[str] = set()
    pending = list(unit.dependencies)
    while pending:
        current = pending.pop()
        if current in seen or current == unit.id:
            continue
        seen.add(current)
        upstream = by_id.get(current)
        if upstream is not None:
            pending.extend(upstream.dependencies)
    return frozenset(seen)


def _filenames_of(unit: GatedPlanUnit) -> tuple[str, ...]:
    """Return the artifact filenames *unit* declares it produces.

    Declaration order is kept, and duplicates dropped, so a unit declaring two
    filenames a criterion names is always reported against the same one.

    Args:
        unit: The unit whose declared artifacts are read.

    Returns:
        Every declared artifact that reads as a filename, in declaration order.
    """
    return tuple(
        dict.fromkeys(
            filename
            for artifact in unit.expected_artifacts
            if (filename := _artifact_filename(artifact)) is not None
        )
    )


@dataclass(frozen=True, slots=True)
class _GateIndex:
    """The per-plan facts every gate comparison reads.

    The id map and each unit's declared filenames are the same for every
    comparison, and rebuilding them inside the per-unit loop is what made this
    scan the slowest thing on the plan-edit path once the item cap admitted a
    whole tree rather than one level.
    """

    by_id: dict[str, GatedPlanUnit]
    filenames: dict[str, tuple[str, ...]]


def _gate_index(units: Sequence[GatedPlanUnit]) -> _GateIndex:
    """Derive the gate-scan facts for *units*.

    Returns:
        The units keyed by id, and the declared filenames of each.
    """
    return _GateIndex(
        by_id={unit.id: unit for unit in units},
        filenames={unit.id: _filenames_of(unit) for unit in units},
    )


def _undecidable_criterion(
    *,
    unit: GatedPlanUnit,
    others: Sequence[GatedPlanUnit],
    index: _GateIndex,
) -> str | None:
    """Describe *unit*'s first criterion that its plan cannot yet judge.

    Returns:
        A message naming the item, the artifact and its producer, or ``None``
        when every criterion is decidable where it stands.
    """
    if not unit.acceptance_criteria:
        return None
    named = _criterion_tokens(unit)
    if not named:
        return None
    by_id = index.by_id
    reachable = _dependency_closure(unit, by_id)
    # What arrives in time, gathered BEFORE anything is refused. The question
    # is whether the plan delivers the file by the moment this gate runs, so
    # one unreachable sibling declaring the same filename settles nothing:
    # judging on the first match instead makes the answer depend on the order
    # the units happen to arrive in, and refuses plans whose own dependency
    # produces exactly what the criterion names.
    delivered = set(index.filenames[unit.id])
    for one in others:
        if one.id in reachable and one.id != unit.id:
            delivered.update(index.filenames[one.id])
    for other in others:
        if other.id == unit.id or other.id in reachable:
            continue
        for filename in index.filenames[other.id]:
            if filename in delivered or filename not in named:
                continue
            remedy = (
                "Judge this item on what it produces itself; it cannot wait "
                f"for {other.id!r}, which already waits for it"
                if unit.id in _dependency_closure(other, by_id)
                else "Declare the dependency, or judge this item on what it "
                "produces itself"
            )
            return (
                f"{unit.id!r} has an acceptance criterion naming {filename!r}, "
                f"which {other.id!r} ({other.title!r}) produces and {unit.id!r} "
                "does not wait for, so the criterion is unjudgeable when this "
                f"item is reviewed and stays unjudgeable through every rework. "
                f"{remedy}"
            )
    return None


def describe_undecidable_criterion(
    *,
    unit: GatedPlanUnit,
    others: Sequence[GatedPlanUnit],
) -> str | None:
    """Describe a gate that demands evidence its plan produces later, or ``None``.

    The DAG orders the WORK; it says nothing about whether the EVIDENCE each
    gate demands exists by the time that gate runs. An item whose criterion
    names a file a downstream item produces cannot pass at the moment it is
    judged, and cannot pass on any rework either: the task reruns, the file
    still does not exist, and the reviewer refuses again for as long as the
    plan stands.

    Matching is on declared artifact filenames only, so it fires on a plan
    naming its own deliverables rather than on shared subject vocabulary.

    Args:
        unit: The unit whose criteria are being checked.
        others: Every unit in the plan, *unit* included or not.

    Returns:
        A message naming the item, the artifact and its producer, or ``None``
        when every criterion is decidable where it stands.
    """
    # *unit* last, so it wins the id map exactly as the caller's own entry did.
    return _undecidable_criterion(
        unit=unit, others=others, index=_gate_index((*others, unit))
    )


def describe_undecidable_criteria(units: Sequence[GatedPlanUnit]) -> tuple[str, ...]:
    """Describe every gate demanding evidence its plan produces later.

    The index is built once for the whole plan and read by every comparison;
    see :class:`_GateIndex` for why that is not an optimisation detail.

    Returns:
        A message per offending unit, in plan order; empty when every gate is
        judgeable where it stands.
    """
    index = _gate_index(units)
    return tuple(
        message
        for unit in units
        if (message := _undecidable_criterion(unit=unit, others=units, index=index))
        is not None
    )


def combine_graph_violations(messages: Sequence[str]) -> str | None:
    """Fold every graph violation into the one detail a caller raises.

    A planning session that regenerates its whole plan on each rejection cannot
    converge while it is told about one violation at a time: it resolves the
    pair it was given and manufactures another. So the count leads, and the
    wording says plainly that fixing one is not enough. A lone violation reads
    exactly as it did before there was a plural form, because every existing
    caller and test asserts that wording.

    Returns:
        The single message unchanged, a numbered list when there are several,
        or ``None`` when the graph is clean.
    """
    if not messages:
        return None
    if len(messages) == 1:
        return messages[0]
    numbered = " ".join(
        f"({index}) {message}." for index, message in enumerate(messages, start=1)
    )
    return (
        f"The plan has {len(messages)} problems and they must all be fixed in "
        f"the next submission, not one at a time: {numbered}"
    )


def validate_expected_artifacts(
    *,
    entity_id: str,
    kind: PlanItemKind,
    expected_artifacts: tuple[NotBlankStr, ...],
) -> None:
    """Enforce that a WORK unit declares a deliverable and a DECISION does not.

    The two fail-loud zero-artifact guards (the loop's ``NO_OP``
    reclassification and the post-execution transition) both key off the
    dispatched task's ``artifacts_expected``, so a WORK unit declaring none
    disarms both and a chat-text-only run reaches review as a silent success.
    Requiring one deliverable per WORK unit arms them structurally, mirroring
    the non-empty ``acceptance_criteria`` invariant beside it.

    A ``DECISION`` never dispatches, so a deliverable on one means the unit was
    typed wrong: the coverage map would expect an artifact no task will produce.

    Args:
        entity_id: Identifier of the plan item / subtask, for the message.
        kind: Whether the unit is executed work or a recorded decision.
        expected_artifacts: The declared deliverables.

    Raises:
        ValueError: When a WORK unit declares no deliverable, or a DECISION
            declares one.
    """
    if kind is PlanItemKind.WORK:
        if not expected_artifacts:
            msg = (
                f"{entity_id!r} is WORK and must declare at least one expected "
                "artifact, so the zero-artifact guard engages when it runs"
            )
            raise ValueError(msg)
        return
    if expected_artifacts:
        msg = f"{entity_id!r} is a DECISION and declares expected artifacts"
        raise ValueError(msg)
