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
