"""Toolsmith orchestration service.

Ties the self-extending toolkit together: it is the capability-gap sink,
runs the detection -> author -> guard cycle, and applies approved
proposals. The cycle mirrors the self-improvement loop but at the
``TOOL_CREATION`` altitude:

1. Aggregate recurring capability gaps from the gap store.
2. For each gap, either author a sandbox tool (primary path) or route to
   the code-modification overflow handler (service-access capabilities).
3. Wrap each authored blueprint in an ``ImprovementProposal`` and run it
   through the guard chain (scope, rollback, rate limit, approval). Only
   guarded-and-enqueued proposals are returned.

Approved proposals are applied via :meth:`apply`, which validates against
the benchmark gate and live-registers the tool on pass.
"""

from datetime import timedelta
from typing import TYPE_CHECKING

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    ApplyResult,
    GuardVerdict,
    ImprovementProposal,
    ProposalAltitude,
    ProposalRationale,
    RollbackOperation,
    RollbackPlan,
)
from synthorg.meta.toolsmith.errors import (
    ToolAuthoringError,
    ToolCapabilityNotAllowedError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.toolsmith import (
    TOOLSMITH_AUTHOR_OVERFLOW_TO_CODE_MOD,
    TOOLSMITH_AUTHOR_SKIPPED,
    TOOLSMITH_CYCLE_COMPLETED,
    TOOLSMITH_CYCLE_STARTED,
    TOOLSMITH_PROPOSAL_GUARD_REJECTED,
)

if TYPE_CHECKING:
    from datetime import datetime

    from synthorg.meta.protocol import ProposalGuard
    from synthorg.meta.toolsmith.applier import ToolCreationApplier
    from synthorg.meta.toolsmith.config import ToolsmithConfig
    from synthorg.meta.toolsmith.models import CapabilityGap, ToolBlueprint
    from synthorg.meta.toolsmith.protocol import (
        CapabilityGapStore,
        ToolBlueprintGenerator,
        ToolCreationOverflowHandler,
    )

logger = get_logger(__name__)


class ToolsmithService:
    """Orchestrates capability-gap detection, authoring, and application.

    Args:
        config: Toolsmith configuration.
        gap_store: Capability-gap store (also the sink the service exposes).
        generator: Authors blueprints from gaps.
        applier: Validates and live-registers approved blueprints.
        guards: Sequential guard chain (scope, rollback, rate, approval).
        overflow_handler: Handles service-access gaps (optional).
        existing_capabilities: Callable returning the current capability
            surface, so the generator avoids duplicates.
        clock: Time source.
    """

    def __init__(  # noqa: PLR0913 -- explicit DI of the toolsmith collaborators
        self,
        *,
        config: ToolsmithConfig,
        gap_store: CapabilityGapStore,
        generator: ToolBlueprintGenerator,
        applier: ToolCreationApplier,
        guards: tuple[ProposalGuard, ...],
        overflow_handler: ToolCreationOverflowHandler | None = None,
        existing_capabilities: tuple[NotBlankStr, ...] = (),
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._gap_store = gap_store
        self._generator = generator
        self._applier = applier
        self._guards = guards
        self._overflow_handler = overflow_handler
        self._existing_capabilities = existing_capabilities
        self._clock = clock or SystemClock()

    async def record_gap(
        self,
        signature: NotBlankStr,
        *,
        occurred_at: datetime | None = None,
    ) -> None:
        """Record a capability-gap observation (the sink seam)."""
        await self._gap_store.record_gap(
            signature, occurred_at=occurred_at or self._clock.now()
        )

    async def run_cycle(
        self, *, now: datetime | None = None
    ) -> tuple[ImprovementProposal, ...]:
        """Detect recurring gaps, author proposals, and guard them."""
        moment = now or self._clock.now()
        logger.info(TOOLSMITH_CYCLE_STARTED)
        gaps = await self._gap_store.recurring(
            threshold=self._config.gap_recurrence_threshold,
            window=timedelta(hours=self._config.gap_window_hours),
            now=moment,
        )
        proposals: list[ImprovementProposal] = []
        for gap in gaps:
            proposals.extend(await self._handle_gap(gap))
        logger.info(
            TOOLSMITH_CYCLE_COMPLETED,
            gaps=len(gaps),
            proposals=len(proposals),
        )
        return tuple(proposals)

    async def apply(self, proposal: ImprovementProposal) -> ApplyResult:
        """Validate and live-register an approved tool-creation proposal."""
        return await self._applier.apply(proposal)

    async def _handle_gap(self, gap: CapabilityGap) -> tuple[ImprovementProposal, ...]:
        """Author or overflow a single gap, then guard the result."""
        if gap.signature in self._config.service_access_capabilities:
            return await self._handle_overflow(gap)
        try:
            blueprint = await self._generator.author(
                gap, existing_capabilities=self._existing_capabilities
            )
        except (ToolCapabilityNotAllowedError, ToolAuthoringError) as exc:
            logger.warning(
                TOOLSMITH_AUTHOR_SKIPPED,
                capability=gap.signature,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ()
        proposal = _build_proposal(gap, blueprint)
        if await self._guards_pass(proposal):
            return (proposal,)
        return ()

    async def _handle_overflow(
        self, gap: CapabilityGap
    ) -> tuple[ImprovementProposal, ...]:
        """Route a service-access gap to the code-modification overflow."""
        logger.info(
            TOOLSMITH_AUTHOR_OVERFLOW_TO_CODE_MOD,
            capability=gap.signature,
            configured=self._overflow_handler is not None,
        )
        if self._overflow_handler is None:
            return ()
        proposals = await self._overflow_handler.handle(gap)
        guarded = [p for p in proposals if await self._guards_pass(p)]
        return tuple(guarded)

    async def _guards_pass(self, proposal: ImprovementProposal) -> bool:
        """Evaluate the guard chain sequentially; all must pass."""
        for guard in self._guards:
            result = await guard.evaluate(proposal)
            if result.verdict is GuardVerdict.REJECTED:
                logger.info(
                    TOOLSMITH_PROPOSAL_GUARD_REJECTED,
                    guard=guard.name,
                    reason=result.reason,
                )
                return False
        return True


def _build_proposal(
    gap: CapabilityGap, blueprint: ToolBlueprint
) -> ImprovementProposal:
    """Wrap an authored blueprint in a ``TOOL_CREATION`` proposal."""
    rollback = RollbackPlan(
        operations=(
            RollbackOperation(
                operation_type="retire_tool",
                target=blueprint.name,
                description=f"Retire and unregister authored tool {blueprint.name!r}.",
            ),
        ),
        validation_check=f"tool {blueprint.name!r} is no longer registered",
    )
    return ImprovementProposal(
        altitude=ProposalAltitude.TOOL_CREATION,
        title=f"Author tool for {gap.signature}",
        description=(
            f"Authored a sandbox tool addressing the recurring "
            f"capability gap {gap.signature!r} "
            f"({gap.occurrences} occurrences)."
        ),
        rationale=ProposalRationale(
            signal_summary=(
                f"{gap.signature} requested {gap.occurrences} times in the window"
            ),
            pattern_detected="recurring capability gap",
            expected_impact=f"org can perform {gap.signature}",
            confidence_reasoning="gap recurrence threshold met",
        ),
        tool_changes=(blueprint,),
        rollback_plan=rollback,
        confidence=0.5,
        source_rule=NotBlankStr("capability_gap"),
    )


__all__ = ["ToolsmithService"]
