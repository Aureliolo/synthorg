"""Self-improvement service orchestrator.

Central service that ties together signal aggregation, rule
evaluation, strategy dispatch, guard chain, rollout execution,
and Chief of Staff confidence learning.
"""

import asyncio

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.memory.protocol import MemoryBackend
from synthorg.meta._service_config import _SECRET_PATHS, _redact_secrets
from synthorg.meta._service_lifecycle import SelfImprovementLifecycleMixin
from synthorg.meta._service_rollout import SelfImprovementRolloutMixin
from synthorg.meta.appliers.architecture_applier import ArchitectureApplierContext
from synthorg.meta.appliers.config_applier import (
    ConfigProvider,
    SettingsWritePort,
)
from synthorg.meta.appliers.prompt_applier import PromptApplierContext
from synthorg.meta.chief_of_staff.outcome_store import MemoryBackendOutcomeStore
from synthorg.meta.chief_of_staff.protocol import ConfidenceAdjuster
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.errors import SelfImprovementTriggerError
from synthorg.meta.factory import (
    build_appliers,
    build_confidence_adjuster,
    build_guards,
    build_regression_detector,
    build_rollout_strategies,
    build_rule_engine,
    build_strategies,
)
from synthorg.meta.models import (
    GuardVerdict,
    ImprovementCycleResult,
    ImprovementProposal,
    OrgSignalSnapshot,
    RuleMatch,
)
from synthorg.meta.protocol import ImprovementStrategy
from synthorg.meta.rollout.ab_test import AbTestRecordSink
from synthorg.meta.rollout.before_after import RolloutSnapshotBuilder
from synthorg.meta.rollout.group_aggregator import GroupSignalAggregator
from synthorg.meta.rollout.roster import OrgRoster
from synthorg.meta.rules.builtin import default_rules
from synthorg.meta.telemetry.factory import build_analytics_emitter
from synthorg.meta.telemetry.protocol import AnalyticsEmitter
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_CONFIDENCE_ADJUSTMENT_FAILED,
    COS_LEARNING_ENABLED,
    COS_OUTCOME_RECORD_FAILED,
)
from synthorg.observability.events.meta import (
    META_CYCLE_COMPLETED,
    META_CYCLE_NO_TRIGGERS,
    META_CYCLE_STARTED,
    META_CYCLE_TRIGGER_FAILED,
    META_CYCLE_TRIGGERED,
    META_PROPOSAL_GUARD_REJECTED,
)
from synthorg.providers.base import BaseCompletionProvider
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


