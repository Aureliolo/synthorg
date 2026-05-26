"""Event-driven ceremony scheduling strategy.

Ceremonies subscribe to engine events with configurable debounce.
No fixed schedule -- ceremonies fire reactively based on event
subscriptions.

**Config keys** (per-ceremony ``policy_override.strategy_config``):

- ``on_event`` (str): event name this ceremony subscribes to.
- ``debounce`` (int): number of events before firing (per-ceremony
  override).

**Config keys** (sprint-level ``ceremony_policy.strategy_config``):

- ``debounce_default`` (int): global fallback debounce (default: 5).
- ``transition_event`` (str): event name that triggers sprint
  auto-transition.
"""

from typing import TYPE_CHECKING, Any

from synthorg.engine.workflow.ceremony_policy import (
    CeremonyStrategyType,
)
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.strategies._helpers import get_ceremony_config
from synthorg.engine.workflow.velocity_types import VelocityCalcType
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    SPRINT_AUTO_TRANSITION,
    SPRINT_CEREMONY_EVENT_COUNTER_INCREMENTED,
    SPRINT_CEREMONY_EVENT_DEBOUNCE_NOT_MET,
    SPRINT_CEREMONY_SKIPPED,
    SPRINT_CEREMONY_TRIGGERED,
    SPRINT_STRATEGY_CONFIG_INVALID,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.engine.workflow.ceremony_context import CeremonyEvalContext
    from synthorg.engine.workflow.sprint_config import (
        SprintCeremonyConfig,
        SprintConfig,
    )

logger = get_logger(__name__)

# -- Config keys ---------------------------------------------------------------

_KEY_ON_EVENT: str = "on_event"
_KEY_DEBOUNCE: str = "debounce"
_KEY_DEBOUNCE_DEFAULT: str = "debounce_default"
_KEY_TRANSITION_EVENT: str = "transition_event"

_KNOWN_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        _KEY_ON_EVENT,
        _KEY_DEBOUNCE,
        _KEY_DEBOUNCE_DEFAULT,
        _KEY_TRANSITION_EVENT,
    }
)

_DEFAULT_DEBOUNCE: int = 5
_MAX_DEBOUNCE: int = 10_000
_MAX_EVENT_NAME_LEN: int = 128
_MAX_DISTINCT_EVENTS: int = 64

# -- Recognized lifecycle event names -----------------------------------------

EVENT_TASK_COMPLETED: str = "task_completed"
EVENT_TASK_ADDED: str = "task_added"
EVENT_TASK_BLOCKED: str = "task_blocked"
EVENT_BUDGET_UPDATED: str = "budget_updated"


