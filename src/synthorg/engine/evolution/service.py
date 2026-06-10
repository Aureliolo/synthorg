"""Evolution service -- orchestrates the evolution pipeline.

Trigger -> build context -> proposer -> guards -> adapter.apply.
"""

import asyncio
import copy
from types import MappingProxyType

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.evolution.config import EvolutionConfig
from synthorg.engine.evolution.models import (
    AdaptationAxis,
    AdaptationDecision,
    AdaptationProposal,
    EvolutionEvent,
)
from synthorg.engine.evolution.protocols import (
    AdaptationAdapter,
    AdaptationGuard,
    AdaptationProposer,
    EvolutionContext,
    EvolutionTrigger,
)
from synthorg.engine.identity.store.protocol import IdentityVersionStore
from synthorg.hr.performance.models import AgentPerformanceSnapshot
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.memory.models import MemoryEntry
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evolution import (
    EVOLUTION_ADAPTATION_FAILED,
    EVOLUTION_ADAPTED,
    EVOLUTION_CONTEXT_BUILD_FAILED,
    EVOLUTION_CONTEXT_MEMORY_FAILED,
    EVOLUTION_CONTEXT_SNAPSHOT_FAILED,
    EVOLUTION_GUARDS_PASSED,
    EVOLUTION_GUARDS_REJECTED,
    EVOLUTION_PROPOSAL_GENERATED,
    EVOLUTION_SERVICE_COMPLETE,
    EVOLUTION_SERVICE_STARTED,
    EVOLUTION_TRIGGER_SKIPPED,
)

logger = get_logger(__name__)


