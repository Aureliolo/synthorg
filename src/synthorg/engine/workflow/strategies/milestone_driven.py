"""Milestone-driven ceremony scheduling strategy.

Ceremonies fire at semantic project milestones rather than task counts
or time.  Milestone definitions (which ceremonies they trigger) come
from ``ceremony_policy.strategy_config``.  Task membership within
milestones is managed at runtime via ``on_external_event`` lifecycle
hooks (``milestone_assign`` / ``milestone_unassign`` events).

**Config keys** (sprint-level ``ceremony_policy.strategy_config``):

- ``milestones`` (list[dict]): milestone definitions, each with
  ``name`` (str) and ``ceremony`` (str) keys.
- ``transition_milestone`` (str): milestone name that triggers sprint
  auto-transition.
"""

from typing import TYPE_CHECKING, Any

from synthorg.engine.workflow.ceremony_policy import (
    CeremonyStrategyType,
)
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.velocity_types import VelocityCalcType
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    SPRINT_AUTO_TRANSITION_MILESTONE,
    SPRINT_CEREMONY_MILESTONE_ASSIGNED,
    SPRINT_CEREMONY_MILESTONE_COMPLETED,
    SPRINT_CEREMONY_MILESTONE_NOT_READY,
    SPRINT_CEREMONY_MILESTONE_UNASSIGNED,
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

_KEY_MILESTONES: str = "milestones"
_KEY_TRANSITION_MILESTONE: str = "transition_milestone"

_KNOWN_CONFIG_KEYS: frozenset[str] = frozenset(
    {_KEY_MILESTONES, _KEY_TRANSITION_MILESTONE},
)

_MAX_MILESTONES: int = 32
_MAX_NAME_LEN: int = 128
_MAX_TASKS_PER_MILESTONE: int = 1000

# -- External event names ------------------------------------------------------

EVENT_MILESTONE_ASSIGN: str = "milestone_assign"
EVENT_MILESTONE_UNASSIGN: str = "milestone_unassign"


