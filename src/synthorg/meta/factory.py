"""Factory for the self-improvement meta-loop components.

Constructs strategies, guards, appliers, rollout strategies,
and regression detectors from configuration, filtering by
enabled altitudes and disabled rules.
"""

from collections.abc import Mapping
from copy import deepcopy
from types import MappingProxyType
from typing import assert_never

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.clock import Clock
from synthorg.core.types import NotBlankStr
from synthorg.meta.appliers.architecture_applier import (
    ArchitectureApplier,
    ArchitectureApplierContext,
)
from synthorg.meta.appliers.config_applier import (
    ConfigApplier,
    ConfigProvider,
    SettingsWritePort,
)
from synthorg.meta.appliers.prompt_applier import (
    PromptApplier,
    PromptApplierContext,
)
from synthorg.meta.chief_of_staff.learning import (
    BayesianConfidenceAdjuster,
    ExponentialMovingAverageAdjuster,
)
from synthorg.meta.chief_of_staff.protocol import ConfidenceAdjuster
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.guards.approval_gate import ApprovalGateGuard
from synthorg.meta.guards.rate_limit import RateLimitGuard
from synthorg.meta.guards.rollback_plan import RollbackPlanGuard
from synthorg.meta.guards.scope_check import ScopeCheckGuard
from synthorg.meta.models import ProposalAltitude
from synthorg.meta.protocol import ImprovementStrategy, ProposalApplier, ProposalGuard
from synthorg.meta.rollout.ab_record import AbTestRecordSink
from synthorg.meta.rollout.ab_test import ABTestRollout
from synthorg.meta.rollout.before_after import (
    BeforeAfterRollout,
    RolloutSnapshotBuilder,
)
from synthorg.meta.rollout.canary import CanarySubsetRollout
from synthorg.meta.rollout.group_aggregator import GroupSignalAggregator
from synthorg.meta.rollout.inverse_dispatch import (
    ArchitectureMutator,
    BranchMutator,
    CodeMutator,
    ConfigMutator,
    PrincipleRemovalMutator,
    PromptMutator,
    RollbackHandler,
    default_rollback_handlers,
)
from synthorg.meta.rollout.regression.composite import (
    TieredRegressionDetector,
)
from synthorg.meta.rollout.rollback import RollbackExecutor
from synthorg.meta.rollout.roster import OrgRoster
from synthorg.meta.rules.builtin import default_rules
from synthorg.meta.rules.engine import RuleEngine
from synthorg.meta.strategies.architecture import (
    ArchitectureProposalStrategy,
)
from synthorg.meta.strategies.config_tuning import ConfigTuningStrategy
from synthorg.meta.strategies.prompt_tuning import PromptTuningStrategy
from synthorg.meta.validation.scope_validator import ScopeValidator
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_CONFIG_LOADED,
    META_STRATEGY_REGISTERED,
)
from synthorg.providers.base import BaseCompletionProvider

logger = get_logger(__name__)


def build_rule_engine(
    config: SelfImprovementConfig,
) -> RuleEngine:
    """Build a RuleEngine from configuration.

    Loads default rules, filters out disabled rules.

    Args:
        config: Self-improvement configuration.

    Returns:
        Configured RuleEngine.
    """
    all_rules = default_rules()
    disabled = set(config.rules.disabled_rules)
    enabled = tuple(r for r in all_rules if r.name not in disabled)
    logger.info(
        META_CONFIG_LOADED,
        total_rules=len(all_rules),
        enabled_rules=len(enabled),
        disabled_rules=list(disabled),
    )
    return RuleEngine(rules=enabled)


