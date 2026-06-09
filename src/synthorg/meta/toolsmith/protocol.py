"""Pluggable protocols for the self-extending toolkit (toolsmith).

Every collaborator in the toolsmith pipeline is defined here as a
``@runtime_checkable`` Protocol so the service composes against
interfaces (protocol + strategy + factory + config discriminator).
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.meta.models import ImprovementProposal
from synthorg.meta.toolsmith.models import (
    CapabilityGap,
    ToolBlueprint,
    ToolValidationResult,
)


@runtime_checkable
class CapabilityGapSink(Protocol):
    """Receives a single capability-gap observation.

    Wired into the MCP ``capability_gap`` envelope helper so every
    unfulfilled capability request is recorded for recurrence analysis.
    """

    async def record_gap(
        self,
        signature: NotBlankStr,
        *,
        occurred_at: datetime,
    ) -> None:
        """Record one observation of a missing capability.

        Args:
            signature: Stable key for the missing capability (the
                requested ``domain:action`` capability tag).
            occurred_at: Observation timestamp (UTC, caller-supplied
                via the Clock seam).
        """
        ...


@runtime_checkable
class CapabilityGapStore(CapabilityGapSink, Protocol):
    """Aggregates capability-gap observations and surfaces recurrence."""

    async def recurring(
        self,
        *,
        threshold: int,
        window: timedelta,
        now: datetime,
    ) -> tuple[CapabilityGap, ...]:
        """Return gaps observed at least ``threshold`` times in the window.

        Args:
            threshold: Minimum occurrences within the window to qualify.
            window: Sliding window measured back from ``now``.
            now: Current time (UTC, caller-supplied via the Clock seam).

        Returns:
            Qualifying gaps, most-frequent first (ties broken by
            signature for determinism).
        """
        ...

    async def clear(self) -> None:
        """Drop all stored observations."""
        ...


@runtime_checkable
class ToolBlueprintGenerator(Protocol):
    """Authors a tool blueprint from a recurring capability gap."""

    async def author(
        self,
        gap: CapabilityGap,
        *,
        existing_capabilities: Sequence[NotBlankStr],
    ) -> ToolBlueprint:
        """Generate a candidate blueprint addressing ``gap``.

        Args:
            gap: The recurring capability gap to address.
            existing_capabilities: Capability tags already on the tool
                surface, so the generator avoids duplicates.

        Returns:
            A candidate :class:`ToolBlueprint` in ``PENDING`` state.

        Raises:
            ToolAuthoringError: If the model output cannot be parsed
                into a valid blueprint.
        """
        ...


@runtime_checkable
class ToolAcceptanceBriefRunner(Protocol):
    """Runs the focused per-tool acceptance brief for a candidate tool."""

    async def run(self, blueprint: ToolBlueprint) -> tuple[bool, int]:
        """Exercise the authored tool against an acceptance probe.

        Args:
            blueprint: The candidate blueprint to exercise.

        Returns:
            ``(passed, score)`` where ``score`` is in ``[0, 100]``.
        """
        ...


@runtime_checkable
class GoldenScorecardProvider(Protocol):
    """Scores the golden-company benchmark with and without a candidate."""

    async def score(self, blueprint: ToolBlueprint) -> tuple[int, int]:
        """Return ``(baseline_total, candidate_total)`` golden scores.

        ``baseline_total`` is the scorecard without the candidate tool;
        ``candidate_total`` is the scorecard with it registered. A
        candidate that regresses the org scores ``candidate < baseline``.
        """
        ...


@runtime_checkable
class ToolValidationGate(Protocol):
    """Validates a candidate blueprint against the golden benchmark."""

    async def validate(self, blueprint: ToolBlueprint) -> ToolValidationResult:
        """Run the per-tool acceptance brief and golden scorecard delta.

        Args:
            blueprint: The candidate blueprint to validate.

        Returns:
            A :class:`ToolValidationResult`; ``passed`` is ``True`` only
            when the brief passes and the golden scorecard does not
            regress.
        """
        ...


@runtime_checkable
class ToolCreationOverflowHandler(Protocol):
    """Handles capability gaps that need service-layer access.

    A sandbox script cannot reach the internal service layer, so gaps
    whose capability is in ``service_access_capabilities`` are routed
    here instead of being authored as sandbox tools. The default
    implementation delegates to the self-improvement ``CODE_MODIFICATION``
    altitude (which yields a draft PR, not a same-run tool).
    """

    async def handle(self, gap: CapabilityGap) -> tuple[ImprovementProposal, ...]:
        """Produce proposals addressing a service-access capability gap."""
        ...


@runtime_checkable
class DynamicToolRegistryProtocol(Protocol):
    """Mutable, lock-guarded registry of live authored tools.

    Layered behind the frozen static ``DomainToolRegistry``: the invoker
    reads the static surface first, then this dynamic layer.
    """

    async def register(self, blueprint: ToolBlueprint) -> None:
        """Register an active blueprint as a live MCP tool."""
        ...

    async def unregister(self, name: NotBlankStr) -> bool:
        """Remove a live tool by name; ``True`` iff it was present."""
        ...

    def names(self) -> tuple[NotBlankStr, ...]:
        """Return the currently-registered dynamic tool names."""
        ...