class SelfImprovementService(
    SelfImprovementRolloutMixin,
    SelfImprovementLifecycleMixin,
):
    """Orchestrates the self-improvement meta-loop cycle.

    1. Evaluates signal snapshot against rules.
    2. Dispatches to strategies for matching altitudes.
    3. Adjusts proposal confidence via historical learning.
    4. Runs proposals through the guard chain.
    5. Returns proposals that passed all guards (ready for approval).

    Args:
        config: Self-improvement configuration.
        memory_backend: Memory backend for outcome learning.
        provider: Completion provider for LLM-based strategies.
            When code_modification_enabled is True but provider is
            None, the code modification strategy is silently skipped.
        config_provider: Zero-arg callable returning the current
            ``RootConfig`` snapshot.  Required for
            ``ConfigApplier.dry_run``; callers that omit it get an
            applier whose ``dry_run`` rejects with an explicit error.
        prompt_context: Read-only view of prompt-scope targets wired
            into ``PromptApplier.dry_run``.  Callers that omit it get
            an applier whose ``dry_run`` rejects with an explicit
            error.
        architecture_context: Read-only view of role / department /
            workflow registries wired into
            ``ArchitectureApplier.dry_run``.  Callers that omit it
            get an applier whose ``dry_run`` rejects with an explicit
            error.
        clock: Time source for rollout observation loops. Defaults to
            ``SystemClock`` when omitted; tests inject ``FakeClock`` for
            deterministic sleep behavior.
        roster: Live agent enumeration used to assign control/treatment
            groups and canary subsets. Defaults to ``NoOpOrgRoster``
            (empty roster); the engine layer should inject a real
            roster bound to the live agent registry.
        snapshot_builder: Async factory producing the current
            ``OrgSignalSnapshot`` during observation windows. Defaults
            to an empty snapshot; callers should wire this to the
            signal aggregator they use for rule evaluation.
        group_aggregator: Per-group metric sampler for A/B tests.
            Defaults to a null aggregator that emits no samples; the
            service layer wires ``TrackerGroupAggregator`` when the
            performance tracker is available.
        approval_store: Optional approval-gate store; required when
            ``config.enabled`` is True so the gate can enforce its
            policy. Construction fails fast if missing.
        config_resolver: Optional resolver wired into the
            ``engine.evolution_enabled`` kill-switch lookup. When
            ``None`` (test harness, anonymous boot), the per-call
            resolver short-circuits to the YAML-baked
            ``config.enabled`` fallback so the standalone constructor
            still works.
        ab_test_record_sink: Durable sink for A/B-test rollout records,
            threaded into the ``ABTestRollout`` strategy so completed
            rollouts surface through the ``/meta/ab-tests`` endpoints.
            ``None`` leaves A/B rollouts in-memory only.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        config: SelfImprovementConfig,
        memory_backend: MemoryBackend | None = None,
        provider: BaseCompletionProvider | None = None,
        config_provider: ConfigProvider | None = None,
        settings_writer: SettingsWritePort | None = None,
        prompt_context: PromptApplierContext | None = None,
        architecture_context: ArchitectureApplierContext | None = None,
        clock: Clock | None = None,
        roster: OrgRoster | None = None,
        snapshot_builder: RolloutSnapshotBuilder | None = None,
        group_aggregator: GroupSignalAggregator | None = None,
        approval_store: ApprovalStoreProtocol | None = None,
        config_resolver: ConfigResolver | None = None,
        ab_test_record_sink: AbTestRecordSink | None = None,
    ) -> None:
        if config.enabled and approval_store is None:
            # Fail-fast so callers notice at construction time rather
            # than observing a silently no-op cycle when the approval
            # gate rejects every proposal for lack of a store.
            msg = (
                "SelfImprovementService requires an approval_store when "
                "config.enabled is True; the approval gate cannot enforce "
                "mandatory human review without one."
            )
            raise ValueError(msg)
        self._config = config
        self._config_resolver = config_resolver
        # Hold direct references for facade methods (trigger_cycle,
        # get_config). Rollout strategies still receive these via the
        # build helper below; the references here are *not* a parallel
        # copy -- the same objects flow into the rollout layer.
        self._snapshot_builder = snapshot_builder
        # Default to SystemClock so trigger_cycle's wall-clock reads
        # always have a clock to call on, even when the caller didn't
        # pass one. Tests that need deterministic timestamps inject a
        # FakeClock here.
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._rule_engine = build_rule_engine(config)
        self._strategies = build_strategies(config, provider=provider)
        self._guards = build_guards(config, approval_store=approval_store)
        self._appliers = build_appliers(
            config,
            config_provider=config_provider,
            settings_writer=settings_writer,
            prompt_context=prompt_context,
            architecture_context=architecture_context,
        )
        self._detector = build_regression_detector()
        self._rollout_strategies = build_rollout_strategies(
            config,
            clock=clock,
            roster=roster,
            snapshot_builder=snapshot_builder,
            group_aggregator=group_aggregator,
            ab_test_record_sink=ab_test_record_sink,
        )

        # Cross-deployment analytics emitter.
        builtin_names = frozenset(r.name for r in default_rules())
        self._analytics_emitter: AnalyticsEmitter | None = build_analytics_emitter(
            config, builtin_rule_names=builtin_names
        )

        # Chief of Staff learning.
        self._outcome_store: MemoryBackendOutcomeStore | None = None
        self._confidence_adjuster: ConfidenceAdjuster | None = None
        if config.chief_of_staff.learning_enabled:
            if memory_backend is None:
                logger.warning(
                    COS_OUTCOME_RECORD_FAILED,
                    reason="learning_enabled_but_no_memory_backend",
                )
            else:
                self._outcome_store = MemoryBackendOutcomeStore(
                    backend=memory_backend,
                    agent_id=NotBlankStr("chief-of-staff"),
                    min_outcomes=config.chief_of_staff.min_outcomes,
                )
                self._confidence_adjuster = build_confidence_adjuster(config)
                logger.info(
                    COS_LEARNING_ENABLED,
                    strategy=config.chief_of_staff.adjuster_strategy,
                )

    async def run_cycle(
        self,
        snapshot: OrgSignalSnapshot,
    ) -> tuple[ImprovementProposal, ...]:
        """Run a complete improvement cycle.

        Evaluates rules, generates proposals, filters through
        guards, and returns proposals ready for human approval.

        Gated by ``engine.evolution_enabled``: when False the cycle
        short-circuits before rule evaluation so an operator can
        suspend the self-improvement loop without restarting the
        process.  Without a resolver wired we fall back to
        ``config.enabled`` (the YAML-baked switch) so standalone /
        test paths still honour the documented default.

        Args:
            snapshot: Current org-wide signal snapshot.

        Returns:
            Proposals that passed all guards (awaiting approval).
        """
        evolution_enabled = await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace="engine",
            key="evolution_enabled",
            fallback=self._config.enabled,
        )
        if not evolution_enabled:
            logger.info(
                META_CYCLE_NO_TRIGGERS,
                reason="evolution_disabled_by_setting",
            )
            return ()

        logger.info(META_CYCLE_STARTED)

        # Step 1: Evaluate rules.
        matches = self._rule_engine.evaluate(snapshot)
        if not matches:
            logger.info(META_CYCLE_NO_TRIGGERS)
            return ()

        # Step 2: Generate proposals from strategies (parallel).
        all_proposals = await self._dispatch_strategies(snapshot, matches)

        # Step 2.5: Adjust confidence via historical learning.
        # Uses return_exceptions=True so a single failed adjustment
        # does not discard results from successful adjustments.
        if self._confidence_adjuster is not None and self._outcome_store is not None:
            results = await asyncio.gather(
                *(
                    self._confidence_adjuster.adjust(
                        p,
                        self._outcome_store,
                    )
                    for p in all_proposals
                ),
                return_exceptions=True,
            )
            adjusted: list[ImprovementProposal] = []
            for original, adj_result in zip(
                all_proposals,
                results,
                strict=True,
            ):
                if isinstance(adj_result, BaseException):
                    logger.warning(
                        COS_CONFIDENCE_ADJUSTMENT_FAILED,
                        proposal_id=str(original.id),
                    )
                    adjusted.append(original)
                else:
                    adjusted.append(adj_result)
            all_proposals = adjusted

        # Step 3: Filter through guard chain.
        approved: list[ImprovementProposal] = []
        for proposal in all_proposals:
            passed = True
            for guard in self._guards:
                result = await guard.evaluate(proposal)
                if result.verdict == GuardVerdict.REJECTED:
                    logger.info(
                        META_PROPOSAL_GUARD_REJECTED,
                        guard=guard.name,
                        proposal_id=str(proposal.id),
                        reason=result.reason,
                    )
                    passed = False
                    break
            if passed:
                approved.append(proposal)

        logger.info(
            META_CYCLE_COMPLETED,
            total_matches=len(matches),
            proposals_generated=len(all_proposals),
            proposals_approved=len(approved),
        )
        return tuple(approved)

    async def _dispatch_strategies(
        self,
        snapshot: OrgSignalSnapshot,
        matches: tuple[RuleMatch, ...],
    ) -> list[ImprovementProposal]:
        """Run strategies in parallel via TaskGroup.

        Returns:
            List of the declared element type.
        """
        results: list[ImprovementProposal] = []

        async def _run(
            strategy: ImprovementStrategy,
            relevant: tuple[RuleMatch, ...],
        ) -> tuple[ImprovementProposal, ...]:
            """Return run."""
            return await strategy.propose(
                snapshot=snapshot,
                triggered_rules=relevant,
            )

        pairs: list[tuple[ImprovementStrategy, tuple[RuleMatch, ...]]] = []
        for strategy in self._strategies:
            relevant = tuple(
                m for m in matches if strategy.altitude in m.suggested_altitudes
            )
            if relevant:
                pairs.append((strategy, relevant))

        if pairs:
            async with asyncio.TaskGroup() as tg:
                tasks = [tg.create_task(_run(s, r)) for s, r in pairs]
            for task in tasks:
                results.extend(task.result())

        return results

    def get_config(self) -> dict[str, object]:
        """Return the active self-improvement config with secrets redacted.

        The dump preserves the exact field structure of
        :class:`SelfImprovementConfig` for ops debugging while masking
        every path in :data:`_SECRET_PATHS` -- callers (notably the MCP
        ``synthorg_meta_get_config`` tool) get a useful, auditable
        readout without leaking GitHub PATs into telemetry.

        Returns:
            Mapping with the declared key/value types.
        """
        dump = self._config.model_dump(mode="json")
        return _redact_secrets(dump, _SECRET_PATHS)

    async def trigger_cycle(self) -> ImprovementCycleResult:
        """Run a full improvement cycle synchronously and return the outcome.

        Builds an :class:`OrgSignalSnapshot` via the wired snapshot
        builder, then awaits :meth:`run_cycle`. The result wraps the
        produced proposals plus run timing so the MCP caller can
        identify and audit the trigger.

        Raises:
            SelfImprovementTriggerError: When no snapshot builder is
                configured. Triggering a cycle without real signals
                would only generate misleading proposals; failing
                fast is preferable to running a no-op against an
                empty snapshot.

        Returns:
            ``ImprovementCycleResult`` instance.
        """
        if self._snapshot_builder is None:
            msg = (
                "SelfImprovementService.trigger_cycle requires a wired "
                "snapshot_builder; the meta loop cannot generate "
                "useful proposals without live signals."
            )
            logger.warning(
                META_CYCLE_TRIGGER_FAILED,
                reason="no_snapshot_builder",
            )
            raise SelfImprovementTriggerError(msg)
        started_at = self._clock.now()
        # Capture monotonic alongside the wall-clock so duration is
        # immune to wall-clock jumps. ``started_at`` / ``completed_at``
        # remain wall-clock for the audit trail, but the reported
        # duration goes through monotonic deltas.
        started_monotonic = self._clock.monotonic()
        try:
            snapshot = await self._snapshot_builder()
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                META_CYCLE_TRIGGER_FAILED,
                reason="snapshot_builder_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # Normalise to ``SelfImprovementTriggerError`` so MCP
            # callers (and any other facade consumers) can map runtime
            # trigger failures to the same ``unavailable`` domain code
            # the missing-builder branch already emits, instead of
            # surfacing the raw provider/persistence exception.
            msg = "Failed to build self-improvement snapshot"
            raise SelfImprovementTriggerError(msg) from exc
        try:
            proposals = await self.run_cycle(snapshot)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                META_CYCLE_TRIGGER_FAILED,
                reason="run_cycle_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Self-improvement cycle execution failed"
            raise SelfImprovementTriggerError(msg) from exc
        completed_at = self._clock.now()
        result = ImprovementCycleResult(
            started_at=started_at,
            completed_at=completed_at,
            proposals=proposals,
        )
        logger.info(
            META_CYCLE_TRIGGERED,
            cycle_id=result.cycle_id,
            proposals_count=result.proposals_count,
            duration_seconds=self._clock.monotonic() - started_monotonic,
        )
        return result
