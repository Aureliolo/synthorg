"""Shared helpers for ceremony scheduling strategies.

Extracts common config resolution, validation, and trigger evaluation
logic used by multiple strategy implementations.
"""

from collections.abc import Mapping
from typing import cast

from synthorg.communication.meeting.frequency import (
    MeetingFrequency,
    frequency_to_seconds,
)
from synthorg.engine.workflow.ceremony_context import CeremonyEvalContext
from synthorg.engine.workflow.ceremony_policy import (
    TRIGGER_EVERY_N,
    TRIGGER_SPRINT_END,
    TRIGGER_SPRINT_MIDPOINT,
    TRIGGER_SPRINT_PERCENTAGE,
    TRIGGER_SPRINT_START,
)
from synthorg.engine.workflow.sprint_config import (
    SprintCeremonyConfig,
    SprintConfig,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import SPRINT_CEREMONY_SKIPPED

logger = get_logger(__name__)

# -- Shared constants -------------------------------------------------------

KEY_DURATION_DAYS = "duration_days"
KEY_EVERY_N = "every_n_completions"
KEY_FREQUENCY = "frequency"
KEY_SPRINT_PERCENTAGE = "sprint_percentage"
KEY_TRIGGER = "trigger"

SECONDS_PER_DAY: float = 86_400.0
MIN_DURATION_DAYS: int = 1
MAX_DURATION_DAYS: int = 90
DEFAULT_EVERY_N: int = 5
DEFAULT_SPRINT_PCT: float = 50.0
MAX_SPRINT_PCT: float = 100.0
DEFAULT_TRANSITION_THRESHOLD: float = 1.0
MIDPOINT_THRESHOLD: float = 0.5

VALID_TRIGGERS: frozenset[str] = frozenset(
    {
        TRIGGER_SPRINT_START,
        TRIGGER_SPRINT_END,
        TRIGGER_SPRINT_MIDPOINT,
        TRIGGER_EVERY_N,
        TRIGGER_SPRINT_PERCENTAGE,
    }
)

VALID_FREQUENCIES: frozenset[str] = frozenset(m.value for m in MeetingFrequency)

# Triggers whose condition stays permanently true once crossed (percentage-
# based).  Unlike ``every_n_completions`` (which resets via
# ``completions_since_last_trigger``), these need interval-based suppression
# to prevent infinite re-firing in the hybrid strategy.
STICKY_TRIGGERS: frozenset[str] = frozenset(
    {
        TRIGGER_SPRINT_END,
        TRIGGER_SPRINT_MIDPOINT,
        TRIGGER_SPRINT_PERCENTAGE,
    }
)


# -- Config resolution ------------------------------------------------------


def get_ceremony_config(
    ceremony: SprintCeremonyConfig,
) -> Mapping[str, object]:
    """Extract strategy config from a ceremony's policy override.

    Returns:
        The ``strategy_config`` mapping from ``ceremony.policy_override``
        when set, or an empty mapping when no override is present.
    """
    if ceremony.policy_override is None:
        return {}
    return ceremony.policy_override.strategy_config or {}


def resolve_interval(
    ceremony: SprintCeremonyConfig,
    strategy_name: str,
) -> float | None:
    """Resolve the firing interval in seconds.

    Priority: ``ceremony.frequency`` > ``strategy_config["frequency"]``.
    Logs a warning when a configured frequency string is invalid.

    Returns:
        The interval in seconds when a valid frequency is configured;
        ``None`` when no frequency is set or the configured value is
        invalid (with a warning log).
    """
    if ceremony.frequency is not None:
        return frequency_to_seconds(ceremony.frequency)

    config = get_ceremony_config(ceremony)
    freq_str = config.get(KEY_FREQUENCY)
    if freq_str is not None:
        try:
            freq = MeetingFrequency(cast("str", freq_str))
        except ValueError:
            logger.warning(
                SPRINT_CEREMONY_SKIPPED,
                ceremony=ceremony.name,
                reason="invalid_frequency",
                frequency=freq_str,
                strategy=strategy_name,
                valid_frequencies=sorted(VALID_FREQUENCIES),
            )
            return None
        return frequency_to_seconds(freq)
    return None


def resolve_duration_days(
    config: SprintConfig,
    strategy_name: str,
) -> int:
    """Resolve duration_days from strategy_config or SprintConfig.

    Falls back to ``config.duration_days`` when the strategy_config
    value is missing, has the wrong type, or is out of range.
    Logs a warning when falling back due to an invalid value.

    Returns:
        The configured duration in days when valid; otherwise the
        SprintConfig's ``duration_days`` default.
    """
    sc = config.ceremony_policy.strategy_config or {}
    duration = sc.get(KEY_DURATION_DAYS)
    if duration is not None:
        if (
            isinstance(duration, int)
            and not isinstance(duration, bool)
            and MIN_DURATION_DAYS <= duration <= MAX_DURATION_DAYS
        ):
            return duration
        logger.warning(
            SPRINT_CEREMONY_SKIPPED,
            reason="invalid_duration_days_in_strategy_config",
            value=duration,
            fallback=config.duration_days,
            strategy=strategy_name,
        )
    return config.duration_days


# -- Trigger evaluation -----------------------------------------------------


def evaluate_task_trigger(
    trigger: str,
    config: Mapping[str, object],
    context: CeremonyEvalContext,
) -> bool:
    """Evaluate a single task-driven trigger condition.

    Args:
        trigger: Trigger type string (e.g. ``sprint_percentage``).
        config: Per-ceremony strategy config mapping.
        context: Current evaluation context.

    Returns:
        ``True`` if the trigger condition is met.
    """
    has_tasks = context.total_tasks_in_sprint > 0
    pct = context.sprint_percentage_complete

    if trigger == TRIGGER_SPRINT_START:
        # Sprint-start ceremonies fire via the on_sprint_activated
        # lifecycle hook, not via periodic evaluation.  Returning
        # False here is intentional -- see CeremonyScheduler.
        return False

    if trigger == TRIGGER_SPRINT_END:
        return has_tasks and pct >= DEFAULT_TRANSITION_THRESHOLD

    if trigger == TRIGGER_SPRINT_MIDPOINT:
        return has_tasks and pct >= MIDPOINT_THRESHOLD

    if trigger == TRIGGER_EVERY_N:
        raw_n = config.get(KEY_EVERY_N, DEFAULT_EVERY_N)
        if not isinstance(raw_n, int) or isinstance(raw_n, bool) or raw_n < 1:
            logger.warning(
                SPRINT_CEREMONY_SKIPPED,
                trigger=trigger,
                reason="invalid_every_n_config",
                value=raw_n,
            )
            return False
        return context.completions_since_last_trigger >= raw_n

    if trigger == TRIGGER_SPRINT_PERCENTAGE:
        raw_threshold = config.get(KEY_SPRINT_PERCENTAGE, DEFAULT_SPRINT_PCT)
        if not isinstance(raw_threshold, int | float) or isinstance(
            raw_threshold, bool
        ):
            logger.warning(
                SPRINT_CEREMONY_SKIPPED,
                trigger=trigger,
                reason="invalid_sprint_percentage_config",
                value=raw_threshold,
            )
            return False
        if raw_threshold <= 0 or raw_threshold > MAX_SPRINT_PCT:
            logger.warning(
                SPRINT_CEREMONY_SKIPPED,
                trigger=trigger,
                reason="invalid_sprint_percentage_bounds",
                value=raw_threshold,
            )
            return False
        return has_tasks and pct >= (raw_threshold / MAX_SPRINT_PCT)

    logger.warning(
        SPRINT_CEREMONY_SKIPPED,
        trigger=trigger,
        reason="unrecognized_trigger",
        valid_triggers=sorted(VALID_TRIGGERS),
    )
    return False


# -- Validation helpers -----------------------------------------------------


def validate_duration_days(value: object) -> None:
    """Validate optional duration_days config value.

    Raises:
        ValueError: When ``value`` is not a positive integer in
            ``[MIN_DURATION_DAYS, MAX_DURATION_DAYS]``.
    """
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool):
        msg = f"{KEY_DURATION_DAYS} must be a positive integer, got {value!r}"
        raise ValueError(msg)  # noqa: TRY004
    if value < MIN_DURATION_DAYS or value > MAX_DURATION_DAYS:
        msg = (
            f"{KEY_DURATION_DAYS} must be between "
            f"{MIN_DURATION_DAYS} and {MAX_DURATION_DAYS}, "
            f"got {value!r}"
        )
        raise ValueError(msg)