class EvolutionService:
    """Orchestrates the agent evolution pipeline.

    Pipeline:
    1. Build ``EvolutionContext`` from identity store, tracker, memory
    2. Call proposer to generate ``AdaptationProposal``(s)
    3. For each proposal, run through guards
    4. For approved proposals, dispatch to matching adapter by axis
    5. Record ``EvolutionEvent``

    Args:
        identity_store: Versioned identity storage.
        tracker: Performance tracker for snapshot data.
        proposer: Adaptation proposer strategy.
        guard: Adaptation guard (typically CompositeGuard).
        adapters: Mapping from axis to adapter.
        trigger: Evolution trigger (None = triggers disabled).
        memory_backend: Memory backend for context retrieval.
        config: Evolution configuration.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        identity_store: IdentityVersionStore,
        tracker: PerformanceTracker,
        proposer: AdaptationProposer,
        guard: AdaptationGuard,
        adapters: dict[AdaptationAxis, AdaptationAdapter],
        trigger: EvolutionTrigger | None = None,
        memory_backend: MemoryBackend | None = None,
        config: EvolutionConfig,
    ) -> None:
        self._identity_store = identity_store
        self._tracker = tracker
        self._trigger = trigger
        self._proposer = proposer
        self._guard = guard
        self._adapters = MappingProxyType(copy.deepcopy(adapters))
        self._memory_backend = memory_backend
        self._config = config

    async def evolve(
        self,
        *,
        agent_id: NotBlankStr,
    ) -> tuple[EvolutionEvent, ...]:
        """Run the evolution pipeline for an agent.

        Args:
            agent_id: Agent to evolve.

        Returns:
            Tuple of evolution events (one per proposal).
            Empty if no proposals or all rejected.

        Raises:
            asyncio.CancelledError: If the evolution task is cancelled
                during context build.
        """
        if not self._config.enabled:
            return ()

        logger.info(
            EVOLUTION_SERVICE_STARTED,
            agent_id=str(agent_id),
        )

        # 1. Build context.
        try:
            context = await self._build_context(agent_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                EVOLUTION_CONTEXT_BUILD_FAILED,
                agent_id=str(agent_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ()

        # 1b. Check trigger (skip if trigger says no).
        if self._trigger is not None:
            should_run = await self._trigger.should_trigger(
                agent_id=agent_id,
                context=context,
            )
            if not should_run:
                logger.debug(
                    EVOLUTION_TRIGGER_SKIPPED,
                    agent_id=str(agent_id),
                    trigger=self._trigger.name,
                )
                return ()

        # 2. Generate proposals.
        proposals = await self._proposer.propose(
            agent_id=agent_id,
            context=context,
        )
        if not proposals:
            logger.info(
                EVOLUTION_SERVICE_COMPLETE,
                agent_id=str(agent_id),
                proposals=0,
                applied=0,
            )
            return ()

        logger.info(
            EVOLUTION_PROPOSAL_GENERATED,
            agent_id=str(agent_id),
            proposal_count=len(proposals),
        )

        # 3-4. Evaluate guards and apply.
        events: list[EvolutionEvent] = []
        for proposal in proposals:
            event = await self._process_proposal(agent_id, proposal)
            events.append(event)

        applied_count = sum(1 for e in events if e.applied)
        logger.info(
            EVOLUTION_SERVICE_COMPLETE,
            agent_id=str(agent_id),
            proposals=len(proposals),
            applied=applied_count,
        )
        return tuple(events)

    async def _process_proposal(
        self,
        agent_id: NotBlankStr,
        proposal: AdaptationProposal,
    ) -> EvolutionEvent:
        """Evaluate a single proposal through guards and apply.

        Returns:
            An :class:`EvolutionEvent` describing the proposal, the
            guard decision, and whether the adapter applied it.
        """
        # Check if the axis is enabled.
        axis_enabled = self._is_axis_enabled(proposal.axis)
        if not axis_enabled:
            decision = AdaptationDecision(
                proposal_id=proposal.id,
                approved=False,
                guard_name="config",
                reason=f"axis {proposal.axis.value} is disabled",
            )
            return EvolutionEvent(
                agent_id=agent_id,
                proposal=proposal,
                decision=decision,
                applied=False,
            )

        # Run through guards.
        decision = await self._guard.evaluate(proposal)
        if not decision.approved:
            logger.info(
                EVOLUTION_GUARDS_REJECTED,
                agent_id=str(agent_id),
                axis=proposal.axis.value,
                guard=str(decision.guard_name),
                reason=str(decision.reason),
            )
            return EvolutionEvent(
                agent_id=agent_id,
                proposal=proposal,
                decision=decision,
                applied=False,
            )

        logger.info(
            EVOLUTION_GUARDS_PASSED,
            agent_id=str(agent_id),
            axis=proposal.axis.value,
        )

        # Get version before adaptation.
        version_before = await self._get_version_before(agent_id, proposal)

        # Apply the adaptation.
        adapter = self._adapters.get(proposal.axis)
        if adapter is None:
            logger.warning(
                EVOLUTION_ADAPTATION_FAILED,
                agent_id=str(agent_id),
                axis=proposal.axis.value,
                error=f"no adapter for axis {proposal.axis.value}",
            )
            return EvolutionEvent(
                agent_id=agent_id,
                proposal=proposal,
                decision=decision,
                applied=False,
            )

        # Try to apply the adaptation.
        success = await self._apply_adaptation(
            agent_id,
            proposal,
            adapter,
        )
        if not success:
            return EvolutionEvent(
                agent_id=agent_id,
                proposal=proposal,
                decision=decision,
                applied=False,
            )

        # Get version after adaptation.
        version_after = await self._get_version_after(agent_id, proposal)

        logger.info(
            EVOLUTION_ADAPTED,
            agent_id=str(agent_id),
            axis=proposal.axis.value,
            version_before=version_before,
            version_after=version_after,
        )

        return EvolutionEvent(
            agent_id=agent_id,
            proposal=proposal,
            decision=decision,
            applied=True,
            identity_version_before=version_before,
            identity_version_after=version_after,
        )

    def _is_axis_enabled(self, axis: AdaptationAxis) -> bool:
        """Check if an adaptation axis is enabled in config.

        Returns:
            ``True`` when the matching ``EvolutionConfig.adapters``
            flag is on, ``False`` otherwise.
        """
        adapter_cfg = self._config.adapters
        if axis == AdaptationAxis.IDENTITY:
            return adapter_cfg.identity
        if axis == AdaptationAxis.STRATEGY_SELECTION:
            return adapter_cfg.strategy_selection
        if axis == AdaptationAxis.PROMPT_TEMPLATE:
            return adapter_cfg.prompt_template
        return False  # type: ignore[unreachable]  # pragma: no cover

    async def _get_version_before(
        self,
        agent_id: NotBlankStr,
        proposal: AdaptationProposal,
    ) -> int | None:
        """Get identity version before adaptation (for identity axis only).

        Returns:
            The current identity version, or ``None`` when the
            proposal is not on the identity axis or no versions
            exist.
        """
        if proposal.axis != AdaptationAxis.IDENTITY:
            return None

        versions = await self._identity_store.list_versions(agent_id)
        return versions[0].version if versions else None

    async def _apply_adaptation(
        self,
        agent_id: NotBlankStr,
        proposal: AdaptationProposal,
        adapter: AdaptationAdapter,
    ) -> bool:
        """Apply the adaptation. Return True on success, False on failure.

        Returns:
            ``True`` when ``adapter.apply`` completes without raising,
            ``False`` when the adapter raises a non-critical exception
            (logged via :data:`EVOLUTION_ADAPTATION_FAILED`).

        Raises:
            asyncio.CancelledError: If the task is cancelled mid-apply
                (the adapter is given no time to clean up; treat the
                adaptation as not-applied).
        """
        try:
            await adapter.apply(proposal, agent_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # Adapter should log via EVOLUTION_ADAPTATION_FAILED;
            # this defensive fallback runs only when the adapter
            # omits the failure event. Log at WARNING (not DEBUG)
            # because a `False` return is an error outcome, and
            # operators must see the underlying cause -- otherwise
            # the failure is invisible at the operator's default
            # log level.
            logger.warning(
                EVOLUTION_ADAPTATION_FAILED,
                agent_id=str(agent_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                source="service_fallback",
            )
            return False
        else:
            return True

    async def _get_version_after(
        self,
        agent_id: NotBlankStr,
        proposal: AdaptationProposal,
    ) -> int | None:
        """Get identity version after adaptation (for identity axis only).

        Returns:
            The new identity version, or ``None`` when the proposal
            is not on the identity axis or no versions exist.
        """
        if proposal.axis != AdaptationAxis.IDENTITY:
            return None

        versions = await self._identity_store.list_versions(agent_id)
        return versions[0].version if versions else None

    async def _build_context(
        self,
        agent_id: NotBlankStr,
    ) -> EvolutionContext:
        """Build the evolution context for an agent.

        Returns:
            A populated :class:`EvolutionContext` carrying the current
            identity, performance snapshot, procedural memories, and
            up to the last 20 task metrics.

        Raises:
            ValueError: If ``agent_id`` is not in the identity store.
        """
        identity = await self._identity_store.get_current(agent_id)
        if identity is None:
            msg = f"Agent {agent_id!r} not found in identity store"
            logger.warning(
                EVOLUTION_CONTEXT_BUILD_FAILED,
                agent_id=str(agent_id),
                error=msg,
            )
            raise ValueError(msg)

        # Fetch performance snapshot.
        snapshot = await self._fetch_performance_snapshot(agent_id)

        # Fetch procedural memories.
        memories = await self._fetch_procedural_memories(agent_id)

        # Get recent task metrics (limit to most recent 20).
        task_results = self._tracker.get_task_metrics(agent_id=agent_id)
        recent_tasks = task_results[-20:] if task_results else ()

        return EvolutionContext(
            agent_id=agent_id,
            identity=identity,
            performance_snapshot=snapshot,
            recent_task_results=tuple(recent_tasks),
            recent_procedural_memories=memories,
        )

    async def _fetch_performance_snapshot(
        self,
        agent_id: NotBlankStr,
    ) -> AgentPerformanceSnapshot | None:
        """Fetch performance snapshot (best-effort).

        Returns:
            The :class:`AgentPerformanceSnapshot` from the tracker, or
            ``None`` when the tracker raises a non-critical exception.
        """
        try:
            return await self._tracker.get_snapshot(agent_id)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                EVOLUTION_CONTEXT_SNAPSHOT_FAILED,
                agent_id=str(agent_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

    async def _fetch_procedural_memories(
        self,
        agent_id: NotBlankStr,
    ) -> tuple[MemoryEntry, ...]:
        """Fetch procedural memories (best-effort).

        Returns:
            A tuple of procedural :class:`MemoryEntry` (up to 10) from
            the configured memory backend, or an empty tuple when no
            backend is wired or the backend raises a non-critical
            exception.
        """
        if self._memory_backend is None:
            return ()

        try:
            from synthorg.core.memory_enums import MemoryCategory  # noqa: PLC0415
            from synthorg.memory.models import (  # noqa: PLC0415
                MemoryQuery,
            )

            return await self._memory_backend.retrieve(
                agent_id,
                MemoryQuery(
                    text="procedural evolution context",
                    categories=frozenset([MemoryCategory.PROCEDURAL]),
                    limit=10,
                ),
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                EVOLUTION_CONTEXT_MEMORY_FAILED,
                agent_id=str(agent_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ()
