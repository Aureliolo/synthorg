"""The one place a test says what an ``AgentEngine`` is NOT wired with.

``EngineDependencies`` has no defaults, deliberately: a production caller
that omits a collaborator is the defect this whole package exists to make
impossible. A test is the one legitimate exception, because a unit test
about (say) budget refusal is not making a claim about the review pipeline
and should not have to restate sixty absences to say so.

So the absences are spelled ONCE, here, and a test overrides only the
bundle it is actually about. That keeps the escape hatch in a single file
a reviewer can read, rather than scattered across two hundred call sites,
and it is why ``check_engine_dependencies_total.py`` names this module by
path: nothing under ``src/`` or ``evals/`` may build one of these.
"""

from typing import TypedDict, Unpack

from synthorg.core.clock import Clock, SystemClock
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.agent_state_recording import no_agent_state
from synthorg.engine.artifacts.baseline_scope import RunBaselineProbe
from synthorg.engine.dependencies import (
    EngineBehaviour,
    EngineBudget,
    EngineCore,
    EngineDependencies,
    EngineGovernance,
    EngineLoopControls,
    EngineMemory,
    EngineObservability,
    EngineOrg,
    EngineRecovery,
    EngineRouting,
    EngineTooling,
)
from synthorg.engine.recovery import FailAndReassignStrategy
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.registry import ProviderRegistry
from synthorg.security.audit import AuditLog
from synthorg.tools.connection_tool_runtimes import ConnectionToolRuntimes
from synthorg.tools.registry import ToolRegistry
from synthorg.workers.engine_assembly import EngineAssemblyInputs


def unwired_core(
    provider: CompletionProvider, *, clock: Clock | None = None
) -> EngineCore:
    """The minimum an engine needs: a provider and a clock.

    Args:
        provider: The completion driver under test.
        clock: Time source; ``None`` takes the real one.

    Returns:
        A core bundle with nothing else wired.
    """
    return EngineCore(
        provider=provider,
        clock=clock if clock is not None else SystemClock(),
        config_resolver=None,
        tool_registry=None,
        execution_loop=None,
        shutdown_checker=None,
    )


UNWIRED_ROUTING = EngineRouting(
    provider_registry=None,
    provider_configs=None,
    model_resolver=None,
)

UNWIRED_BUDGET = EngineBudget(
    cost_tracker=None,
    budget_enforcer=None,
    cost_forecast_repo=None,
    coordination_metrics_collector=None,
)

UNWIRED_LOOP_CONTROLS = EngineLoopControls(
    stagnation_detector=None,
    compaction_callback=None,
    step_classifier=None,
    steering_inbox=None,
    background_job_watcher=None,
)

UNWIRED_MEMORY = EngineMemory(
    memory_backend=None,
    memory_injection_strategy_provider=None,
    ontology_injection_strategy=None,
    procedural_memory_config=None,
    capture_strategy=None,
    distillation_capture_enabled=False,
)

UNWIRED_ORG = EngineOrg(
    agent_registry=None,
    capability=None,
    task_engine=None,
    project_repo=None,
    coordinator=None,
    evolution_service=None,
    mcp_self_consumer=None,
)

UNWIRED_OBSERVABILITY = EngineObservability(
    event_stream_hub=None,
    event_reader=None,
    interrupt_store=None,
    flight_recorder_sink=None,
    agent_state_repository_provider=no_agent_state,
    classification_sinks=(),
    error_taxonomy_config=None,
    agent_middleware_chain=None,
)

DEFAULT_BEHAVIOUR = EngineBehaviour(clarification_enabled=True, scoping_enabled=True)


def unwired_governance() -> EngineGovernance:
    """Governance with nothing wired but a fresh audit log.

    A function rather than a constant because ``AuditLog`` accumulates:
    two tests sharing one would read each other's entries.

    Returns:
        The bundle.
    """
    return EngineGovernance(
        policy_engine=None,
        security_config=None,
        security_config_provider=None,
        audit_log=AuditLog(),
        approval_store=None,
        approval_gate=None,
        parked_context_repo=None,
        approval_interrupt_timeout_seconds=None,
        review_gate=None,
        review_pipeline=None,
    )


def unwired_tooling() -> EngineTooling:
    """Tooling with nothing wired but empty connection runtimes.

    Returns:
        The bundle.
    """
    return EngineTooling(
        external_api_runtime=None,
        connection_tool_runtimes=ConnectionToolRuntimes(),
        tool_invocation_tracker=None,
        brain_tool_factory_provider=None,
        knowledge_tool_factory_provider=None,
        docs_tool_factory_provider=None,
        research_tool_factory_provider=None,
        structure_map_tool_factory_provider=None,
    )