def build_strategies(
    config: SelfImprovementConfig,
    *,
    provider: BaseCompletionProvider | None = None,
) -> tuple[ImprovementStrategy, ...]:
    """Build enabled improvement strategies.

    Args:
        config: Self-improvement configuration.
        provider: Completion provider for LLM-based strategies
            (required when code_modification_enabled is True).

    Returns:
        Tuple of enabled strategies.
    """
    strategies: list[ImprovementStrategy] = []
    if config.config_tuning_enabled:
        strategies.append(ConfigTuningStrategy(config=config))
        logger.debug(
            META_STRATEGY_REGISTERED,
            altitude="config_tuning",
        )
    if config.architecture_proposals_enabled:
        strategies.append(ArchitectureProposalStrategy(config=config))
        logger.debug(
            META_STRATEGY_REGISTERED,
            altitude="architecture",
        )
    if config.prompt_tuning_enabled:
        strategies.append(PromptTuningStrategy(config=config))
        logger.debug(
            META_STRATEGY_REGISTERED,
            altitude="prompt_tuning",
        )
    if config.code_modification_enabled:
        if provider is None:
            logger.warning(
                META_STRATEGY_REGISTERED,
                altitude="code_modification",
                reason="skipped_no_provider",
            )
        else:
            from synthorg.meta.strategies.code_modification import (  # noqa: PLC0415
                CodeModificationStrategy,
            )

            scope_validator = ScopeValidator(
                allowed_paths=tuple(
                    config.code_modification.allowed_paths,
                ),
                forbidden_paths=tuple(
                    config.code_modification.forbidden_paths,
                ),
            )
            strategies.append(
                CodeModificationStrategy(
                    config=config,
                    provider=provider,
                    scope_validator=scope_validator,
                ),
            )
            logger.debug(
                META_STRATEGY_REGISTERED,
                altitude="code_modification",
            )
    return tuple(strategies)


def build_guards(
    config: SelfImprovementConfig,
    *,
    approval_store: ApprovalStoreProtocol | None = None,
) -> tuple[ProposalGuard, ...]:
    """Build the proposal guard chain.

    Guards are always in this order: scope check, rollback plan,
    rate limit, approval gate.

    Args:
        config: Self-improvement configuration.
        approval_store: Approval store routed into
            :class:`ApprovalGateGuard` so proposals are durably
            registered before proceeding.  When ``None`` the
            approval gate fails closed -- every proposal is
            rejected because the mandatory-review invariant
            cannot be enforced.

    Returns:
        Tuple of guards in evaluation order.
    """
    return (
        ScopeCheckGuard(config=config),
        RollbackPlanGuard(),
        RateLimitGuard(
            max_proposals=config.guards.proposal_rate_limit,
            window_hours=config.guards.rate_limit_window_hours,
        ),
        ApprovalGateGuard(approval_store=approval_store),
    )


