"""Factory that wires the self-extending toolkit from configuration.

Constructs the gap store, dynamic registry, blueprint generator, benchmark
gate, applier, guard chain, and the orchestrating :class:`ToolsmithService`
from a :class:`SelfImprovementConfig` plus the runtime dependencies the
boot layer resolves (provider, blueprint repository, sandbox backend,
golden-scorecard provider, approval store).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr
from synthorg.meta.factory import build_guards
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.toolsmith.applier import ToolCreationApplier
from synthorg.meta.toolsmith.approval_consumer import ToolApprovalConsumer
from synthorg.meta.toolsmith.dynamic_registry import DynamicToolRegistry
from synthorg.meta.toolsmith.gap_store import RingBufferCapabilityGapStore
from synthorg.meta.toolsmith.models import ToolBlueprint
from synthorg.meta.toolsmith.script_handler import make_dynamic_tool_handler
from synthorg.meta.toolsmith.service import ToolsmithService
from synthorg.meta.toolsmith.strategy import LLMToolBlueprintGenerator
from synthorg.meta.toolsmith.validation_gate import (
    BenchmarkToolValidationGate,
    SandboxBriefRunner,
    SandboxResolver,
)
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.providers.protocol import ConnectionSelector
from synthorg.settings.resolver import ConfigResolver

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.budget.tracker_protocol import CostTrackerProtocol
    from synthorg.meta.config import SelfImprovementConfig
    from synthorg.meta.signal_models import OrgSignalSnapshot
    from synthorg.meta.toolsmith.protocol import (
        GoldenScorecardProvider,
        ToolCreationOverflowHandler,
    )
    from synthorg.persistence.tool_blueprint_protocol import DynamicToolRepository

    SnapshotProvider = Callable[[], Awaitable[OrgSignalSnapshot]]


@dataclass(frozen=True)
class ToolsmithRuntime:
    """The wired toolsmith components the boot layer installs.

    Attributes:
        service: The orchestrating service (also the capability-gap sink).
        dynamic_registry: The live registry the layered tool surface reads.
        approval_consumer: The approve-to-live consumer, or ``None`` when no
            approval store is wired (nothing to consume approvals from).
    """

    service: ToolsmithService
    dynamic_registry: DynamicToolRegistry
    approval_consumer: ToolApprovalConsumer | None = None


def build_toolsmith(  # noqa: PLR0913 -- explicit DI of the toolsmith collaborators
    *,
    si_config: SelfImprovementConfig,
    connections: ConnectionSelector,
    repo: DynamicToolRepository,
    sandbox_resolver: SandboxResolver,
    scorecard_provider: GoldenScorecardProvider | None = None,
    approval_store: ApprovalStoreProtocol | None = None,
    overflow_handler: ToolCreationOverflowHandler | None = None,
    snapshot_provider: SnapshotProvider | None = None,
    existing_capabilities: tuple[NotBlankStr, ...] = (),
    cost_tracker: CostTrackerProtocol | None = None,
    clock: Clock | None = None,
    config_resolver: ConfigResolver | None = None,
    notification_dispatcher: NotificationDispatcher | None = None,
) -> ToolsmithRuntime:
    """Wire the toolsmith pipeline from config and runtime dependencies.

    Args:
        si_config: Self-improvement config (carries the embedded
            ``toolsmith`` config and drives the shared guard chain).
        connections: Resolves the connection a configured model names, for
            both blueprint authoring and the code-modification overflow arm.
        repo: Durable blueprint store.
        sandbox_resolver: Resolves the sandbox backend per blueprint (so a
            Docker-declared tool runs under Docker, a subprocess one under
            subprocess).
        scorecard_provider: Golden-scorecard provider for the gate;
            required when ``toolsmith.validation.require_golden_delta``.
        approval_store: Approval store routed into the approval gate.
        overflow_handler: Handler for service-access capability gaps;
            when ``None`` a code-modification overflow handler is built
            automatically if ``code_modification_enabled``.
        snapshot_provider: Optional live snapshot source for the
            code-modification overflow (defaults to a neutral baseline).
        existing_capabilities: Current capability surface (dedup hint).
        cost_tracker: Optional cost tracker for the authoring call.
        clock: Time source.
        config_resolver: Optional resolver threaded into the service for the
            live ``tool_creation_enabled`` gate and per-gap allowlist re-read,
            and into the overflow strategy for the live model read.
        notification_dispatcher: Optional operator-alert sink for recurring
            SERVICE_ABSENT gaps (a wired handler with no backing service) and
            for approve-to-live apply failures raised by the
            ``ToolApprovalConsumer`` (a burned operator approval); ``None``
            disables both ops signals.

    Returns:
        A :class:`ToolsmithRuntime` with the service and dynamic registry.
    """
    resolved_clock = clock or SystemClock()
    tsc = si_config.toolsmith

    gap_store = RingBufferCapabilityGapStore(
        max_observations=tsc.gap_buffer_size,
    )

    def _handler_factory(blueprint: ToolBlueprint) -> ToolHandler:
        """Return handler factory."""
        return make_dynamic_tool_handler(blueprint, sandbox_resolver(blueprint))

    dynamic_registry = DynamicToolRegistry(handler_factory=_handler_factory)

    generator = LLMToolBlueprintGenerator(
        config=tsc,
        connections=connections,
        cost_tracker=cost_tracker,
        clock=resolved_clock,
        config_resolver=config_resolver,
    )
    gate = BenchmarkToolValidationGate(
        config=tsc,
        brief_runner=SandboxBriefRunner(sandbox_resolver),
        scorecard_provider=scorecard_provider,
    )
    applier = ToolCreationApplier(
        repo=repo,
        registry=dynamic_registry,
        gate=gate,
        clock=resolved_clock,
    )
    guards = build_guards(
        si_config, approval_store=approval_store, config_resolver=config_resolver
    )
    resolved_overflow = overflow_handler or _build_overflow_handler(
        si_config=si_config,
        connections=connections,
        cost_tracker=cost_tracker,
        snapshot_provider=snapshot_provider,
        config_resolver=config_resolver,
    )
    service = ToolsmithService(
        config=tsc,
        gap_store=gap_store,
        generator=generator,
        applier=applier,
        guards=guards,
        overflow_handler=resolved_overflow,
        existing_capabilities=existing_capabilities,
        dynamic_registry=dynamic_registry,
        blueprint_repo=repo,
        clock=resolved_clock,
        config_resolver=config_resolver,
        notification_dispatcher=notification_dispatcher,
    )
    # The approve-to-live consumer needs a place to pull approvals from; without
    # an approval store there is nothing to consume, so it stays unwired.
    consumer = (
        ToolApprovalConsumer(
            service=service,
            blueprint_repo=repo,
            approval_store=approval_store,
            notification_dispatcher=notification_dispatcher,
        )
        if approval_store is not None
        else None
    )
    return ToolsmithRuntime(
        service=service,
        dynamic_registry=dynamic_registry,
        approval_consumer=consumer,
    )


def _build_overflow_handler(
    *,
    si_config: SelfImprovementConfig,
    connections: ConnectionSelector,
    cost_tracker: CostTrackerProtocol | None,
    snapshot_provider: SnapshotProvider | None,
    config_resolver: ConfigResolver | None = None,
) -> ToolCreationOverflowHandler | None:
    """Build the code-modification overflow handler when that altitude is on.

    Returns ``None`` when ``code_modification_enabled`` is unset, so
    service-access gaps simply log an unhandled-overflow notice.

    Returns:
        The ``ToolCreationOverflowHandler`` value when present, ``None`` otherwise.
    """
    if not si_config.code_modification_enabled:
        return None
    from synthorg.meta.strategies.code_modification import (  # noqa: PLC0415
        CodeModificationStrategy,
    )
    from synthorg.meta.toolsmith.overflow import (  # noqa: PLC0415
        CodeModificationOverflowHandler,
    )
    from synthorg.meta.validation.scope_validator import ScopeValidator  # noqa: PLC0415

    scope_validator = ScopeValidator(
        allowed_paths=tuple(si_config.code_modification.allowed_paths),
        forbidden_paths=tuple(si_config.code_modification.forbidden_paths),
    )
    strategy = CodeModificationStrategy(
        config=si_config,
        connections=connections,
        scope_validator=scope_validator,
        cost_tracker=cost_tracker,
        config_resolver=config_resolver,
    )
    return CodeModificationOverflowHandler(
        strategy, snapshot_provider=snapshot_provider
    )


__all__ = ["ToolsmithRuntime", "build_toolsmith"]
