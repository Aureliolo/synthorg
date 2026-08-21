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


def describe_unroutable_role(
    *,
    entity_id: str,
    required_role: str | None,
    available_roles: tuple[NotBlankStr, ...],
) -> str | None:
    """Describe why an owning role cannot be routed, or ``None`` when it can.

    A plan item's owner is the role a dispatch looks up, so an invented one
    (the near-miss "Backend Engineer" for an org staffing "Backend Developer")
    produces an item with nobody behind it, discovered at dispatch if at all.

    Returns a message rather than raising, because the same judgement is made
    at two boundaries that report differently: decomposition turns it into a
    correctable ``DecompositionError`` the planning session can resubmit
    against, and the operator edit path into a validation failure. One
    wording, two reports.

    An empty roster means "no roster known" and passes: an org with no agents
    has nothing to check against, and failing there would block a greenlight
    for a reason unrelated to the plan.

    Args:
        entity_id: Identifier of the plan item / subtask, for the message.
        required_role: The declared owner, or ``None`` when unowned.
        available_roles: The roles the org actually staffs.

    Returns:
        A message naming the offending role and the valid set, or ``None``.
    """
    if not available_roles or required_role is None:
        return None
    if required_role in available_roles:
        return None
    valid = ", ".join(sorted(available_roles))
    return (
        f"{entity_id!r} names required_role {required_role!r}, which no agent "
        f"holds, so the item cannot be routed. Available roles: {valid}"
    )


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


#: Words too common to constitute a reference to another item. A title token
#: only implicates another item when it names that item's subject, and every
#: plan is full of these.
_GENERIC_TITLE_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "add",
        "an",
        "and",
        "build",
        "create",
        "for",
        "implement",
        "in",
        "of",
        "on",
        "set",
        "setup",
        "the",
        "to",
        "up",
        "with",
        "write",
    }
)

#: Shortest token that can identify another item's subject. Anything shorter
#: is a preposition or an abbreviation shared across unrelated items.
_MIN_REFERENCE_TOKEN: Final[int] = 4

#: Largest share of a plan's titles a token may appear in and still name one
#: item; the bound is inclusive, so a token in exactly half the titles is kept.
#: A word carried by MORE than half the plan is that plan's house vocabulary
#: ("Subtask 1" / "Subtask 2", "Service layer" / "Service tests"), and treating
#: it as a reference would make every item depend on every other.
_MAX_DISTINCTIVE_SHARE: Final[float] = 0.5

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


def _words(text: str) -> list[str]:
    """Split *text* into lowercased alphanumeric words.

    Returns:
        The words, in order, with punctuation dropped.
    """
    return "".join(c if c.isalnum() else " " for c in text.lower()).split()


def _title_tokens(unit: PlanUnit) -> frozenset[str]:
    """Reduce a unit's title to the tokens that could name its subject.

    Returns:
        Lowercased alphanumeric title tokens, minus the generic verbs and
        articles every plan title carries.
    """
    return frozenset(
        word
        for word in _words(unit.title)
        if len(word) >= _MIN_REFERENCE_TOKEN and word not in _GENERIC_TITLE_TOKENS
    )


def _distinctive_tokens(
    other: PlanUnit,
    *,
    plan_titles: Sequence[frozenset[str]],
) -> frozenset[str]:
    """Return the tokens of *other* that name it rather than the whole plan.

    A token carried by more than half the plan's titles is that plan's house
    vocabulary, not a reference: matching on it made "Subtask 2" a reference
    to "Subtask 1", and would make every item in a plan named after one
    subject depend on every other.

    Returns:
        The subset of *other*'s title tokens rare enough to identify it, empty
        when none are.
    """
    ceiling = len(plan_titles) * _MAX_DISTINCTIVE_SHARE
    return frozenset(
        token
        for token in _title_tokens(other)
        if sum(token in title for title in plan_titles) <= ceiling
    )