def unwired_recovery() -> EngineRecovery:
    """Fail-and-reassign, no probe, no checkpointing.

    A function because the strategy is stateful: it is the one collaborator
    a shared module-level instance used to hide, and the reason
    ``AgentEngine`` no longer carries a module-level default for it.

    Returns:
        The bundle.
    """
    return EngineRecovery(
        recovery_strategy=FailAndReassignStrategy(),
        run_probe=None,
        checkpointing=None,
    )


class BundleOverrides(TypedDict, total=False):
    """The bundles a test may name, each replacing its unwired default.

    A ``TypedDict`` consumed through ``**kwargs: Unpack[...]`` rather than
    one keyword parameter per bundle: eleven parameters would put this
    helper over the argument cap, and a bare ``**kwargs: object`` would buy
    that back by giving up the type check on every one of two hundred call
    sites, which is where handing ``budget=`` a governance bundle would
    otherwise go unnoticed.
    """

    core: EngineCore
    routing: EngineRouting
    budget: EngineBudget
    governance: EngineGovernance
    loop_controls: EngineLoopControls
    memory: EngineMemory
    org: EngineOrg
    tooling: EngineTooling
    observability: EngineObservability
    recovery: EngineRecovery
    behaviour: EngineBehaviour


def engine_deps(
    provider: CompletionProvider, **groups: Unpack[BundleOverrides]
) -> EngineDependencies:
    """Build a dependency declaration wired with only what a test names.

    Args:
        provider: The completion driver, used to build a default core.
        **groups: Per-bundle overrides; anything unnamed is unwired.

    Returns:
        The complete declaration.
    """
    return EngineDependencies(
        core=groups["core"] if "core" in groups else unwired_core(provider),
        routing=groups.get("routing", UNWIRED_ROUTING),
        budget=groups.get("budget", UNWIRED_BUDGET),
        governance=(
            groups["governance"] if "governance" in groups else unwired_governance()
        ),
        loop_controls=groups.get("loop_controls", UNWIRED_LOOP_CONTROLS),
        memory=groups.get("memory", UNWIRED_MEMORY),
        org=groups.get("org", UNWIRED_ORG),
        tooling=groups["tooling"] if "tooling" in groups else unwired_tooling(),
        observability=groups.get("observability", UNWIRED_OBSERVABILITY),
        recovery=groups["recovery"] if "recovery" in groups else unwired_recovery(),
        behaviour=groups.get("behaviour", DEFAULT_BEHAVIOUR),
    )


def engine_with(
    provider: CompletionProvider, **groups: Unpack[BundleOverrides]
) -> AgentEngine:
    """Build an engine wired with only what a test names.

    Args:
        provider: The completion driver.
        **groups: Per-bundle overrides; anything unnamed is unwired.

    Returns:
        The engine.
    """
    return AgentEngine(engine_deps(provider, **groups))


def assembly_inputs(
    provider: CompletionProvider,
    *,
    provider_registry: ProviderRegistry | None = None,
    tool_registry: ToolRegistry | None = None,
    run_probe: RunBaselineProbe | None = None,
) -> EngineAssemblyInputs:
    """What ``build_agent_engine`` is handed in a test.

    The same escape hatch as :func:`engine_deps`, for the same reason: a
    test about one boot collaborator should not have to restate the nine
    inputs it is not making a claim about.

    Args:
        provider: The completion driver.
        provider_registry: Where a bound pair resolves; empty by default.
        tool_registry: The base tool set; empty by default.
        run_probe: The delivery baseline probe; absent by default.

    Returns:
        The inputs.
    """
    return EngineAssemblyInputs(
        provider=provider,
        provider_registry=(
            provider_registry
            if provider_registry is not None
            else ProviderRegistry(drivers={})
        ),
        tool_registry=tool_registry if tool_registry is not None else ToolRegistry([]),
        run_probe=run_probe,
        coordination_metrics_collector=None,
        external_api_runtime=None,
        connection_tool_runtimes=ConnectionToolRuntimes(),
        flight_recorder_sink=None,
        step_classifier=None,
        classification_detector_timeout_seconds=None,
    )


__all__ = [
    "DEFAULT_BEHAVIOUR",
    "UNWIRED_BUDGET",
    "UNWIRED_LOOP_CONTROLS",
    "UNWIRED_MEMORY",
    "UNWIRED_OBSERVABILITY",
    "UNWIRED_ORG",
    "UNWIRED_ROUTING",
    "assembly_inputs",
    "engine_deps",
    "engine_with",
    "unwired_core",
    "unwired_governance",
    "unwired_recovery",
    "unwired_tooling",
]
