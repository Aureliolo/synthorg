"""Factory mixin for :class:`AgentEngine`: approval gate, loop, tool invoker."""

from typing import TYPE_CHECKING, Literal

from synthorg.core.agent import AgentIdentity
from synthorg.core.clock import Clock
from synthorg.core.task import Task
from synthorg.engine._agent_loop_selection import resolve_loop
from synthorg.engine._agent_tool_registry import (
    registry_with_chat_tools,
    registry_with_delegate_tool,
    registry_with_forge_tools,
)
from synthorg.engine._security_factory import (
    SecurityLlmInfra,
    make_security_interceptor,
    registry_with_approval_tool,
    registry_with_external_api_tool,
    registry_with_human_input_tools,
)
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.loop_protocol import ExecutionLoop
from synthorg.engine.loop_selector import (
    build_execution_loop,
)
from synthorg.observability import get_logger
from synthorg.observability.events.tool import TOOL_REGISTRY_BUILT
from synthorg.security.protocol import SecurityInterceptionStrategy
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.permissions import ToolPermissionChecker

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.budget.enforcer import BudgetEnforcer
    from synthorg.budget.tracker_protocol import CostTrackerProtocol
    from synthorg.communication.event_stream.interrupt import InterruptStore
    from synthorg.communication.event_stream.stream import EventStreamHub
    from synthorg.config.schema import ProviderConfig
    from synthorg.core.effective_autonomy import EffectiveAutonomy
    from synthorg.engine._agent_engine_types import (
        BrainToolFactoryProvider,
        DocsToolFactoryProvider,
        KnowledgeToolFactoryProvider,
        ResearchToolFactoryProvider,
        StructureMapToolFactoryProvider,
    )
    from synthorg.engine.compaction.protocol import CompactionCallback
    from synthorg.engine.delegation.protocol import SubAgentRunner
    from synthorg.engine.intervention.inbox import SteeringInbox
    from synthorg.engine.loop_selector import AutoLoopConfig
    from synthorg.engine.mcp_self_consumer import MCPSelfConsumerProvider
    from synthorg.engine.openhands.config import (
        OpenHandsLoopConfig,
        OpenHandsLoopDeps,
    )
    from synthorg.engine.quality.classifier import StepQualityClassifier
    from synthorg.engine.stagnation.protocol import StagnationDetector
    from synthorg.memory.injection import (
        MemoryInjectionStrategy,
        MemoryInjectionStrategyProvider,
    )
    from synthorg.ontology.injection.protocol import OntologyInjectionStrategy
    from synthorg.persistence.parked_context_protocol import (
        ParkedContextRepository,
    )
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.providers.routing.resolver import ModelResolver
    from synthorg.security.audit import AuditLog
    from synthorg.security.config import SecurityConfig
    from synthorg.security.policy_engine.protocol import PolicyEngine
    from synthorg.settings.resolver import ConfigResolver
    from synthorg.tools.chat._runtime import ChatToolsRuntime
    from synthorg.tools.external_api._runtime import ExternalApiRuntime
    from synthorg.tools.forge._runtime import ForgeToolsRuntime
    from synthorg.tools.invocation_tracker import ToolInvocationTracker
    from synthorg.tools.registry import ToolRegistry

logger = get_logger(__name__)


