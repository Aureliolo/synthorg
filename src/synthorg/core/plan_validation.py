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

from synthorg.core.plan_enums import PlanItemKind
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


def describe_unstated_reference(
    *,
    unit: PlanUnit,
    others: Sequence[PlanUnit],
) -> str | None:
    """Describe an item that names another it does not depend on, or ``None``.

    "Integrate game loop: tie engine, renderer, and input together" cannot
    precede the three items it names, but with no declared dependency the
    dispatcher is free to run it first, and did.

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
        if other.id == unit.id or other.id in unit.dependencies:
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
