# module-kind: code
"""Strategic-lens assignment for meeting participants.

A lens distributes distinct viewpoints across a meeting's participants.
It is an enhancement rather than a requirement, so every failure mode
here degrades to "no lenses" and logs why: an assigner that raises, one
that returns a mapping whose keys do not match the participants, and one
that returns a value which is not a non-empty string.

The mapping is rejected whole rather than per entry because a partial one
is indistinguishable from a deliberate one downstream: consumers look each
participant up by their own id and skip anyone absent, so a mapping that
lost or renamed keys silently drops those participants' lenses instead of
reporting anything. Rejecting it turns that into one logged failure.

Both dependencies are typed structurally rather than imported, which
keeps the meeting package clear of an import cycle with the
lens-strategy package.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meeting import (
    MEETING_LENS_ASSIGNMENT_FAILED,
)

logger = get_logger(__name__)

#: Sentinel count reported when the assigner returned no mapping at all,
#: distinguishing that from a mapping that happened to be empty.
_NO_MAPPING: int = -1


@runtime_checkable
class LensStrategyConfig(Protocol):
    """Minimal view of the lens-strategy config."""

    @property
    def default_lenses(self) -> tuple[str, ...]:
        """The configured default lens collection."""
        ...


@runtime_checkable
class LensAssigner(Protocol):
    """Minimal view of the lens assigner."""

    def assign(
        self,
        participant_ids: tuple[str, ...],
        available_lenses: tuple[str, ...],
    ) -> dict[str, str]:
        """Assign a lens to each participant."""
        ...


def compute_lens_assignments(
    participant_ids: tuple[str, ...],
    *,
    assigner: LensAssigner | None,
    strategy_config: LensStrategyConfig | None,
) -> dict[str, str] | None:
    """Assign a strategic lens to each participant.

    Args:
        participant_ids: The meeting's participants.
        assigner: Assignment strategy. ``None`` disables lenses.
        strategy_config: Source of the available lenses. ``None``
            disables lenses.

    Returns:
        A mapping of participant id to lens, or ``None`` when lenses are
        disabled or the assigner produced an unusable answer.
    """
    if assigner is None or strategy_config is None:
        return None
    try:
        result: dict[str, str] = assigner.assign(
            participant_ids,
            strategy_config.default_lenses,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            MEETING_LENS_ASSIGNMENT_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None

    expected_ids = set(participant_ids)
    if not isinstance(result, dict) or set(result.keys()) != expected_ids:
        actual = set(result.keys()) if isinstance(result, dict) else set()
        logger.warning(
            MEETING_LENS_ASSIGNMENT_FAILED,
            error="Lens assigner returned mapping with mismatched keys",
            expected_count=len(expected_ids),
            actual_count=len(result) if isinstance(result, dict) else _NO_MAPPING,
            missing_participants=sorted(expected_ids - actual),
            unexpected_participants=sorted(actual - expected_ids),
        )
        return None
    invalid = sorted(
        participant
        for participant, lens in result.items()
        if not (isinstance(lens, str) and lens)
    )
    if invalid:
        logger.warning(
            MEETING_LENS_ASSIGNMENT_FAILED,
            error="Lens assigner returned non-string or empty lens value",
            invalid_participants=invalid,
        )
        return None

    return dict(result)