class AgentEngineFactoriesMixin:
    """Mixin providing approval-gate, loop, and tool-invoker factories."""

    _approval_store: ApprovalStoreProtocol | None
    _clarification_enabled: bool
    _scoping_enabled: bool
    _clock: Clock
    _external_api_runtime: ExternalApiRuntime | None
    _forge_tools_runtime: ForgeToolsRuntime | None
    _chat_tools_runtime: ChatToolsRuntime | None
    _brain_tool_factory_provider: BrainToolFactoryProvider | None
    _knowledge_tool_factory_provider: KnowledgeToolFactoryProvider | None
    _docs_tool_factory_provider: DocsToolFactoryProvider | None
    _research_tool_factory_provider: ResearchToolFactoryProvider | None
    _structure_map_tool_factory_provider: StructureMapToolFactoryProvider | None
    _parked_context_repo: ParkedContextRepository | None
    _event_stream_hub: EventStreamHub | None
    _interrupt_store: InterruptStore | None
    _injected_approval_gate: ApprovalGate | None
    _approval_gate: ApprovalGate | None
    _policy_engine: PolicyEngine | None
    _policy_evaluation_mode: Literal["enforce", "log_only"]
    _mcp_self_consumer: MCPSelfConsumerProvider | None
    _approval_interrupt_timeout_seconds: float | None
    _stagnation_detector: StagnationDetector | None
    _step_classifier: StepQualityClassifier | None
    _compaction_callback: CompactionCallback | None
    _steering_inbox: SteeringInbox | None
    _auto_loop_config: AutoLoopConfig | None
    _loop: ExecutionLoop
    _openhands_loop_config: OpenHandsLoopConfig | None
    _openhands_loop_deps: OpenHandsLoopDeps | None
    _memory_injection_strategy_provider: MemoryInjectionStrategyProvider | None
    _ontology_injection_strategy: OntologyInjectionStrategy | None
    _model_resolver: ModelResolver | None
    _provider_configs: Mapping[str, ProviderConfig] | None
    _provider_registry: ProviderRegistry | None
    _tool_registry: ToolRegistry | None
    _tool_invocation_tracker: ToolInvocationTracker | None
    _security_config: SecurityConfig | None
    _security_config_provider: Callable[[], SecurityConfig | None] | None
    _budget_enforcer: BudgetEnforcer | None
    _audit_log: AuditLog
    _cost_tracker: CostTrackerProtocol | None
    _config_resolver: ConfigResolver | None
    _sub_agent_runner: SubAgentRunner | None

    def _live_security_config(self) -> SecurityConfig | None:
        """Return the live security config (provider when wired, else static).

        The boot path wires ``_security_config_provider`` to read the
        ``AppState`` security holder, so operator toggles to the four
        ``security.*`` flags apply per request without a restart. Tests /
        direct construction fall back to the static boot ``_security_config``.
        """
        if self._security_config_provider is not None:
            return self._security_config_provider()
        return self._security_config

    @property
    def has_security_governance(self) -> bool:
        """Whether an enabled ``SecurityConfig`` governs sensitive actions.

        Reads the live config (through the holder when wired) so a runtime
        toggle of ``security.enabled`` gates the direct-MCP acting surface
        without a restart.
        """
        live = self._live_security_config()
        return live is not None and live.enabled

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
                clock=self._clock,
            )
        return ApprovalGate(
            park_service=ParkService(),
            parked_context_repo=self._parked_context_repo,
            event_hub=self._event_stream_hub,
            interrupt_store=self._interrupt_store,
            clock=self._clock,
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
            step_classifier=self._step_classifier,
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
            from task complexity.
        """
        return await resolve_loop(
            task,
            agent_id=agent_id,
            task_id=task_id,
            static_loop=self._loop,
            auto_loop_config=self._auto_loop_config,
            build=self._build_loop,
        )

    def _build_loop(self, loop_type: str) -> ExecutionLoop:
        """Build a loop of ``loop_type`` from the engine's dependencies.

        Returns:
            The constructed :class:`ExecutionLoop`.
        """
        return build_execution_loop(
            loop_type,
            approval_gate=self._approval_gate,
            stagnation_detector=self._stagnation_detector,
            compaction_callback=self._compaction_callback,
            openhands_loop_config=self._openhands_loop_config,
            openhands_loop_deps=self._openhands_loop_deps,
            steering_inbox=self._steering_inbox,
            step_classifier=self._step_classifier,
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
            self._live_security_config(),
            self._audit_log,
            approval_store=self._approval_store,
            effective_autonomy=effective_autonomy,
            llm_infra=self._security_llm_infra(),
        )

    def _security_llm_infra(self) -> SecurityLlmInfra | None:
        """Bundle the provider infrastructure the security LLM features need.

        Both halves are required. A registry with no resolver builds the
        features and then cannot read a single ``MODEL_REF``, so every
        dispatch resolves to nothing and the classifier proceeds without the
        auto-rejection it was switched on for, with no disabled-feature
        warning anywhere. Returning ``None`` routes that through the same
        path as a missing registry, which does warn.

        Returns:
            The bundle, or ``None`` when either the provider registry or the
            settings resolver is absent, leaving every LLM-backed security
            feature unwired and saying so.
        """
        if self._provider_registry is None or self._config_resolver is None:
            return None
        return SecurityLlmInfra(
            provider_registry=self._provider_registry,
            provider_configs=self._provider_configs or {},
            config_resolver=self._config_resolver,
            model_resolver=self._model_resolver,
            cost_tracker=self._cost_tracker,
        )

    def _make_tool_invoker(
        self,
        identity: AgentIdentity,
        task_id: str | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
        project_id: str | None = None,
        *,
        memory_strategy: MemoryInjectionStrategy | None,
    ) -> ToolInvoker | None:
        """Create a ToolInvoker with permission checking and security.

        Args:
            identity: The agent the invoker is built for.
            task_id: Task the invoker serves, when there is one.
            effective_autonomy: Resolved autonomy for this unit of work.
            project_id: Project the work belongs to, when there is one.
            memory_strategy: The strategy this unit of work resolved, passed
                in rather than read here so the memory tools installed on the
                registry and the memories injected into the context come from
                the same one. Required, not defaulted: every caller starts a
                unit of work and so has its own moment to resolve at.

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
        registry = registry_with_human_input_tools(
            registry,
            self._approval_store,
            identity,
            task_id=task_id,
            clarification_enabled=self._clarification_enabled,
            scoping_enabled=self._scoping_enabled,
        )
        registry = registry_with_external_api_tool(
            registry,
            self._external_api_runtime,
            approval_store=self._approval_store,
            identity=identity,
            task_id=task_id,
            effective_autonomy=effective_autonomy,
        )
        registry = registry_with_forge_tools(
            registry,
            self._forge_tools_runtime,
            approval_store=self._approval_store,
            identity=identity,
            task_id=task_id,
            effective_autonomy=effective_autonomy,
        )
        registry = registry_with_chat_tools(
            registry,
            self._chat_tools_runtime,
            approval_store=self._approval_store,
            identity=identity,
            task_id=task_id,
            effective_autonomy=effective_autonomy,
        )
        registry = registry_with_delegate_tool(
            registry,
            self._sub_agent_runner,
            config_resolver=self._config_resolver,
            identity=identity,
            task_id=task_id,
            project_id=project_id,
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
        # The knowledge tool factory is wired late (memory-gated
        # ``_wire_knowledge_engine`` runs after the boot engine is built),
        # so it is resolved through a provider at per-task time; ``None``
        # until the substrate is wired (or forever when disabled), in which
        # case no knowledge tools are added.
        knowledge_tool_factory = (
            self._knowledge_tool_factory_provider()
            if self._knowledge_tool_factory_provider is not None
            else None
        )
        if knowledge_tool_factory is not None:
            from synthorg.core.types import NotBlankStr  # noqa: PLC0415
            from synthorg.tools.registry import (  # noqa: PLC0415
                ToolRegistry as _KnowledgeToolRegistry,
            )

            knowledge_tools = knowledge_tool_factory.build_tools(
                project_id=NotBlankStr(project_id) if project_id is not None else None,
            )
            if knowledge_tools:
                registry = _KnowledgeToolRegistry(
                    [*registry.all_tools(), *knowledge_tools],
                )
        # Living-docs tools bind both project_id AND author_agent_id from the
        # task context, so they are added only when a project scope exists.
        docs_tool_factory = (
            self._docs_tool_factory_provider()
            if self._docs_tool_factory_provider is not None
            else None
        )
        if docs_tool_factory is not None and project_id is not None:
            from synthorg.core.types import NotBlankStr  # noqa: PLC0415
            from synthorg.tools.registry import (  # noqa: PLC0415
                ToolRegistry as _DocsToolRegistry,
            )

            docs_tools = docs_tool_factory.build_tools(
                project_id=NotBlankStr(project_id),
                author_agent_id=NotBlankStr(str(identity.id)),
            )
            if docs_tools:
                registry = _DocsToolRegistry([*registry.all_tools(), *docs_tools])
        # The research tool binds an optional project scope + the acting agent.
        research_tool_factory = (
            self._research_tool_factory_provider()
            if self._research_tool_factory_provider is not None
            else None
        )
        if research_tool_factory is not None:
            from synthorg.core.types import NotBlankStr  # noqa: PLC0415
            from synthorg.tools.registry import (  # noqa: PLC0415
                ToolRegistry as _ResearchToolRegistry,
            )

            research_tools = research_tool_factory.build_tools(
                project_id=NotBlankStr(project_id) if project_id is not None else None,
                created_by=NotBlankStr(str(identity.id)),
            )
            if research_tools:
                registry = _ResearchToolRegistry(
                    [*registry.all_tools(), *research_tools],
                )
        # The structure-map query tool binds the task's project scope, so
        # it is added only when a project scope exists. The factory is
        # parked on the engine slice by brownfield intake (``None`` until a
        # codebase is imported), so the tool is absent otherwise.
        structure_map_tool_factory = (
            self._structure_map_tool_factory_provider()
            if self._structure_map_tool_factory_provider is not None
            else None
        )
        if structure_map_tool_factory is not None and project_id is not None:
            from synthorg.core.types import NotBlankStr  # noqa: PLC0415
            from synthorg.tools.registry import (  # noqa: PLC0415
                ToolRegistry as _StructureMapToolRegistry,
            )

            structure_map_tools = structure_map_tool_factory.build_tools(
                project_id=NotBlankStr(project_id),
            )
            if structure_map_tools:
                registry = _StructureMapToolRegistry(
                    [*registry.all_tools(), *structure_map_tools],
                )
        if memory_strategy is not None:
            from synthorg.memory.tools import (  # noqa: PLC0415
                registry_with_memory_tools,
            )

            registry = registry_with_memory_tools(
                registry,
                memory_strategy,
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

        agent_tools = identity.tools
        if self._mcp_self_consumer is not None:
            mcp_tools = self._mcp_self_consumer(
                identity,
                agent_tools.access_level,
            )
            if mcp_tools:
                registry = _ToolRegistry2(
                    [*registry.all_tools(), *mcp_tools],
                )

        checker = ToolPermissionChecker.from_permissions(agent_tools)
        interceptor = self._make_security_interceptor(effective_autonomy)
        # The one place an agent's tool surface is final. Every step above
        # rebuilt the registry, so this is the only count and list an operator
        # can act on: what this agent could actually reach for this task.
        logger.info(
            TOOL_REGISTRY_BUILT,
            agent_id=str(identity.id),
            task_id=task_id,
            tool_count=len(registry.all_tools()),
            tools=sorted(tool.name for tool in registry.all_tools()),
        )
        invoker = ToolInvoker(
            registry,
            permission_checker=checker,
            security_interceptor=interceptor,
            agent_id=str(identity.id),
            task_id=task_id,
            invocation_tracker=self._tool_invocation_tracker,
            policy_engine=self._policy_engine,
            policy_evaluation_mode=self._policy_evaluation_mode,
            cost_tracker=self._cost_tracker,
        )
        deferred.bind(invoker)
        return invoker
