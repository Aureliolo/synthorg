"""Factory mixin for :class:`AgentEngine`: approval gate, loop, tool invoker."""

from typing import TYPE_CHECKING

from synthorg.core.agent import AgentIdentity, ToolPermissions
from synthorg.core.task import Task
from synthorg.engine._security_factory import (
    make_security_interceptor,
    registry_with_approval_tool,
    registry_with_external_api_tool,
)
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.loop_protocol import ExecutionLoop
from synthorg.engine.loop_selector import (
    build_execution_loop,
    select_loop_type,
)
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_AUTO_SELECTED,
    EXECUTION_LOOP_BUDGET_UNAVAILABLE,
)
from synthorg.observability.events.trust import (
    TRUST_AGENT_AUTO_INITIALIZED,
    TRUST_TOOLS_NARROWED,
)
from synthorg.security.protocol import SecurityInterceptionStrategy
from synthorg.security.trust.enforcement import (
    resolve_effective_tool_permissions,
)
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.permissions import ToolPermissionChecker

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.budget.enforcer import BudgetEnforcer
    from synthorg.budget.tracker import CostTracker
    from synthorg.communication.event_stream.interrupt import InterruptStore
    from synthorg.communication.event_stream.stream import EventStreamHub
    from synthorg.config.schema import ProviderConfig
    from synthorg.core.effective_autonomy import EffectiveAutonomy
    from synthorg.engine.agent_engine import BrainToolFactoryProvider
    from synthorg.engine.compaction.protocol import CompactionCallback
    from synthorg.engine.hybrid_models import HybridLoopConfig
    from synthorg.engine.intervention.inbox import SteeringInbox
    from synthorg.engine.loop_selector import AutoLoopConfig
    from synthorg.engine.mcp_self_consumer import MCPSelfConsumerProvider
    from synthorg.engine.plan_models import PlanExecuteConfig
    from synthorg.engine.stagnation.protocol import StagnationDetector
    from synthorg.memory.injection import MemoryInjectionStrategy
    from synthorg.ontology.injection.protocol import OntologyInjectionStrategy
    from synthorg.persistence.parked_context_protocol import (
        ParkedContextRepository,
    )
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.providers.routing.resolver import ModelResolver
    from synthorg.security.audit import AuditLog
    from synthorg.security.config import SecurityConfig
    from synthorg.security.policy_engine.protocol import PolicyEngine
    from synthorg.security.trust.service import TrustService
    from synthorg.tools.external_api._runtime import ExternalApiRuntime
    from synthorg.tools.invocation_tracker import ToolInvocationTracker
    from synthorg.tools.registry import ToolRegistry

logger = get_logger(__name__)