def build_appliers(
    config: SelfImprovementConfig | None = None,
    *,
    config_provider: ConfigProvider | None = None,
    settings_writer: SettingsWritePort | None = None,
    prompt_context: PromptApplierContext | None = None,
    architecture_context: ArchitectureApplierContext | None = None,
) -> Mapping[ProposalAltitude, ProposalApplier]:
    """Build proposal appliers for each altitude.

    Args:
        config: Self-improvement configuration. When provided
            and ``code_modification_enabled``, includes the
            ``CodeApplier``.
        config_provider: Zero-arg callable returning the current
            ``RootConfig``.  Required for ``ConfigApplier.dry_run``
            to validate changes; callers that do not provide it get
            an applier whose ``dry_run`` returns an explicit error.
        settings_writer: Settings read/write seam threaded into the
            ``ConfigApplier`` so its ``apply`` persists changes; ``None``
            leaves ``apply`` rejecting proposals with an explicit error.
        prompt_context: Read-only view of prompt-scope targets.
            Required for ``PromptApplier.dry_run``.
        architecture_context: Read-only view of role / department /
            workflow registries.  Required for
            ``ArchitectureApplier.dry_run``.

    Returns:
        Read-only mapping of altitude to applier.
    """
    appliers: dict[ProposalAltitude, ProposalApplier] = {
        ProposalAltitude.CONFIG_TUNING: ConfigApplier(
            config_provider=config_provider,
            settings_writer=settings_writer,
        ),
        ProposalAltitude.ARCHITECTURE: ArchitectureApplier(
            context=architecture_context,
        ),
        ProposalAltitude.PROMPT_TUNING: PromptApplier(context=prompt_context),
    }
    if config is not None and config.code_modification_enabled:
        code_cfg = config.code_modification
        if code_cfg.github_token is None or code_cfg.github_repo is None:
            logger.warning(
                META_STRATEGY_REGISTERED,
                altitude="code_modification_applier",
                reason="skipped_no_github_credentials",
            )
        elif code_cfg.project_root is None:
            # Fail closed: the CI validator must run against an explicit,
            # absolute checkout. Defaulting to the process CWD would point
            # ruff / mypy / pytest at whatever tree the worker happened to
            # start in, so an unset project_root disables the applier
            # rather than silently validating the wrong files.
            logger.warning(
                META_STRATEGY_REGISTERED,
                altitude="code_modification_applier",
                reason="skipped_no_project_root",
            )
        else:
            from pathlib import Path  # noqa: PLC0415

            from synthorg.meta.appliers.code_applier import (  # noqa: PLC0415
                CodeApplier,
            )
            from synthorg.meta.appliers.github_client import (  # noqa: PLC0415
                HttpGitHubClient,
            )
            from synthorg.meta.validation.ci_validator import (  # noqa: PLC0415
                LocalCIValidator,
            )

            ci_validator = LocalCIValidator(
                project_root=Path(str(code_cfg.project_root)).resolve(),
                scope_validator=ScopeValidator(
                    allowed_paths=tuple(code_cfg.allowed_paths),
                    forbidden_paths=tuple(code_cfg.forbidden_paths),
                ),
                timeout_seconds=code_cfg.ci_timeout_seconds,
            )
            github_client = HttpGitHubClient(
                token=str(code_cfg.github_token),
                repo=str(code_cfg.github_repo),
                api_base_url=str(code_cfg.github_api_url),
                base_branch=str(code_cfg.base_branch),
                timeout=code_cfg.api_timeout_seconds,
            )
            appliers[ProposalAltitude.CODE_MODIFICATION] = CodeApplier(
                ci_validator=ci_validator,
                github_client=github_client,
                code_modification_config=code_cfg,
            )
    return MappingProxyType(deepcopy(appliers))


def build_regression_detector() -> TieredRegressionDetector:
    """Build the tiered regression detector.

    Returns:
        Configured TieredRegressionDetector.
    """
    return TieredRegressionDetector()


def build_confidence_adjuster(
    config: SelfImprovementConfig,
) -> ConfidenceAdjuster:
    """Build a confidence adjuster strategy from config.

    Args:
        config: Self-improvement configuration.

    Returns:
        Configured confidence adjuster.
    """
    strategy = config.chief_of_staff.adjuster_strategy
    if strategy == "ema":
        return ExponentialMovingAverageAdjuster(
            alpha=config.chief_of_staff.ema_alpha,
        )
    if strategy == "bayesian":
        return BayesianConfidenceAdjuster()
    assert_never(strategy)