def validate_trigger(value: object) -> None:
    """Validate optional trigger config value.

    Raises:
        ValueError: When ``value`` is set but not in
            :data:`VALID_TRIGGERS`.
    """
    if value is not None and value not in VALID_TRIGGERS:
        msg = f"Invalid trigger {value!r}. Valid triggers: {sorted(VALID_TRIGGERS)}"
        raise ValueError(msg)


def validate_every_n(value: object) -> None:
    """Validate optional every_n_completions config value.

    Raises:
        ValueError: When ``value`` is set but not a positive integer.
    """
    if value is not None and (
        not isinstance(value, int) or isinstance(value, bool) or value < 1
    ):
        msg = f"{KEY_EVERY_N} must be a positive integer, got {value!r}"
        raise ValueError(msg)


def validate_sprint_percentage(value: object) -> None:
    """Validate optional sprint_percentage config value.

    Raises:
        ValueError: When ``value`` is set but not a number in
            ``(0, MAX_SPRINT_PCT]``.
    """
    if value is not None and (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or value <= 0
        or value > MAX_SPRINT_PCT
    ):
        msg = (
            f"{KEY_SPRINT_PERCENTAGE} must be between "
            f"0 (exclusive) and {MAX_SPRINT_PCT} (inclusive),"
            f" got {value!r}"
        )
        raise ValueError(msg)


def validate_frequency(value: object) -> None:
    """Validate optional frequency config value.

    Raises:
        ValueError: When ``value`` is set but not in
            :data:`VALID_FREQUENCIES`.
    """
    if value is not None and value not in VALID_FREQUENCIES:
        msg = (
            f"Invalid frequency {value!r}. "
            f"Valid frequencies: {sorted(VALID_FREQUENCIES)}"
        )
        raise ValueError(msg)