class EventDrivenStrategy:
    """Ceremony scheduling strategy driven by engine events.

    Ceremonies subscribe to named events (e.g. ``task_completed``,
    ``task_blocked``, or any external event) with a configurable
    debounce count.  A ceremony fires once the debounce threshold is
    reached, then the firing baseline advances so the next debounce
    window begins from the current count.

    State is tracked per-sprint and cleared on sprint transitions.
    """

    __slots__ = ("_ceremony_last_fire_at", "_debounce_default", "_event_counts")

    def __init__(self) -> None:
        self._event_counts: dict[str, int] = {}
        self._ceremony_last_fire_at: dict[str, int] = {}
        self._debounce_default: int = _DEFAULT_DEBOUNCE

    # -- Core evaluation -------------------------------------------------------

    def should_fire_ceremony(
        self,
        ceremony: SprintCeremonyConfig,
        sprint: Sprint,  # noqa: ARG002
        context: CeremonyEvalContext,  # noqa: ARG002
    ) -> bool:
        """Check if the subscribed event's debounce threshold is met.

        Args:
            ceremony: The ceremony being evaluated.
            sprint: Current sprint state.
            context: Evaluation context.

        Returns:
            ``True`` if the ceremony should fire.
        """
        config = get_ceremony_config(ceremony)
        raw_event = config.get(_KEY_ON_EVENT)

        if (
            not isinstance(raw_event, str)
            or not raw_event.strip()
            or len(raw_event) > _MAX_EVENT_NAME_LEN
        ):
            logger.debug(
                SPRINT_CEREMONY_SKIPPED,
                ceremony=ceremony.name,
                reason="no_on_event",
                strategy="event_driven",
            )
            return False

        on_event = raw_event.strip()
        debounce = self._resolve_debounce(config, ceremony.name)
        global_count = self._event_counts.get(on_event, 0)
        last_fire_at = self._ceremony_last_fire_at.get(ceremony.name, 0)
        events_since_fire = global_count - last_fire_at

        if events_since_fire >= debounce:
            self._ceremony_last_fire_at[ceremony.name] = global_count
            logger.info(
                SPRINT_CEREMONY_TRIGGERED,
                ceremony=ceremony.name,
                on_event=on_event,
                debounce=debounce,
                events_since_fire=events_since_fire,
                strategy="event_driven",
            )
            return True

        logger.debug(
            SPRINT_CEREMONY_EVENT_DEBOUNCE_NOT_MET,
            ceremony=ceremony.name,
            on_event=on_event,
            debounce=debounce,
            events_since_fire=events_since_fire,
            strategy="event_driven",
        )
        return False

    def should_transition_sprint(
        self,
        sprint: Sprint,
        config: SprintConfig,
        context: CeremonyEvalContext,
    ) -> SprintStatus | None:
        """Return IN_REVIEW when the configured transition event fires.

        Only transitions from ACTIVE status.  Once the transition
        event has been observed (from external events or internal
        counters), this method returns ``IN_REVIEW`` on every
        subsequent call for the remainder of the sprint.

        Args:
            sprint: Current sprint state.
            config: Sprint configuration.
            context: Evaluation context.

        Returns:
            ``SprintStatus.IN_REVIEW`` if transition event detected,
            else ``None``.
        """
        if sprint.status is not SprintStatus.ACTIVE:
            return None

        strategy_config = config.ceremony_policy.strategy_config or {}
        raw_transition = strategy_config.get(_KEY_TRANSITION_EVENT)
        if (
            not isinstance(raw_transition, str)
            or not raw_transition.strip()
            or len(raw_transition) > _MAX_EVENT_NAME_LEN
        ):
            return None

        return self._check_transition_event(
            raw_transition.strip(),
            context,
        )

    def _check_transition_event(
        self,
        transition_event: str,
        context: CeremonyEvalContext,
    ) -> SprintStatus | None:
        """Check if transition event has been observed.

        Returns:
            ``SprintStatus.IN_REVIEW`` when the named event appears
            in ``context.external_events`` or the internal counter;
            ``None`` otherwise.
        """
        if transition_event in context.external_events:
            logger.info(
                SPRINT_AUTO_TRANSITION,
                transition_event=transition_event,
                source="external_events",
                strategy="event_driven",
            )
            return SprintStatus.IN_REVIEW

        if self._event_counts.get(transition_event, 0) > 0:
            logger.info(
                SPRINT_AUTO_TRANSITION,
                transition_event=transition_event,
                source="internal_counter",
                strategy="event_driven",
            )
            return SprintStatus.IN_REVIEW

        return None

    # -- Lifecycle hooks -------------------------------------------------------

    async def on_sprint_activated(
        self,
        sprint: Sprint,  # noqa: ARG002
        config: SprintConfig,
    ) -> None:
        """Reset state and read debounce_default from config.

        Args:
            sprint: The activated sprint.
            config: Sprint configuration.
        """
        self._event_counts.clear()
        self._ceremony_last_fire_at.clear()

        strategy_config = (
            config.ceremony_policy.strategy_config
            if config.ceremony_policy.strategy_config is not None
            else {}
        )
        debounce_default = strategy_config.get(_KEY_DEBOUNCE_DEFAULT)
        if (
            isinstance(debounce_default, int)
            and not isinstance(debounce_default, bool)
            and 1 <= debounce_default <= _MAX_DEBOUNCE
        ):
            self._debounce_default = debounce_default
        elif debounce_default is not None:
            logger.warning(
                SPRINT_CEREMONY_SKIPPED,
                reason="invalid_debounce_default",
                value=debounce_default,
                fallback=_DEFAULT_DEBOUNCE,
                strategy="event_driven",
            )
            self._debounce_default = _DEFAULT_DEBOUNCE
        else:
            self._debounce_default = _DEFAULT_DEBOUNCE

    async def on_sprint_deactivated(self) -> None:
        """Clear all internal state."""
        self._event_counts.clear()
        self._ceremony_last_fire_at.clear()
        self._debounce_default = _DEFAULT_DEBOUNCE

    async def on_task_completed(
        self,
        sprint: Sprint,  # noqa: ARG002
        task_id: str,  # noqa: ARG002
        story_points: float,  # noqa: ARG002
        context: CeremonyEvalContext,  # noqa: ARG002
    ) -> None:
        """Increment the ``task_completed`` event counter.

        Args:
            sprint: Current sprint state.
            task_id: The completed task ID.
            story_points: Points earned for the task.
            context: Evaluation context.
        """
        self._increment(EVENT_TASK_COMPLETED)

    async def on_task_added(
        self,
        sprint: Sprint,  # noqa: ARG002
        task_id: str,  # noqa: ARG002
    ) -> None:
        """Increment the ``task_added`` event counter.

        Args:
            sprint: Current sprint state.
            task_id: The added task ID.
        """
        self._increment(EVENT_TASK_ADDED)

    async def on_task_blocked(
        self,
        sprint: Sprint,  # noqa: ARG002
        task_id: str,  # noqa: ARG002
    ) -> None:
        """Increment the ``task_blocked`` event counter.

        Args:
            sprint: Current sprint state.
            task_id: The blocked task ID.
        """
        self._increment(EVENT_TASK_BLOCKED)

    async def on_budget_updated(
        self,
        sprint: Sprint,  # noqa: ARG002
        budget_consumed_fraction: float,  # noqa: ARG002
    ) -> None:
        """Increment the ``budget_updated`` event counter.

        Args:
            sprint: Current sprint state.
            budget_consumed_fraction: Budget consumed fraction.
        """
        self._increment(EVENT_BUDGET_UPDATED)

    async def on_external_event(
        self,
        sprint: Sprint,  # noqa: ARG002
        event_name: str,
        payload: Mapping[str, Any],  # noqa: ARG002
    ) -> None:
        """Increment the counter for the named external event.

        Args:
            sprint: Current sprint state.
            event_name: Name of the external event.
            payload: Event payload data.
        """
        if (
            not isinstance(event_name, str)
            or not event_name.strip()
            or len(event_name) > _MAX_EVENT_NAME_LEN
        ):
            logger.warning(
                SPRINT_CEREMONY_SKIPPED,
                reason="invalid_external_event_name",
                value=(
                    event_name
                    if isinstance(event_name, str)
                    else type(event_name).__name__
                ),
                strategy="event_driven",
            )
            return
        self._increment(event_name.strip())

    # -- Metadata --------------------------------------------------------------

    @property
    def strategy_type(self) -> CeremonyStrategyType:
        """Return EVENT_DRIVEN."""
        return CeremonyStrategyType.EVENT_DRIVEN

    def get_default_velocity_calculator(self) -> VelocityCalcType:
        """Return POINTS_PER_SPRINT."""
        return VelocityCalcType.POINTS_PER_SPRINT

    def validate_strategy_config(
        self,
        config: Mapping[str, Any],
    ) -> None:
        """Validate event-driven strategy config.

        Args:
            config: Strategy config to validate.

        Raises:
            ValueError: If the config contains invalid keys or values.
        """
        unknown = set(config) - _KNOWN_CONFIG_KEYS
        if unknown:
            msg = f"Unknown config keys: {sorted(unknown)}"
            logger.warning(
                SPRINT_STRATEGY_CONFIG_INVALID,
                strategy="event_driven",
                unknown_keys=sorted(unknown),
            )
            raise ValueError(msg)

        self._validate_string_key(config, _KEY_ON_EVENT)
        self._validate_string_key(config, _KEY_TRANSITION_EVENT)
        self._validate_debounce_keys(config)

    # -- Private helpers -------------------------------------------------------

    @staticmethod
    def _validate_string_key(
        config: Mapping[str, Any],
        key: str,
    ) -> None:
        """Validate that *key* is a non-empty string if present.

        Raises:
            ValueError: When the key is present but not a non-blank
                string of length ``<= _MAX_EVENT_NAME_LEN``.
        """
        value = config.get(key)
        if value is None:
            return
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > _MAX_EVENT_NAME_LEN
        ):
            msg = f"'{key}' must be a non-empty string (<= {_MAX_EVENT_NAME_LEN} chars)"
            logger.warning(
                SPRINT_STRATEGY_CONFIG_INVALID,
                strategy="event_driven",
                key=key,
                value=value,
            )
            raise ValueError(msg)

    @staticmethod
    def _validate_debounce_keys(
        config: Mapping[str, Any],
    ) -> None:
        """Validate debounce and debounce_default if present.

        Raises:
            ValueError: When a debounce value is not a positive
                integer or exceeds :data:`_MAX_DEBOUNCE`.
        """
        for key in (_KEY_DEBOUNCE, _KEY_DEBOUNCE_DEFAULT):
            value = config.get(key)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                msg = f"'{key}' must be a positive integer, got {value!r}"
                logger.warning(
                    SPRINT_STRATEGY_CONFIG_INVALID,
                    strategy="event_driven",
                    key=key,
                    value=value,
                )
                raise ValueError(msg)
            if value > _MAX_DEBOUNCE:
                msg = f"'{key}' must be <= {_MAX_DEBOUNCE}, got {value!r}"
                logger.warning(
                    SPRINT_STRATEGY_CONFIG_INVALID,
                    strategy="event_driven",
                    key=key,
                    value=value,
                    limit=_MAX_DEBOUNCE,
                )
                raise ValueError(msg)

    def _resolve_debounce(
        self,
        config: Mapping[str, Any],
        ceremony_name: str,
    ) -> int:
        """Resolve debounce value with type validation.

        Returns:
            The configured ``debounce`` integer when valid; the
            strategy's debounce default otherwise (with a warning
            log).
        """
        raw = config.get(_KEY_DEBOUNCE, self._debounce_default)
        if (
            isinstance(raw, bool)
            or not isinstance(raw, int)
            or not (1 <= raw <= _MAX_DEBOUNCE)
        ):
            logger.warning(
                SPRINT_CEREMONY_SKIPPED,
                ceremony=ceremony_name,
                reason="invalid_debounce",
                value=raw,
                fallback=self._debounce_default,
                strategy="event_driven",
            )
            return self._debounce_default
        result: int = raw
        return result

    def _increment(self, event_name: str) -> None:
        """Increment the global event counter for the given event.

        Callers must validate ``event_name`` before calling.
        Internal lifecycle hooks pass known constants; external
        events are validated in ``on_external_event``.
        """
        if (
            event_name not in self._event_counts
            and len(self._event_counts) >= _MAX_DISTINCT_EVENTS
        ):
            logger.warning(
                SPRINT_CEREMONY_SKIPPED,
                reason="too_many_distinct_events",
                event_name=event_name,
                limit=_MAX_DISTINCT_EVENTS,
                strategy="event_driven",
            )
            return
        self._event_counts[event_name] = self._event_counts.get(event_name, 0) + 1
        logger.debug(
            SPRINT_CEREMONY_EVENT_COUNTER_INCREMENTED,
            event_name=event_name,
            count=self._event_counts[event_name],
        )
