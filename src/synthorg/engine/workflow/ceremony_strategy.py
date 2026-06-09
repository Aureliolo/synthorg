"""Pluggable ceremony scheduling strategy protocol.

Defines the ``CeremonySchedulingStrategy`` runtime-checkable protocol
that all scheduling strategy implementations must satisfy.  The protocol
includes stateless core evaluation methods and optional lifecycle hooks
for stateful strategies.

See ``docs/design/ceremony-scheduling.md`` for the full design.
"""

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from synthorg.engine.workflow.ceremony_context import (
    CeremonyEvalContext,
)
from synthorg.engine.workflow.ceremony_policy import (
    CeremonyStrategyType,
)
from synthorg.engine.workflow.sprint_config import (
    SprintCeremonyConfig,
    SprintConfig,
)
from synthorg.engine.workflow.sprint_lifecycle import (
    Sprint,
    SprintStatus,
)
from synthorg.engine.workflow.velocity_types import (
    VelocityCalcType,
)


@runtime_checkable
class CeremonySchedulingStrategy(Protocol):
    """Pluggable strategy for ceremony scheduling.

    Implementations provide stateless core evaluation methods that
    the ``CeremonyScheduler`` calls on every relevant event.  Optional
    lifecycle hooks allow stateful strategies (e.g. external-trigger,
    throughput-adaptive) to manage subscriptions and internal tracking.

    **Core methods** (must be implemented):

    - ``should_fire_ceremony``: evaluate whether a ceremony should fire.
    - ``should_transition_sprint``: evaluate whether the sprint should
      auto-transition.
    - ``strategy_type``: return the strategy type enum value.
    - ``get_default_velocity_calculator``: return the default velocity
      calculator type for this strategy.
    - ``validate_strategy_config``: validate strategy-specific config.

    **Lifecycle hooks** (optional -- implement as no-ops for stateless
    strategies):

    - ``on_sprint_activated``, ``on_sprint_deactivated``
    - ``on_task_completed``, ``on_task_added``, ``on_task_blocked``
    - ``on_budget_updated``, ``on_external_event``
    """

    # -- Core evaluation (stateless, called per event) -----------------------

    def should_fire_ceremony(
        self,
        ceremony: SprintCeremonyConfig,
        sprint: Sprint,
        context: CeremonyEvalContext,
    ) -> bool:
        """Evaluate whether a ceremony should fire right now.

        Args:
            ceremony: The ceremony configuration being evaluated.
            sprint: Current sprint state.
            context: Rich evaluation context with counters, timings,
                and metrics.

        Returns:
            ``True`` if the ceremony should fire.
        """
        ...

    def should_transition_sprint(
        self,
        sprint: Sprint,
        config: SprintConfig,
        context: CeremonyEvalContext,
    ) -> SprintStatus | None:
        """Evaluate whether the sprint should auto-transition.

        Args:
            sprint: Current sprint state.
            config: Sprint configuration (includes transition threshold).
            context: Rich evaluation context.

        Returns:
            Target ``SprintStatus`` if the sprint should transition,
            ``None`` if no transition.
        """
        ...

    # -- Lifecycle hooks (optional, for stateful strategies) -----------------

    async def on_sprint_activated(
        self,
        sprint: Sprint,
        config: SprintConfig,
    ) -> None:
        """Called when a sprint is activated in the CeremonyScheduler.

        Use this hook to initialize internal state, subscribe to events,
        or set up rate tracking.

        Args:
            sprint: The activated sprint.
            config: Sprint configuration.
        """
        ...

    async def on_sprint_deactivated(self) -> None:
        """Called when the active sprint is deactivated.

        Use this hook to clean up subscriptions or internal state.
        """
        ...

    async def on_task_completed(
        self,
        sprint: Sprint,
        task_id: str,
        story_points: float,
        context: CeremonyEvalContext,
    ) -> None:
        """Called after a task is completed within the active sprint.

        Use this hook to update internal rate tracking or state.
        The ``CeremonyScheduler`` calls ``should_fire_ceremony`` and
        ``should_transition_sprint`` separately after this hook.

        Args:
            sprint: Current sprint state (after completion).
            task_id: The completed task ID.
            story_points: Points earned for the task.
            context: Evaluation context at the time of completion.
        """
        ...

    async def on_task_added(
        self,
        sprint: Sprint,
        task_id: str,
    ) -> None:
        """Called when a task is added to the active sprint.

        Args:
            sprint: Current sprint state (after addition).
            task_id: The added task ID.
        """
        ...

    async def on_task_blocked(
        self,
        sprint: Sprint,
        task_id: str,
    ) -> None:
        """Called when a task in the active sprint is blocked.

        Args:
            sprint: Current sprint state.
            task_id: The blocked task ID.
        """
        ...

    async def on_budget_updated(
        self,
        sprint: Sprint,
        budget_consumed_fraction: float,
    ) -> None:
        """Called when the sprint's budget consumption changes.

        Args:
            sprint: Current sprint state.
            budget_consumed_fraction: Budget consumed as a fraction
                (0.0--1.0).
        """
        ...

    async def on_external_event(
        self,
        sprint: Sprint,
        event_name: str,
        payload: Mapping[str, object],
    ) -> None:
        """Called when an external event is received.

        Args:
            sprint: Current sprint state.
            event_name: Name of the external event.
            payload: Event payload data.
        """
        ...

    # -- Metadata ------------------------------------------------------------

    @property
    def strategy_type(self) -> CeremonyStrategyType:
        """Return the strategy type enum value."""
        ...

    def get_default_velocity_calculator(self) -> VelocityCalcType:
        """Return the default velocity calculator type for this strategy."""
        ...

    def validate_strategy_config(
        self,
        config: Mapping[str, object],
    ) -> None:
        """Validate strategy-specific configuration parameters.

        Args:
            config: Strategy config dict to validate.

        Raises:
            ValueError: If the config is invalid.
        """
        ...
