# module-kind: service
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

import asyncio
from datetime import datetime, timedelta
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
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
from synthorg.meta.protocol import ProposalGuard
from synthorg.meta.toolsmith.applier import ToolCreationApplier
from synthorg.meta.toolsmith.config import ToolsmithConfig
from synthorg.meta.toolsmith.dynamic_registry import DynamicToolRegistry
from synthorg.meta.toolsmith.errors import (
    ToolAuthoringError,
    ToolCapabilityNotAllowedError,
)
from synthorg.meta.toolsmith.models import (
    CapabilityGap,
    GapKind,
    ToolBlueprint,
    ToolBlueprintState,
)
from synthorg.meta.toolsmith.protocol import (
    CapabilityGapStore,
    ToolBlueprintGenerator,
    ToolCreationOverflowHandler,
)
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import SETTINGS_FETCH_FAILED
from synthorg.observability.events.toolsmith import (
    TOOLSMITH_AUTHOR_OVERFLOW_TO_CODE_MOD,
    TOOLSMITH_AUTHOR_SKIPPED,
    TOOLSMITH_CYCLE_COMPLETED,
    TOOLSMITH_CYCLE_STARTED,
    TOOLSMITH_GAP_RECURRING_DETECTED,
    TOOLSMITH_PROPOSAL_GUARD_REJECTED,
    TOOLSMITH_SERVICE_ABSENT_GAP,
)
from synthorg.persistence.tool_blueprint_protocol import (
    DynamicToolRepository,
    ToolBlueprintFilterSpec,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_TOOL_CREATION_KEY: Final[str] = "tool_creation_enabled"
_ALLOWED_CAPABILITIES_KEY: Final[str] = "tool_creation_allowed_capabilities"

# Recurring capability gaps that reach the threshold are real demand
# signals, but a single observation could still be noise; mid-confidence
# is the right starting prior until the gate-then-guards chain has
# additional evidence.
_TOOL_CREATION_CONFIDENCE: Final[float] = 0.5

# A blueprint in any of these states means the capability is already served
# or has an in-flight proposal, so re-authoring the recurring gap is a
# duplicate. RETIRED is excluded: a rolled-back capability may be re-proposed.
_NON_TERMINAL_BLUEPRINT_STATES: Final[frozenset[ToolBlueprintState]] = frozenset(
    {
        ToolBlueprintState.PENDING,
        ToolBlueprintState.VALIDATED,
        ToolBlueprintState.ACTIVE,
    }
)


class ToolsmithService:
    """Orchestrates capability-gap detection, authoring, and application.

    Args:
        config: Toolsmith configuration.
        gap_store: Capability-gap store (also the sink the service exposes).
        generator: Authors blueprints from gaps.
        applier: Validates and live-registers approved blueprints.
        guards: Sequential guard chain (scope, rollback, rate, approval).
        overflow_handler: Handles service-access gaps (optional).
        existing_capabilities: Static capability surface (dedup hint).
            The dynamic-registry capabilities are merged at gap-handling
            time via ``dynamic_registry`` (if provided), so the generator
            also avoids duplicating tools registered earlier in this run.
        dynamic_registry: Live dynamic-tool registry whose capabilities
            extend the dedup hint at call time. Optional so the service
            still works in tests that exercise authoring in isolation.
        clock: Time source.
        config_resolver: Optional resolver for the live
            ``self_improvement.tool_creation_enabled`` gate and the per-gap
            ``tool_creation_allowed_capabilities`` re-read. The service is
            wired unconditionally so the gate can flip on at runtime; when
            ``None`` (test harness) both reads fall back to the baked
            ``ToolsmithConfig`` so a disabled toolsmith stays fail-safe.
        blueprint_repo: Optional durable blueprint store used to dedup
            re-authoring (``_has_open_blueprint``) and to persist a PENDING
            blueprint for approve-to-live rehydration; ``None`` disables both.
        notification_dispatcher: Optional operator-alert sink for recurring
            ``SERVICE_ABSENT`` gaps; ``None`` disables that ops signal.
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
        dynamic_registry: DynamicToolRegistry | None = None,
        blueprint_repo: DynamicToolRepository | None = None,
        clock: Clock | None = None,
        config_resolver: ConfigResolver | None = None,
        notification_dispatcher: NotificationDispatcher | None = None,
    ) -> None:
        self._config = config
        self._gap_store = gap_store
        self._generator = generator
        self._applier = applier
        self._guards = guards
        self._overflow_handler = overflow_handler
        self._existing_capabilities = existing_capabilities
        self._dynamic_registry = dynamic_registry
        self._blueprint_repo = blueprint_repo
        self._clock = clock or SystemClock()
        self._config_resolver = config_resolver
        self._notification_dispatcher = notification_dispatcher
        # Capability signatures already alerted as service-absent, so a gap that
        # recurs every cycle raises a single actionable ops alert rather than
        # re-notifying the operator until the backing service is implemented.
        self._alerted_service_absent: set[str] = set()

    async def record_gap(
        self,
        signature: NotBlankStr,
        *,
        occurred_at: datetime | None = None,
        kind: GapKind = GapKind.MISSING_TOOL,
    ) -> None:
        """Record a capability-gap observation (the sink seam)."""
        await self._gap_store.record_gap(
            signature, occurred_at=occurred_at or self._clock.now(), kind=kind
        )

    async def _tool_creation_enabled(self) -> bool:
        """Resolve the live tool-creation master gate.

        Fail-safe to the baked ``ToolsmithConfig.enabled`` when no resolver
        is wired or the lookup fails, so the toolsmith (wired unconditionally)
        stays off by default and never authors when disabled.

        Returns:
            ``True`` when tool creation is live-enabled.
        """
        return await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace=SettingNamespace.SELF_IMPROVEMENT,
            key=_TOOL_CREATION_KEY,
            fallback=self._config.enabled,
        )

    async def _resolve_allowed_capabilities(self) -> frozenset[str]:
        """Re-read the capability allowlist live, per proposal.

        Falls back to the baked ``allowed_capabilities`` when no resolver is
        wired or the lookup fails, so an operator can narrow or widen the
        allowlist at runtime without a restart while a settings outage keeps
        the deployed allowlist.

        Returns:
            The set of capability tags the toolsmith may author for.
        """
        baked = frozenset(self._config.allowed_capabilities)
        if self._config_resolver is None:
            return baked
        try:
            raw = await self._config_resolver.get_json(
                SettingNamespace.SELF_IMPROVEMENT, _ALLOWED_CAPABILITIES_KEY
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace=str(SettingNamespace.SELF_IMPROVEMENT),
                key=_ALLOWED_CAPABILITIES_KEY,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return baked
        if not isinstance(raw, list) or not all(
            isinstance(tag, str) and tag.strip() for tag in raw
        ):
            # Fail closed: a malformed entry must not authorise a synthetic
            # capability name; fall back to the baked allowlist instead.
            return baked
        return frozenset(tag.strip() for tag in raw)

    async def run_cycle(
        self, *, now: datetime | None = None
    ) -> tuple[ImprovementProposal, ...]:
        """Detect recurring gaps, author proposals, and guard them.

        Per-gap authoring is parallelised via TaskGroup so the LLM round
        trip and guard chain run concurrently across gaps; each gap's
        per-call ``ToolCapabilityNotAllowedError`` / ``ToolAuthoringError``
        is caught inside ``_handle_gap`` so a single bad gap cannot abort
        the whole batch.

        Returns:
            Tuple of the declared element types.
        """
        if not await self._tool_creation_enabled():
            logger.info(
                TOOLSMITH_CYCLE_COMPLETED,
                gaps=0,
                proposals=0,
                note="tool_creation_disabled",
            )
            return ()
        moment = now or self._clock.now()
        logger.info(TOOLSMITH_CYCLE_STARTED)
        gaps = await self._gap_store.recurring(
            threshold=self._config.gap_recurrence_threshold,
            window=timedelta(hours=self._config.gap_window_hours),
            now=moment,
        )
        for gap in gaps:
            logger.info(
                TOOLSMITH_GAP_RECURRING_DETECTED,
                capability=gap.signature,
                occurrences=gap.occurrences,
                kind=gap.kind.value,
            )
        proposals: list[ImprovementProposal] = []
        if gaps:
            async with asyncio.TaskGroup() as tg:
                tasks = [tg.create_task(self._handle_gap(gap)) for gap in gaps]
            for task in tasks:
                proposals.extend(task.result())
        logger.info(
            TOOLSMITH_CYCLE_COMPLETED,
            gaps=len(gaps),
            proposals=len(proposals),
        )
        return tuple(proposals)

    async def apply(self, proposal: ImprovementProposal) -> ApplyResult:
        """Validate and live-register an approved tool-creation proposal.

        Rejects when tool creation is disabled live, so an operator turning
        the gate off blocks even an already-approved proposal from
        registering a new tool.

        Returns:
            ``ApplyResult`` instance.
        """
        if not await self._tool_creation_enabled():
            return ApplyResult(
                success=False,
                error_message=NotBlankStr(
                    "tool creation is disabled "
                    "(self_improvement.tool_creation_enabled is off)"
                ),
                changes_applied=0,
            )
        return await self._applier.apply(proposal)

    async def _handle_gap(self, gap: CapabilityGap) -> tuple[ImprovementProposal, ...]:
        """Author or overflow a single gap, then guard the result.

        A SERVICE_ABSENT gap (a wired handler whose backing service is not
        implemented) is a SynthOrg framework gap, not novel-tool demand, so it
        raises an operator ops signal and is never authored.

        Returns:
            Tuple of the declared element types.
        """
        if gap.kind is GapKind.SERVICE_ABSENT:
            await self._signal_service_absent(gap)
            return ()
        if gap.signature in self._config.service_access_capabilities:
            return await self._handle_overflow(gap)
        # Re-read the allowlist live per gap so an operator can narrow it at
        # runtime; a signature the deployed config allowed is skipped the
        # moment it leaves the live allowlist.
        if gap.signature not in await self._resolve_allowed_capabilities():
            logger.info(
                TOOLSMITH_AUTHOR_SKIPPED,
                capability=gap.signature,
                reason="not_in_live_allowlist",
            )
            return ()
        # A gap recurs across cycles while its first proposal is still pending
        # (or the tool is already validated/active). Re-authoring it would
        # collide on the UNIQUE tool name and pile duplicate approval items on
        # the operator, so skip once a non-terminal blueprint already exists.
        if await self._has_open_blueprint(gap.signature):
            logger.info(
                TOOLSMITH_AUTHOR_SKIPPED,
                capability=gap.signature,
                reason="blueprint_already_open",
            )
            return ()
        # Dedup hint = static surface known at boot + dynamic-registry
        # capabilities the applier registered earlier in this run. The
        # latter prevents the LLM from authoring a duplicate of a tool
        # added in the same cycle (it cannot see live state otherwise).
        existing = self._existing_capabilities
        if self._dynamic_registry is not None:
            existing = (*existing, *self._dynamic_registry.capabilities())
        try:
            blueprint = await self._generator.author(
                gap, existing_capabilities=existing
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
        if not await self._guards_pass(proposal):
            return ()
        # Persist the PENDING blueprint only after the guard chain accepts the
        # proposal: the approval gate has now registered an item referencing
        # this blueprint by id, so the approve-to-live consumer can rehydrate
        # it after an operator approves. Persisting before the guards would
        # leave an orphan PENDING row on rejection, which _has_open_blueprint
        # then treats as open and skips re-authoring forever. The applier
        # re-saves it (owning the lifecycle) on apply; this is the durable link.
        await self._persist_pending_blueprint(blueprint)
        return (proposal,)

    async def _has_open_blueprint(self, capability: str) -> bool:
        """True if a non-terminal blueprint for this capability already exists.

        Non-terminal = PENDING / VALIDATED / ACTIVE (an in-flight proposal or a
        live tool). RETIRED does not count, so a rolled-back capability can be
        re-proposed. A best-effort read: a query failure falls through to
        authoring rather than blocking the cycle.

        Returns:
            ``True`` when such a blueprint exists; ``False`` otherwise
            (including when no repo is wired or the query fails).
        """
        if self._blueprint_repo is None:
            return False
        try:
            existing = await self._blueprint_repo.query(
                ToolBlueprintFilterSpec(capability=NotBlankStr(capability))
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                TOOLSMITH_AUTHOR_SKIPPED,
                capability=capability,
                note="open_blueprint_check_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        return any(bp.state in _NON_TERMINAL_BLUEPRINT_STATES for bp in existing)

    async def _persist_pending_blueprint(self, blueprint: ToolBlueprint) -> None:
        """Durably store a PENDING blueprint (best-effort).

        A store failure is logged and swallowed so a transient persistence
        error never aborts the cycle. The guard chain has already registered an
        approval item referencing this blueprint's id, so a failed save leaves
        that approval unfulfillable (the consumer retires it and warns) -- an
        operator alert lets the operator re-propose rather than discovering it
        by log-grep, matching the other failure paths in this file.
        """
        if self._blueprint_repo is None:
            return
        try:
            await self._blueprint_repo.save(blueprint)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                TOOLSMITH_AUTHOR_SKIPPED,
                capability=blueprint.capability,
                note="pending_blueprint_persist_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            await self._notify_persist_failed(blueprint, safe_error_description(exc))

    async def _notify_persist_failed(
        self, blueprint: ToolBlueprint, reason: str
    ) -> None:
        """Surface a failed durable blueprint save to the operator (best-effort).

        A no-op when no dispatcher is wired; a dispatch failure is logged
        (criticals re-raised) and never aborts the cycle.
        """
        if self._notification_dispatcher is None:
            return
        from synthorg.notifications.models import (  # noqa: PLC0415
            Notification,
            NotificationCategory,
            NotificationSeverity,
        )

        body = (
            f"Blueprint for capability {blueprint.capability!r} could not be "
            f"durably saved: {reason}. Any approval referencing it is "
            "unfulfillable; re-propose the tool to try again."
        )
        try:
            await self._notification_dispatcher.dispatch(
                Notification(
                    category=NotificationCategory.SYSTEM,
                    severity=NotificationSeverity.WARNING,
                    title="Tool blueprint failed to persist",
                    body=body,
                    source="meta.toolsmith",
                ),
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                TOOLSMITH_AUTHOR_SKIPPED,
                capability=blueprint.capability,
                note="persist_failed_alert_dispatch_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _signal_service_absent(self, gap: CapabilityGap) -> None:
        """Raise an operator ops signal for a recurring service-absent gap.

        The capability exists as a wired MCP handler but its backing SynthOrg
        service is not implemented in this deployment: the fix is to implement
        the service in SynthOrg, not to author a sandbox tool. Best-effort: a
        dispatch failure is logged (criticals re-raised) and never aborts the
        cycle; a no-op when no dispatcher is wired.
        """
        logger.info(
            TOOLSMITH_SERVICE_ABSENT_GAP,
            capability=gap.signature,
            occurrences=gap.occurrences,
            dispatcher_wired=self._notification_dispatcher is not None,
        )
        if self._notification_dispatcher is None:
            return
        # One alert per capability until the backing service is implemented: the
        # gap recurs every cycle, so re-dispatching would spam the operator with
        # the same actionable signal (the dispatcher only fans out, never dedups).
        if gap.signature in self._alerted_service_absent:
            return
        from synthorg.notifications.models import (  # noqa: PLC0415
            Notification,
            NotificationCategory,
            NotificationSeverity,
        )

        body = (
            f"MCP tool {gap.signature!r} was requested {gap.occurrences} times "
            "but its handler has no backing SynthOrg service in this "
            "deployment. Implement/wire the service in SynthOrg (this is a "
            "framework gap, not a tool to author)."
        )
        try:
            await self._notification_dispatcher.dispatch(
                Notification(
                    category=NotificationCategory.SYSTEM,
                    severity=NotificationSeverity.WARNING,
                    title="Wired MCP tool has no backing service",
                    body=body,
                    source="meta.toolsmith",
                ),
            )
            self._alerted_service_absent.add(gap.signature)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                TOOLSMITH_SERVICE_ABSENT_GAP,
                capability=gap.signature,
                note="ops_signal_dispatch_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _handle_overflow(
        self, gap: CapabilityGap
    ) -> tuple[ImprovementProposal, ...]:
        """Route a service-access gap to the code-modification overflow.

        Returns:
            Tuple of the declared element types.
        """
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
        """Evaluate the guard chain sequentially; all must pass.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
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
    """Wrap an authored blueprint in a ``TOOL_CREATION`` proposal.

    Returns:
        ``ImprovementProposal`` instance.
    """
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
        confidence=_TOOL_CREATION_CONFIDENCE,
        source_rule=NotBlankStr("capability_gap"),
    )


__all__ = ["ToolsmithService"]