def _ordered(unit: PlanUnit, other: PlanUnit) -> bool:
    """Whether the plan already orders *unit* against *other*, either way.

    The hazard this module reports is a reference that leaves the referring
    item free to be dispatched FIRST. An edge in either direction removes it:
    ``unit`` depending on ``other`` is the obvious case, and ``other``
    depending on ``unit`` puts ``unit`` first by construction, which is what a
    forward reference ("emits the tokens the parser consumes") describes.

    Reading only the first direction turned a lexer that mentioned its parser
    into a demand for a lexer-depends-on-parser edge, closing a cycle the
    validator rejects on the next submission: a plan that could be corrected
    only by rewording, told to add a dependency instead, until the retries ran
    out.

    Returns:
        True when either item declares a dependency on the other.
    """
    return other.id in unit.dependencies or unit.id in other.dependencies


def describe_unstated_reference(
    *,
    unit: PlanUnit,
    others: Sequence[PlanUnit],
) -> str | None:
    """Describe an item naming another the plan does not order it against.

    "Integrate game loop: tie engine, renderer, and input together" cannot
    precede the three items it names, but with no declared dependency the
    dispatcher is free to run it first, and did. An edge in either direction
    settles the order and clears the reference; see :func:`_ordered`.

    Matching is on whole words, and only on the other item's DISTINCTIVE title
    tokens, so it fires on a genuine reference rather than on the vocabulary a
    plan happens to repeat.

    Args:
        unit: The unit being checked.
        others: Every other unit in the plan.

    Returns:
        A message naming both items, or ``None`` when no unstated reference
        is found.
    """
    plan_titles = [_title_tokens(one) for one in (unit, *others)]
    text = frozenset(_words(f"{unit.title} {unit.description}"))
    for other in others:
        if other.id == unit.id or _ordered(unit, other):
            continue
        tokens = _distinctive_tokens(other, plan_titles=plan_titles)
        if tokens and tokens <= text:
            return (
                f"{unit.id!r} names {other.id!r} ({other.title!r}) in its own "
                "title or description but declares no dependency on it, so it "
                "may be dispatched first. Declare the dependency, or reword it "
                "if the items are genuinely independent"
            )
    return None


def describe_unstated_references(units: Sequence[PlanUnit]) -> tuple[str, ...]:
    """Describe every item naming another it does not depend on.

    One message per offending unit, because the plural caller's job is to hand
    the planner a complete list rather than the first thing that went wrong.

    Returns:
        A message per offending unit, in plan order; empty when the graph is
        clean.
    """
    return tuple(
        message
        for unit in units
        if (message := describe_unstated_reference(unit=unit, others=units)) is not None
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


def _filenames_of(unit: GatedPlanUnit) -> set[str]:
    """Return the artifact filenames *unit* declares it produces.

    Args:
        unit: The unit whose declared artifacts are read.

    Returns:
        Every declared artifact that reads as a filename.
    """
    return {
        filename
        for artifact in unit.expected_artifacts
        if (filename := _artifact_filename(artifact)) is not None
    }


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
    if not unit.acceptance_criteria:
        return None
    named = _criterion_tokens(unit)
    if not named:
        return None
    by_id = {one.id: one for one in others}
    by_id[unit.id] = unit
    reachable = _dependency_closure(unit, by_id)
    # What arrives in time, gathered BEFORE anything is refused. The question
    # is whether the plan delivers the file by the moment this gate runs, so
    # one unreachable sibling declaring the same filename settles nothing:
    # judging on the first match instead makes the answer depend on the order
    # the units happen to arrive in, and refuses plans whose own dependency
    # produces exactly what the criterion names.
    delivered = _filenames_of(unit)
    for one in others:
        if one.id in reachable and one.id != unit.id:
            delivered |= _filenames_of(one)
    for other in others:
        if other.id == unit.id or other.id in reachable:
            continue
        for artifact in other.expected_artifacts:
            filename = _artifact_filename(artifact)
            if filename is None or filename in delivered or filename not in named:
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


def describe_undecidable_criteria(units: Sequence[GatedPlanUnit]) -> tuple[str, ...]:
    """Describe every gate demanding evidence its plan produces later.

    Returns:
        A message per offending unit, in plan order; empty when every gate is
        judgeable where it stands.
    """
    return tuple(
        message
        for unit in units
        if (message := describe_undecidable_criterion(unit=unit, others=units))
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
