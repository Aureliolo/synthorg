"""Factory mixin for :class:`AgentEngine`: approval gate, loop, tool invoker."""

from typing import TYPE_CHECKING, Any

from synthorg.engine._security_factory import (
    make_security_interceptor,
    registry_with_approval_tool,
)
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.loop_selector import (
    build_execution_loop,
    select_loop_type,
)
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_AUTO_SELECTED,
    EXECUTION_LOOP_BUDGET_UNAVAILABLE,
)
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.permissions import ToolPermissionChecker

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task
    from synthorg.engine.loop_protocol import ExecutionLoop
    from synthorg.security.autonomy.models import EffectiveAutonomy
    from synthorg.security.protocol import SecurityInterceptionStrategy

logger = get_logger(__name__)


class AgentEngineFactoriesMixin:
    """Mixin providing approval-gate, loop, and tool-invoker factories."""

    _approval_store: Any
    _parked_context_repo: Any
    _event_stream_hub: Any
    _interrupt_store: Any
    _approval_gate: Any
    _approval_interrupt_timeout_seconds: float | None
    _stagnation_detector: Any
    _compaction_callback: Any
    _auto_loop_config: Any
    _loop: Any
    _hybrid_loop_config: Any
    _plan_execute_config: Any
    _memory_injection_strategy: Any
    _ontology_injection_strategy: Any
    _model_resolver: Any
    _provider_configs: Any
    _provider_registry: Any
    _tool_registry: Any
    _tool_invocation_tracker: Any
    _security_config: Any
    _budget_enforcer: Any
    _audit_log: Any
    _cost_tracker: Any

    def _make_approval_gate(self) -> ApprovalGate | None:
        """Build an ApprovalGate if an approval store is configured.

        The interrupt timeout is sourced from
        ``EngineBridgeConfig.approval_interrupt_timeout_seconds`` via the
        engine's ``approval_interrupt_timeout_seconds`` constructor kwarg
        (projected onto ``self._approval_interrupt_timeout_seconds``).
        When the engine is built without that kwarg the gate falls back
        to its own 300s default so callers that bypass the bridge config
        keep working.
        """
        if self._approval_store is None:
            return None

        from synthorg.security.timeout.park_service import (  # noqa: PLC0415
            ParkService,
        )

        kwargs: dict[str, Any] = {
            "park_service": ParkService(),
            "parked_context_repo": self._parked_context_repo,
            "event_hub": self._event_stream_hub,
            "interrupt_store": self._interrupt_store,
        }
        if self._approval_interrupt_timeout_seconds is not None:
            kwargs["interrupt_timeout_seconds"] = (
                self._approval_interrupt_timeout_seconds
            )
        return ApprovalGate(**kwargs)

    def _make_default_loop(self) -> ExecutionLoop:
        """Build the default ``react`` loop via the shared factory."""
        return build_execution_loop(
            "react",
            approval_gate=self._approval_gate,
            stagnation_detector=self._stagnation_detector,
            compaction_callback=self._compaction_callback,
        )

    async def _resolve_loop(
        self,
        task: Task,
        agent_id: str = "",
        task_id: str = "",
    ) -> ExecutionLoop:
        """Select the execution loop for a task."""
        if self._auto_loop_config is None:
            return self._loop  # type: ignore[no-any-return]

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
        )

    def _make_security_interceptor(
        self,
        effective_autonomy: EffectiveAutonomy | None = None,
    ) -> SecurityInterceptionStrategy | None:
        """Build the SecOps security interceptor if configured."""
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

    def _make_tool_invoker(
        self,
        identity: AgentIdentity,
        task_id: str | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
    ) -> ToolInvoker | None:
        """Create a ToolInvoker with permission checking and security."""
        if self._tool_registry is None:
            return None

        registry = registry_with_approval_tool(
            self._tool_registry,
            self._approval_store,
            identity,
            task_id=task_id,
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

        checker = ToolPermissionChecker.from_permissions(identity.tools)
        interceptor = self._make_security_interceptor(effective_autonomy)
        invoker = ToolInvoker(
            registry,
            permission_checker=checker,
            security_interceptor=interceptor,
            agent_id=str(identity.id),
            task_id=task_id,
            invocation_tracker=self._tool_invocation_tracker,
        )
        deferred.bind(invoker)
        return invoker
