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
from synthorg.meta.factory import build_guards
from synthorg.meta.toolsmith.applier import ToolCreationApplier
from synthorg.meta.toolsmith.dynamic_registry import DynamicToolRegistry
from synthorg.meta.toolsmith.gap_store import RingBufferCapabilityGapStore
from synthorg.meta.toolsmith.script_handler import make_dynamic_tool_handler
from synthorg.meta.toolsmith.service import ToolsmithService
from synthorg.meta.toolsmith.strategy import LLMToolBlueprintGenerator
from synthorg.meta.toolsmith.validation_gate import (
    BenchmarkToolValidationGate,
    SandboxBriefRunner,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.budget.tracker import CostTracker
    from synthorg.core.types import NotBlankStr
    from synthorg.meta.config import SelfImprovementConfig
    from synthorg.meta.mcp.handler_protocol import ToolHandler
    from synthorg.meta.signal_models import OrgSignalSnapshot
    from synthorg.meta.toolsmith.models import ToolBlueprint
    from synthorg.meta.toolsmith.protocol import (
        GoldenScorecardProvider,
        ToolCreationOverflowHandler,
    )
    from synthorg.persistence.tool_blueprint_protocol import DynamicToolRepository
    from synthorg.providers.base import BaseCompletionProvider
    from synthorg.tools.sandbox.protocol import SandboxBackend

    SandboxResolver = Callable[[ToolBlueprint], SandboxBackend]
    SnapshotProvider = Callable[[], Awaitable[OrgSignalSnapshot]]


@dataclass(frozen=True)
class ToolsmithRuntime:
    """The wired toolsmith components the boot layer installs.

    Attributes:
        service: The orchestrating service (also the capability-gap sink).
        dynamic_registry: The live registry the layered tool surface reads.
    """

    service: ToolsmithService
    dynamic_registry: DynamicToolRegistry


def build_toolsmith(  # noqa: PLR0913 -- explicit DI of the toolsmith collaborators
    *,
    si_config: SelfImprovementConfig,
    provider: BaseCompletionProvider,
    repo: DynamicToolRepository,
    sandbox_resolver: SandboxResolver,
    scorecard_provider: GoldenScorecardProvider | None = None,
    approval_store: ApprovalStoreProtocol | None = None,
    overflow_handler: ToolCreationOverflowHandler | None = None,
    snapshot_provider: SnapshotProvider | None = None,
    existing_capabilities: tuple[NotBlankStr, ...] = (),
    cost_tracker: CostTracker | None = None,
    clock: Clock | None = None,
) -> ToolsmithRuntime:
    """Wire the toolsmith pipeline from config and runtime dependencies.

    Args:
        si_config: Self-improvement config (carries the embedded
            ``toolsmith`` config and drives the shared guard chain).
        provider: Completion provider for blueprint authoring.
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

    Returns:
        A :class:`ToolsmithRuntime` with the service and dynamic registry.
    """
    resolved_clock = clock or SystemClock()
    tsc = si_config.toolsmith

    gap_store = RingBufferCapabilityGapStore(
        max_observations=tsc.gap_buffer_size,
    )

    def _handler_factory(blueprint: ToolBlueprint) -> ToolHandler:
        return make_dynamic_tool_handler(blueprint, sandbox_resolver(blueprint))

    dynamic_registry = DynamicToolRegistry(handler_factory=_handler_factory)

    generator = LLMToolBlueprintGenerator(
        config=tsc,
        provider=provider,
        cost_tracker=cost_tracker,
        clock=resolved_clock,
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
    guards = build_guards(si_config, approval_store=approval_store)
    resolved_overflow = overflow_handler or _build_overflow_handler(
        si_config=si_config,
        provider=provider,
        cost_tracker=cost_tracker,
        snapshot_provider=snapshot_provider,
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
        clock=resolved_clock,
    )
    return ToolsmithRuntime(service=service, dynamic_registry=dynamic_registry)


def _build_overflow_handler(
    *,
    si_config: SelfImprovementConfig,
    provider: BaseCompletionProvider,
    cost_tracker: CostTracker | None,
    snapshot_provider: SnapshotProvider | None,
) -> ToolCreationOverflowHandler | None:
    """Build the code-modification overflow handler when that altitude is on.

    Returns ``None`` when ``code_modification_enabled`` is unset, so
    service-access gaps simply log an unhandled-overflow notice.
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
        provider=provider,
        scope_validator=scope_validator,
        cost_tracker=cost_tracker,
    )
    return CodeModificationOverflowHandler(
        strategy, snapshot_provider=snapshot_provider
    )


__all__ = ["ToolsmithRuntime", "build_toolsmith"]