class AgentEngineFactoriesMixin:
    """Mixin providing approval-gate, loop, and tool-invoker factories."""

    _approval_store: ApprovalStoreProtocol | None
    _external_api_runtime: ExternalApiRuntime | None
    _brain_tool_factory_provider: BrainToolFactoryProvider | None
    _parked_context_repo: ParkedContextRepository | None
    _event_stream_hub: EventStreamHub | None
    _interrupt_store: InterruptStore | None
    _injected_approval_gate: ApprovalGate | None
    _approval_gate: ApprovalGate | None
    _trust_service: TrustService | None
    _policy_engine: PolicyEngine | None
    _policy_evaluation_mode: str
    _mcp_self_consumer: MCPSelfConsumerProvider | None
    _approval_interrupt_timeout_seconds: float | None
    _stagnation_detector: StagnationDetector | None
    _compaction_callback: CompactionCallback | None
    _steering_inbox: SteeringInbox | None
    _auto_loop_config: AutoLoopConfig | None
    _loop: ExecutionLoop
    _hybrid_loop_config: HybridLoopConfig | None
    _plan_execute_config: PlanExecuteConfig | None
    _memory_injection_strategy: MemoryInjectionStrategy | None
    _ontology_injection_strategy: OntologyInjectionStrategy | None
    _model_resolver: ModelResolver | None
    _provider_configs: Mapping[str, ProviderConfig] | None
    _provider_registry: ProviderRegistry | None
    _tool_registry: ToolRegistry | None
    _tool_invocation_tracker: ToolInvocationTracker | None
    _security_config: SecurityConfig | None
    _budget_enforcer: BudgetEnforcer | None
    _audit_log: AuditLog
    _cost_tracker: CostTracker | None

    def _make_approval_gate(self) -> ApprovalGate | None:
        """Build an ApprovalGate if an approval store is configured.

        The interrupt timeout is sourced from
        ``EngineBridgeConfig.approval_interrupt_timeout_seconds`` via the
        engine's ``approval_interrupt_timeout_seconds`` constructor kwarg
        (projected onto ``self._approval_interrupt_timeout_seconds``).
        When the engine is built without that kwarg, the gate uses its
        own built-in default interrupt timeout.

        A boot-injected gate (``approval_gate=`` on the engine) wins
        unconditionally: the single-gate invariant (engine parks,
        ``/approvals`` resumes, one ``ParkedContextRepository``) must
        not be defeated by the engine's own ``approval_store is None``
        short-circuit, since the boot gate is wired independently of
        and before the engine's approval-store wiring.

        Returns:
            The injected gate when present; a freshly-built gate when
            an approval store is configured; ``None`` when neither.
        """
        if self._injected_approval_gate is not None:
            return self._injected_approval_gate

        if self._approval_store is None:
            return None

        from synthorg.engine.park_service import ParkService  # noqa: PLC0415

        # The gate's own default interrupt timeout applies when the engine
        # was built without an explicit override (omit, never pass None).
        timeout = self._approval_interrupt_timeout_seconds
        if timeout is not None:
            return ApprovalGate(
                park_service=ParkService(),
                parked_context_repo=self._parked_context_repo,
                event_hub=self._event_stream_hub,
                interrupt_store=self._interrupt_store,
                interrupt_timeout_seconds=timeout,
            )
        return ApprovalGate(
            park_service=ParkService(),
            parked_context_repo=self._parked_context_repo,
            event_hub=self._event_stream_hub,
            interrupt_store=self._interrupt_store,
        )

    def _make_default_loop(self) -> ExecutionLoop:
        """Build the default ``react`` loop via the shared factory.

        Returns:
            A freshly-built ReAct :class:`ExecutionLoop` wired with
            this engine's approval gate, stagnation detector, and
            compaction callback.
        """
        return build_execution_loop(
            "react",
            approval_gate=self._approval_gate,
            stagnation_detector=self._stagnation_detector,
            compaction_callback=self._compaction_callback,
            steering_inbox=self._steering_inbox,
        )

    async def _resolve_loop(
        self,
        task: Task,
        agent_id: str = "",
        task_id: str = "",
    ) -> ExecutionLoop:
        """Select the execution loop for a task.

        Returns:
            The configured default loop when auto-selection is off;
            otherwise an :class:`ExecutionLoop` of the type selected
            from task complexity and (when relevant) live budget
            utilisation.
        """
        if self._auto_loop_config is None:
            return self._loop

        cfg = self._auto_loop_config
        preliminary = select_loop_type(
            complexity=task.estimated_complexity,
            rules=cfg.rules,
            budget_utilization_pct=None,
            budget_tight_threshold=cfg.budget_tight_threshold,
            hybrid_fallback=None,
            default_loop_type=cfg.default_loop_type,
        )

        budget_utilization_pct: float | None = None
        if preliminary == "hybrid" and self._budget_enforcer is not None:
            budget_utilization_pct = (
                await self._budget_enforcer.get_budget_utilization_pct()
            )
            if budget_utilization_pct is None:
                logger.debug(
                    EXECUTION_LOOP_BUDGET_UNAVAILABLE,
                    note="budget utilization unknown; skipping budget-aware downgrade",
                )

        loop_type = select_loop_type(
            complexity=task.estimated_complexity,
            rules=cfg.rules,
            budget_utilization_pct=budget_utilization_pct,
            budget_tight_threshold=cfg.budget_tight_threshold,
            hybrid_fallback=cfg.hybrid_fallback,
            default_loop_type=cfg.default_loop_type,
        )

        logger.info(
            EXECUTION_LOOP_AUTO_SELECTED,
            agent_id=agent_id,
            task_id=task_id,
            complexity=task.estimated_complexity.value,
            selected_loop=loop_type,
            budget_utilization_pct=budget_utilization_pct,
        )

        return build_execution_loop(
            loop_type,
            approval_gate=self._approval_gate,
            stagnation_detector=self._stagnation_detector,
            compaction_callback=self._compaction_callback,
            plan_execute_config=self._plan_execute_config,
            hybrid_loop_config=self._hybrid_loop_config,
            steering_inbox=self._steering_inbox,
        )

    def _make_security_interceptor(
        self,
        effective_autonomy: EffectiveAutonomy | None = None,
    ) -> SecurityInterceptionStrategy | None:
        """Build the SecOps security interceptor if configured.

        Returns:
            A :class:`SecurityInterceptionStrategy` when the security
            wiring is present; ``None`` when the feature is not
            configured.
        """
        return make_security_interceptor(
            self._security_config,
            self._audit_log,
            approval_store=self._approval_store,
            effective_autonomy=effective_autonomy,
            provider_registry=self._provider_registry,
            provider_configs=self._provider_configs,
            model_resolver=self._model_resolver,
            cost_tracker=self._cost_tracker,
        )

    def _trust_narrowed_tools(self, identity: AgentIdentity) -> ToolPermissions:
        """Return the agent's tool permissions narrowed by earned trust.

        No-op when no ``TrustService`` is wired (trust strategy
        ``DISABLED``). Otherwise the agent's trust state is read
        (auto-initialised at the configured initial level on first
        sight so trust enforces from the first run rather than only
        after an out-of-band seed), and the effective permissions are
        the more restrictive of the identity level and the earned
        trust level. A trust-strategy switch therefore changes which
        tools the permission checker admits for the same agent.
        """
        if self._trust_service is None:
            return identity.tools
        agent_key = str(identity.id)
        had_state = self._trust_service.get_trust_state(agent_key) is not None
        # Atomic get-or-create: a concurrent first run for the same
        # agent cannot double-initialise it (TOCTOU on the previous
        # get-then-initialize pair).
        state = self._trust_service.get_or_initialize_agent(agent_key)
        if not had_state:
            logger.info(
                TRUST_AGENT_AUTO_INITIALIZED,
                agent_id=agent_key,
                trust_level=state.global_level.value,
            )
        effective, narrowed = resolve_effective_tool_permissions(
            identity.tools,
            state.global_level,
        )
        if narrowed:
            logger.info(
                TRUST_TOOLS_NARROWED,
                agent_id=agent_key,
                identity_level=identity.tools.access_level.value,
                trust_level=state.global_level.value,
            )
        return effective

    def _make_tool_invoker(
        self,
        identity: AgentIdentity,
        task_id: str | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
        project_id: str | None = None,
    ) -> ToolInvoker | None:
        """Create a ToolInvoker with permission checking and security.

        Returns:
            A :class:`ToolInvoker` wired with the registry (extended
            with approval / external-API / project-brain / memory tools
            when their dependencies are wired); ``None`` when no tool
            registry is configured.
        """
        if self._tool_registry is None:
            return None

        registry = registry_with_approval_tool(
            self._tool_registry,
            self._approval_store,
            identity,
            task_id=task_id,
        )
        registry = registry_with_external_api_tool(
            registry,
            self._external_api_runtime,
            self._approval_store,
            identity,
            task_id=task_id,
            effective_autonomy=effective_autonomy,
        )
        # The brain tool factory is wired late (memory-gated
        # ``_wire_project_brain`` runs after the boot engine is built), so it
        # is resolved through a provider at per-task time rather than captured
        # at construction; ``None`` until the brain is wired (or forever when
        # the brain is disabled), in which case no brain tools are added.
        brain_tool_factory = (
            self._brain_tool_factory_provider()
            if self._brain_tool_factory_provider is not None
            else None
        )
        if project_id is not None:
            from synthorg.project_brain.tool_registry import (  # noqa: PLC0415
                registry_with_brain_tools,
            )

            registry = registry_with_brain_tools(
                registry,
                brain_tool_factory,
                project_id=project_id,
                author_agent_id=str(identity.id),
            )
        if self._memory_injection_strategy is not None:
            from synthorg.memory.tools import (  # noqa: PLC0415
                registry_with_memory_tools,
            )

            registry = registry_with_memory_tools(
                registry,
                self._memory_injection_strategy,
                agent_id=str(identity.id),
            )
        if self._ontology_injection_strategy is not None:
            tool_defs = self._ontology_injection_strategy.get_tool_definitions()
            if tool_defs:
                from synthorg.ontology.injection.hybrid import (  # noqa: PLC0415
                    HybridInjectionStrategy,
                )
                from synthorg.ontology.injection.tool import (  # noqa: PLC0415
                    ToolBasedInjectionStrategy,
                )
                from synthorg.tools.registry import (  # noqa: PLC0415
                    ToolRegistry as _ToolRegistry,
                )

                if isinstance(
                    self._ontology_injection_strategy,
                    ToolBasedInjectionStrategy | HybridInjectionStrategy,
                ):
                    import copy as _copy  # noqa: PLC0415

                    ontology_tool = _copy.deepcopy(
                        self._ontology_injection_strategy.tool,
                    )
                    existing = [_copy.deepcopy(t) for t in registry.all_tools()]
                    registry = _ToolRegistry([*existing, ontology_tool])
        from synthorg.tools.discovery import (  # noqa: PLC0415
            DeferredDisclosureManager,
            build_discovery_tools,
        )
        from synthorg.tools.registry import (  # noqa: PLC0415
            ToolRegistry as _ToolRegistry2,
        )

        deferred = DeferredDisclosureManager()
        discovery = build_discovery_tools(deferred)
        existing = list(registry.all_tools())
        registry = _ToolRegistry2([*existing, *discovery])

        narrowed = self._trust_narrowed_tools(identity)
        if self._mcp_self_consumer is not None:
            mcp_tools = self._mcp_self_consumer(
                identity,
                narrowed.access_level,
            )
            if mcp_tools:
                registry = _ToolRegistry2(
                    [*registry.all_tools(), *mcp_tools],
                )

        checker = ToolPermissionChecker.from_permissions(narrowed)
        interceptor = self._make_security_interceptor(effective_autonomy)
        invoker = ToolInvoker(
            registry,
            permission_checker=checker,
            security_interceptor=interceptor,
            agent_id=str(identity.id),
            task_id=task_id,
            invocation_tracker=self._tool_invocation_tracker,
            policy_engine=self._policy_engine,
            policy_evaluation_mode=self._policy_evaluation_mode,
        )
        deferred.bind(invoker)
        return invoker