def build_rollout_strategies(  # noqa: PLR0913
    config: SelfImprovementConfig | None = None,
    *,
    clock: Clock | None = None,
    roster: OrgRoster | None = None,
    snapshot_builder: RolloutSnapshotBuilder | None = None,
    group_aggregator: GroupSignalAggregator | None = None,
    ab_test_record_sink: AbTestRecordSink | None = None,
) -> Mapping[str, BeforeAfterRollout | CanarySubsetRollout | ABTestRollout]:
    """Build available rollout strategies wired with injected dependencies.

    Args:
        config: Self-improvement configuration. When provided, supplies
            A/B test config, observation window, and check interval.
        clock: Clock for sleeping and timestamping. Defaults to
            ``SystemClock`` when omitted.
        roster: Live agent roster. Defaults to ``NoOpOrgRoster``; the
            service layer should inject a real roster.
        snapshot_builder: Async factory producing the current signal
            snapshot. Defaults to an empty snapshot.
        group_aggregator: Per-group sample aggregator. Defaults to a
            null aggregator that emits no samples.
        ab_test_record_sink: Durable sink for A/B-test rollout records.
            When wired, each ``ABTestRollout`` persists a record so the
            ``/meta/ab-tests`` endpoints surface real rollouts. ``None``
            (no persistence) leaves the rollout in-memory only.

    Returns:
        Read-only mapping of strategy name to rollout strategy.
    """
    ab_cfg = config.rollout.ab_test if config else None
    check_interval = (
        float(config.rollout.regression_check_interval_hours) if config else 4.0
    )
    strategies: dict[str, BeforeAfterRollout | CanarySubsetRollout | ABTestRollout] = {
        "before_after": BeforeAfterRollout(
            clock=clock,
            snapshot_builder=snapshot_builder,
            check_interval_hours=check_interval,
        ),
        "canary": CanarySubsetRollout(
            clock=clock,
            roster=roster,
            snapshot_builder=snapshot_builder,
            check_interval_hours=check_interval,
        ),
        "ab_test": ABTestRollout(
            control_fraction=(ab_cfg.control_fraction if ab_cfg else 0.5),
            min_agents_per_group=(ab_cfg.min_agents_per_group if ab_cfg else 5),
            min_observations_per_group=(
                ab_cfg.min_observations_per_group if ab_cfg else 10
            ),
            improvement_threshold=(ab_cfg.improvement_threshold if ab_cfg else 0.15),
            clock=clock,
            roster=roster,
            group_aggregator=group_aggregator,
            check_interval_hours=check_interval,
            record_sink=ab_test_record_sink,
        ),
    }
    # Intentionally no deepcopy: injected Clock/OrgRoster/
    # GroupSignalAggregator carry shared runtime state (e.g.
    # FakeClock's sleep_calls list) that callers and tests need to
    # observe via identity. MappingProxyType keeps the dispatch
    # mapping read-only; the strategy instances themselves are
    # immutable-by-design (no setters).
    return MappingProxyType(strategies)


def build_rollback_executor(  # noqa: PLR0913
    *,
    config_mutator: ConfigMutator,
    prompt_mutator: PromptMutator,
    architecture_mutator: ArchitectureMutator,
    code_mutator: CodeMutator,
    principle_removal_mutator: PrincipleRemovalMutator | None = None,
    branch_mutator: BranchMutator | None = None,
    extra_handlers: Mapping[str, RollbackHandler] | None = None,
) -> RollbackExecutor:
    """Assemble a RollbackExecutor with the default handler mapping.

    Args:
        config_mutator: Writes config leaves at dotted paths.
        prompt_mutator: Restores org-wide prompt principle text (overlay).
        architecture_mutator: Restores structural entities.
        code_mutator: Reverts source files to previous contents.
        principle_removal_mutator: Removes an active principle created by a
            prompt apply (the inverse of an ADD). When omitted the
            ``remove_principle`` operation has no handler.
        branch_mutator: Deletes a remote branch created by a code apply.
            When omitted the ``revert_branch`` operation has no handler.
        extra_handlers: Additional handlers keyed by operation type,
            merged on top of the defaults (later keys win).

    Returns:
        A RollbackExecutor ready to dispatch the built-in operation types
        plus any extras.
    """
    handlers: dict[NotBlankStr, RollbackHandler] = dict(
        default_rollback_handlers(
            config=config_mutator,
            prompt=prompt_mutator,
            architecture=architecture_mutator,
            code=code_mutator,
            principle_removal=principle_removal_mutator,
            branch=branch_mutator,
        )
    )
    if extra_handlers:
        for key, handler in extra_handlers.items():
            handlers[NotBlankStr(key)] = handler
    return RollbackExecutor(handlers=handlers)
