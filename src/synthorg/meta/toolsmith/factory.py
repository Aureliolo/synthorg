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
    from collections.abc import Callable

    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.budget.tracker import CostTracker
    from synthorg.core.types import NotBlankStr
    from synthorg.meta.config import SelfImprovementConfig
    from synthorg.meta.toolsmith.models import ToolBlueprint
    from synthorg.meta.toolsmith.protocol import (
        GoldenScorecardProvider,
        ToolCreationOverflowHandler,
    )
    from synthorg.persistence.tool_blueprint_protocol import DynamicToolRepository
    from synthorg.providers.base import BaseCompletionProvider
    from synthorg.tools.sandbox.protocol import SandboxBackend

    SandboxResolver = Callable[[ToolBlueprint], SandboxBackend]


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
        overflow_handler: Handler for service-access capability gaps.
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

    def _handler_factory(blueprint: ToolBlueprint) -> object:
        return make_dynamic_tool_handler(blueprint, sandbox_resolver(blueprint))

    dynamic_registry = DynamicToolRegistry(handler_factory=_handler_factory)  # type: ignore[arg-type]

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
    service = ToolsmithService(
        config=tsc,
        gap_store=gap_store,
        generator=generator,
        applier=applier,
        guards=guards,
        overflow_handler=overflow_handler,
        existing_capabilities=existing_capabilities,
        clock=resolved_clock,
    )
    return ToolsmithRuntime(service=service, dynamic_registry=dynamic_registry)


__all__ = ["ToolsmithRuntime", "build_toolsmith"]