class MilestoneDrivenStrategy:
    """Ceremony scheduling strategy driven by milestone completion.

    Ceremonies fire when all tasks tagged with a configured milestone
    are complete.  Milestones are populated via ``on_external_event``
    with ``milestone_assign`` / ``milestone_unassign`` events.

    Firing is **edge-triggered**: each milestone fires its ceremony
    exactly once per sprint.

    State is tracked per-sprint and cleared on sprint transitions.
    """

    __slots__ = (
        "_fired_milestones",
        "_milestone_tasks",
        "_milestones",
        "_transition_milestone",
    )

    def __init__(self) -> None:
        self._milestones: dict[str, str] = {}
        self._milestone_tasks: dict[str, set[str]] = {}
        self._fired_milestones: set[str] = set()
        self._transition_milestone: str | None = None

    # -- Core evaluation -------------------------------------------------------

    def should_fire_ceremony(
        self,
        ceremony: SprintCeremonyConfig,
        sprint: Sprint,
        context: CeremonyEvalContext,  # noqa: ARG002
    ) -> bool:
        """Check if a milestone mapped to this ceremony is complete.

        Args:
            ceremony: The ceremony being evaluated.
            sprint: Current sprint state.
            context: Evaluation context.

        Returns:
            ``True`` if the ceremony should fire.
        """
        completed_set = set(sprint.completed_task_ids)

        for milestone_name, ceremony_name in self._milestones.items():
            if ceremony_name != ceremony.name:
                continue
            if self._evaluate_milestone(
                milestone_name,
                ceremony.name,
                completed_set,
            ):
                return True

        logger.debug(
            SPRINT_CEREMONY_SKIPPED,
            ceremony=ceremony.name,
            reason="no_milestone_complete",
            strategy="milestone_driven",
        )
        return False

    def _evaluate_milestone(
        self,
        milestone_name: str,
        ceremony_name: str,
        completed_set: set[str],
    ) -> bool:
        """Evaluate whether a single milestone should fire.

        A milestone is complete when it has at least one assigned task
        and all assigned tasks appear in the completed set.  Each
        milestone fires exactly once (edge-triggered).

        Args:
            milestone_name: Name of the milestone.
            ceremony_name: Name of the mapped ceremony.
            completed_set: Set of completed task IDs.

        Returns:
            ``True`` if the milestone fired.
        """
        tasks = self._milestone_tasks.get(milestone_name)
        if not tasks:
            logger.debug(
                SPRINT_CEREMONY_MILESTONE_NOT_READY,
                ceremony=ceremony_name,
                milestone=milestone_name,
                strategy="milestone_driven",
            )
            return False

        if milestone_name in self._fired_milestones:
            return False

        if tasks <= completed_set:
            self._fired_milestones.add(milestone_name)
            logger.info(
                SPRINT_CEREMONY_MILESTONE_COMPLETED,
                milestone=milestone_name,
                task_count=len(tasks),
                strategy="milestone_driven",
            )
            logger.info(
                SPRINT_CEREMONY_TRIGGERED,
                ceremony=ceremony_name,
                milestone=milestone_name,
                strategy="milestone_driven",
            )
            return True

        return False

    def should_transition_sprint(
        self,
        sprint: Sprint,
        config: SprintConfig,  # noqa: ARG002
        context: CeremonyEvalContext,  # noqa: ARG002
    ) -> SprintStatus | None:
        """Return IN_REVIEW when the transition milestone is complete.

        Only transitions from ACTIVE status.  Uses the
        ``_transition_milestone`` parsed during
        ``on_sprint_activated``.

        Args:
            sprint: Current sprint state.
            config: Sprint configuration.
            context: Evaluation context.

        Returns:
            ``SprintStatus.IN_REVIEW`` if transition milestone
            complete, else ``None``.
        """
        if sprint.status is not SprintStatus.ACTIVE:
            return None

        if self._transition_milestone is None:
            return None

        tasks = self._milestone_tasks.get(self._transition_milestone)
        if not tasks:
            return None

        completed_set = set(sprint.completed_task_ids)
        if tasks <= completed_set:
            logger.info(
                SPRINT_AUTO_TRANSITION_MILESTONE,
                transition_milestone=self._transition_milestone,
                task_count=len(tasks),
                strategy="milestone_driven",
            )
            return SprintStatus.IN_REVIEW

        return None

    # -- Lifecycle hooks -------------------------------------------------------

    async def on_sprint_activated(
        self,
        sprint: Sprint,  # noqa: ARG002
        config: SprintConfig,
    ) -> None:
        """Reset state and read milestone definitions from config.

        Args:
            sprint: The activated sprint.
            config: Sprint configuration.
        """
        self._milestones.clear()
        self._milestone_tasks.clear()
        self._fired_milestones.clear()
        self._transition_milestone = None

        strategy_config = (
            config.ceremony_policy.strategy_config
            if config.ceremony_policy.strategy_config is not None
            else {}
        )

        raw_milestones = strategy_config.get(_KEY_MILESTONES)
        if isinstance(raw_milestones, list):
            for entry in raw_milestones:
                if not isinstance(entry, dict):
                    continue
                if len(self._milestones) >= _MAX_MILESTONES:
                    break
                name = entry.get("name")
                ceremony = entry.get("ceremony")
                if (
                    isinstance(name, str)
                    and name.strip()
                    and len(name) <= _MAX_NAME_LEN
                    and isinstance(ceremony, str)
                    and ceremony.strip()
                    and len(ceremony) <= _MAX_NAME_LEN
                ):
                    self._milestones[name.strip()] = ceremony.strip()

        raw_transition = strategy_config.get(_KEY_TRANSITION_MILESTONE)
        if (
            isinstance(raw_transition, str)
            and raw_transition.strip()
            and len(raw_transition) <= _MAX_NAME_LEN
        ):
            self._transition_milestone = raw_transition.strip()

    async def on_sprint_deactivated(self) -> None:
        """Clear all internal state."""
        self._milestones.clear()
        self._milestone_tasks.clear()
        self._fired_milestones.clear()
        self._transition_milestone = None

    async def on_task_completed(
        self,
        sprint: Sprint,
        task_id: str,
        story_points: float,
        context: CeremonyEvalContext,
    ) -> None:
        """No-op -- completion is evaluated against sprint state.

        Args:
            sprint: Current sprint state.
            task_id: The completed task ID.
            story_points: Points earned for the task.
            context: Evaluation context.
        """

    async def on_task_added(
        self,
        sprint: Sprint,
        task_id: str,
    ) -> None:
        """No-op -- task membership managed via on_external_event.

        Args:
            sprint: Current sprint state.
            task_id: The added task ID.
        """

    async def on_task_blocked(
        self,
        sprint: Sprint,
        task_id: str,
    ) -> None:
        """No-op.

        Args:
            sprint: Current sprint state.
            task_id: The blocked task ID.
        """

    async def on_budget_updated(
        self,
        sprint: Sprint,
        budget_consumed_fraction: float,
    ) -> None:
        """No-op.

        Args:
            sprint: Current sprint state.
            budget_consumed_fraction: Budget consumed fraction.
        """

    async def on_external_event(
        self,
        sprint: Sprint,
        event_name: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Handle milestone_assign / milestone_unassign events.

        Expected payload keys: ``task_id`` (str), ``milestone`` (str).
        Assign events are rejected if ``task_id`` is not in the
        active sprint's task list.

        Args:
            sprint: Current sprint state.
            event_name: Name of the external event.
            payload: Event payload data.
        """
        if event_name == EVENT_MILESTONE_ASSIGN:
            self._handle_assign(sprint, payload)
        elif event_name == EVENT_MILESTONE_UNASSIGN:
            self._handle_unassign(payload)

    # -- Metadata --------------------------------------------------------------

    @property
    def strategy_type(self) -> CeremonyStrategyType:
        """Return MILESTONE_DRIVEN."""
        return CeremonyStrategyType.MILESTONE_DRIVEN

    def get_default_velocity_calculator(self) -> VelocityCalcType:
        """Return POINTS_PER_SPRINT."""
        return VelocityCalcType.POINTS_PER_SPRINT

    def validate_strategy_config(
        self,
        config: Mapping[str, Any],
    ) -> None:
        """Validate milestone-driven strategy config.

        Args:
            config: Strategy config to validate.

        Raises:
            ValueError: If the config contains invalid keys or values.
            TypeError: If ``milestones`` is not a list or an entry
                is not a mapping.
        """
        unknown = set(config) - _KNOWN_CONFIG_KEYS
        if unknown:
            msg = f"Unknown config keys: {sorted(unknown)}"
            logger.warning(
                SPRINT_STRATEGY_CONFIG_INVALID,
                strategy="milestone_driven",
                unknown_keys=sorted(unknown),
            )
            raise ValueError(msg)

        self._validate_milestones(config)
        self._validate_transition_milestone(config)

    # -- Private helpers -------------------------------------------------------

    def _handle_assign(
        self,
        sprint: Sprint,
        payload: Mapping[str, Any],
    ) -> None:
        """Validate, guard, and delegate a milestone assignment."""
        normalized = self._normalize_payload(payload, "assign")
        if normalized is None:
            return
        task_id, milestone = normalized

        if task_id not in sprint.task_ids:
            logger.debug(
                SPRINT_CEREMONY_SKIPPED,
                reason="task_not_in_active_sprint",
                task_id=task_id,
                milestone=milestone,
                strategy="milestone_driven",
            )
            return

        self._add_task_to_milestone(task_id, milestone)

    def _normalize_payload(
        self,
        payload: Mapping[str, Any],
        event_kind: str,
    ) -> tuple[str, str] | None:
        """Extract and validate ``(task_id, milestone)`` from payload.

        Args:
            payload: Event payload data.
            event_kind: ``"assign"`` or ``"unassign"`` for logging.

        Returns:
            Stripped ``(task_id, milestone)`` or ``None`` if invalid.
        """
        task_id = payload.get("task_id")
        milestone = payload.get("milestone")
        if (
            not isinstance(task_id, str)
            or not task_id.strip()
            or not isinstance(milestone, str)
            or not milestone.strip()
        ):
            logger.debug(
                SPRINT_CEREMONY_SKIPPED,
                reason=f"invalid_milestone_{event_kind}_payload",
                strategy="milestone_driven",
            )
            return None
        return task_id.strip(), milestone.strip()

    def _add_task_to_milestone(
        self,
        task_id: str,
        milestone: str,
    ) -> None:
        """Register a task under a known milestone.

        Rejects unknown milestones and enforces the per-milestone
        task cap (``_MAX_TASKS_PER_MILESTONE``).

        Args:
            task_id: Stripped task identifier.
            milestone: Stripped milestone name.
        """
        if (
            milestone not in self._milestones
            and milestone != self._transition_milestone
        ):
            logger.debug(
                SPRINT_CEREMONY_SKIPPED,
                reason="unknown_milestone",
                milestone=milestone,
                strategy="milestone_driven",
            )
            return

        tasks = self._milestone_tasks.setdefault(milestone, set())

        if task_id not in tasks and len(tasks) >= _MAX_TASKS_PER_MILESTONE:
            logger.warning(
                SPRINT_CEREMONY_SKIPPED,
                reason="too_many_tasks_in_milestone",
                milestone=milestone,
                limit=_MAX_TASKS_PER_MILESTONE,
                strategy="milestone_driven",
            )
            return

        if task_id not in tasks:
            tasks.add(task_id)
            logger.info(
                SPRINT_CEREMONY_MILESTONE_ASSIGNED,
                task_id=task_id,
                milestone=milestone,
                task_count=len(tasks),
                strategy="milestone_driven",
            )

    def _handle_unassign(self, payload: Mapping[str, Any]) -> None:
        """Remove a task from a milestone.

        No sprint guard -- unassign is always allowed so that stale
        entries can be cleaned up even if a task leaves the sprint.
        """
        normalized = self._normalize_payload(payload, "unassign")
        if normalized is None:
            return
        task_id, milestone = normalized

        tasks = self._milestone_tasks.get(milestone)
        if tasks is not None and task_id in tasks:
            tasks.discard(task_id)
            logger.info(
                SPRINT_CEREMONY_MILESTONE_UNASSIGNED,
                task_id=task_id,
                milestone=milestone,
                remaining=len(tasks),
                strategy="milestone_driven",
            )

    @staticmethod
    def _validate_milestones(config: Mapping[str, Any]) -> None:
        """Validate the ``milestones`` config key.

        Raises:
            TypeError: When ``milestones`` is set but not a list.
            ValueError: When the list exceeds ``_MAX_MILESTONES``
                entries or any entry fails validation.
        """
        raw = config.get(_KEY_MILESTONES)
        if raw is None:
            return

        if not isinstance(raw, list):
            msg = "'milestones' must be a list"
            logger.warning(
                SPRINT_STRATEGY_CONFIG_INVALID,
                strategy="milestone_driven",
                key=_KEY_MILESTONES,
                value_type=type(raw).__name__,
            )
            raise TypeError(msg)

        if len(raw) > _MAX_MILESTONES:
            msg = f"'milestones' must have <= {_MAX_MILESTONES} entries, got {len(raw)}"
            logger.warning(
                SPRINT_STRATEGY_CONFIG_INVALID,
                strategy="milestone_driven",
                key=_KEY_MILESTONES,
                count=len(raw),
                limit=_MAX_MILESTONES,
            )
            raise ValueError(msg)

        seen_names: set[str] = set()
        for i, entry in enumerate(raw):
            _validate_single_milestone(entry, i, seen_names)

    @staticmethod
    def _validate_transition_milestone(
        config: Mapping[str, Any],
    ) -> None:
        """Validate the ``transition_milestone`` config key.

        Raises:
            ValueError: When ``transition_milestone`` is set but is
                blank, not a string, or exceeds ``_MAX_NAME_LEN``.
        """
        raw = config.get(_KEY_TRANSITION_MILESTONE)
        if raw is None:
            return

        if isinstance(raw, bool) or not isinstance(raw, str) or not raw.strip():
            msg = "'transition_milestone' must be a non-empty string"
            logger.warning(
                SPRINT_STRATEGY_CONFIG_INVALID,
                strategy="milestone_driven",
                key=_KEY_TRANSITION_MILESTONE,
                value=raw,
            )
            raise ValueError(msg)

        if len(raw) > _MAX_NAME_LEN:
            msg = (
                f"'transition_milestone' must be <= "
                f"{_MAX_NAME_LEN} chars, got {len(raw)}"
            )
            logger.warning(
                SPRINT_STRATEGY_CONFIG_INVALID,
                strategy="milestone_driven",
                key=_KEY_TRANSITION_MILESTONE,
                length=len(raw),
                limit=_MAX_NAME_LEN,
            )
            raise ValueError(msg)


def _validate_single_milestone(
    entry: object,
    index: int,
    seen_names: set[str],
) -> None:
    """Validate a single milestone entry in the config list.

    Raises:
        TypeError: When the entry is not a mapping.
        ValueError: When required string fields are missing / blank or
            the milestone ``name`` duplicates an earlier entry.
    """
    if not isinstance(entry, dict):
        msg = f"'milestones[{index}]' must be a mapping"
        logger.warning(
            SPRINT_STRATEGY_CONFIG_INVALID,
            strategy="milestone_driven",
            index=index,
        )
        raise TypeError(msg)

    _validate_milestone_string(entry, "name", index)
    _validate_milestone_string(entry, "ceremony", index)

    name = entry["name"].strip()
    if name in seen_names:
        msg = f"Duplicate milestone name: {name!r}"
        logger.warning(
            SPRINT_STRATEGY_CONFIG_INVALID,
            strategy="milestone_driven",
            duplicate=name,
        )
        raise ValueError(msg)
    seen_names.add(name)


def _validate_milestone_string(
    entry: dict[str, Any],
    key: str,
    index: int,
) -> None:
    """Validate that a milestone entry has a non-empty string key.

    Raises:
        ValueError: When ``entry[key]`` is missing, not a string, or
            exceeds ``_MAX_NAME_LEN`` characters.
    """
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
        msg = f"'milestones[{index}].{key}' must be a non-empty string"
        logger.warning(
            SPRINT_STRATEGY_CONFIG_INVALID,
            strategy="milestone_driven",
            key=f"milestones[{index}].{key}",
            value=value,
        )
        raise ValueError(msg)

    if len(value) > _MAX_NAME_LEN:
        msg = (
            f"'milestones[{index}].{key}' must be <= "
            f"{_MAX_NAME_LEN} chars, got {len(value)}"
        )
        logger.warning(
            SPRINT_STRATEGY_CONFIG_INVALID,
            strategy="milestone_driven",
            key=f"milestones[{index}].{key}",
            length=len(value),
            limit=_MAX_NAME_LEN,
        )
        raise ValueError(msg)
