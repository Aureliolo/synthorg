# module-kind: declarative
"""The declared subsystems.

One entry per subsystem whose activation depends on something that can be
absent at boot and arrive later. ``activate`` is the wiring function itself;
``requires`` states the dependency check where the reconciler can order by it
and report on it, rather than leaving it buried in the function body where
only that one call site can act on it.

Collaborators are read from live state at activation rather than captured
when the table is built. A value captured once is the same mistake as a
wiring decision made once: the provider registry in particular is rebuilt
after a persisted reload, and a captured reference would be the stale one.

Wiring imports are local to each adapter. This module names most of the
wiring tree, so importing those eagerly would pull the whole boot graph into
any cold import of :mod:`synthorg.api`. The collaborator TYPES below are
different: they are protocols and leaf types the accessors have to name so an
activation call cannot silently take its arguments in the wrong order.
"""

from synthorg._core.features import require_service
from synthorg.api.state import AppState
from synthorg.api.subsystems.spec import CapabilityId, SubsystemSpec
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.meta.config import SelfImprovementConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.subsystem import SUBSYSTEM_ACTIVATION_DECLINED
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)


async def _si_config(app_state: AppState) -> SelfImprovementConfig:
    """Return the self-improvement config several activations need.

    Reads the slice cache rather than re-parsing: nine activations ask for it
    in a single pass, and a subsystem gated on an operator toggle asks again
    on every pass thereafter. ``MetaSelfImprovementSettingsSubscriber``
    invalidates the cache on an edit, so a shared instance is as live as a
    fresh parse.

    Args:
        app_state: Application state carrying the cached config.

    Returns:
        The resolved config.
    """
    from synthorg.meta.state import self_improvement_config_of  # noqa: PLC0415

    return await self_improvement_config_of(app_state)


def _registry(app_state: AppState) -> ProviderRegistry | None:
    """Return the live provider registry.

    Args:
        app_state: Application state carrying the provider slice.

    Returns:
        The registry, or ``None`` when no provider is configured.
    """
    from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415

    return app_state.slice(ProvidersStateSlice).registry


def _persistence(app_state: AppState) -> PersistenceBackend | None:
    """Return the live persistence backend.

    Args:
        app_state: Application state carrying the persistence slice.

    Returns:
        The backend, or ``None`` before it connects.
    """
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    return app_state.slice(PersistenceStateSlice).backend


def _cost_tracker(app_state: AppState) -> CostTrackerProtocol | None:
    """Return the live cost tracker.

    Args:
        app_state: Application state carrying the budget slice.

    Returns:
        The cost tracker, or ``None`` when unwired.
    """
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415

    return app_state.slice(BudgetStateSlice).cost_tracker


def _approval_store(app_state: AppState) -> ApprovalStoreProtocol | None:
    """Return the live approval store.

    Args:
        app_state: Application state carrying the approval slice.

    Returns:
        The store, or ``None`` when unwired.
    """
    from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415

    return app_state.slice(ApprovalStateSlice).store


def _required_approval_store(app_state: AppState) -> ApprovalStoreProtocol:
    """Return the approval store for an activation that cannot run without it.

    Only for a subsystem declaring ``APPROVAL_STORE`` in ``requires``: the
    reconciler has already checked it is there, and raising is the honest
    response if that ever stops being true, rather than passing ``None`` into
    a collaborator whose signature says it never receives one.

    Args:
        app_state: Application state carrying the approval slice.

    Returns:
        The wired store.
    """
    return require_service(_approval_store(app_state), "Approval Store")


async def _activate_memory_backend(app_state: AppState) -> None:
    """Wire the durable agent-memory backend."""
    from synthorg.api.lifecycle_helpers.memory_backend_wiring import (  # noqa: PLC0415
        wire_memory_backend,
    )

    await wire_memory_backend(app_state)


async def _deactivate_memory_backend(app_state: AppState) -> None:
    """Take the durable agent-memory backend down."""
    from synthorg.api.lifecycle_helpers.memory_backend_wiring import (  # noqa: PLC0415
        unwire_memory_backend,
    )

    await unwire_memory_backend(app_state)


async def _activate_org_memory_backend(app_state: AppState) -> None:
    """Wire the hybrid org-memory backend."""
    from synthorg.api.lifecycle_helpers.org_memory_wiring import (  # noqa: PLC0415
        wire_org_memory_backend,
    )

    await wire_org_memory_backend(app_state)


async def _activate_evolution_outcomes(app_state: AppState) -> None:
    """Wire the durable evolution-outcome store."""
    from synthorg.api.lifecycle_helpers.evolution_outcomes_wiring import (  # noqa: PLC0415
        wire_evolution_outcomes,
    )

    await wire_evolution_outcomes(app_state)


async def _activate_docs_engine(app_state: AppState) -> None:
    """Wire the docs engine."""
    from synthorg.api.lifecycle_helpers.feature_wiring import (  # noqa: PLC0415
        _wire_docs_engine,
    )

    await _wire_docs_engine(app_state)


async def _deactivate_docs_engine(app_state: AppState) -> None:
    """Take the docs engine down."""
    from synthorg.api.lifecycle_helpers.feature_wiring import (  # noqa: PLC0415
        unwire_docs_engine,
    )

    await unwire_docs_engine(app_state)


async def _activate_knowledge_engine(app_state: AppState) -> None:
    """Wire the knowledge + provenance substrate."""
    from synthorg.api.lifecycle_helpers.knowledge_wiring import (  # noqa: PLC0415
        wire_knowledge_engine,
    )

    await wire_knowledge_engine(app_state, provider_registry=_registry(app_state))


async def _deactivate_knowledge_engine(app_state: AppState) -> None:
    """Take the knowledge substrate down."""
    from synthorg.api.lifecycle_helpers.knowledge_wiring import (  # noqa: PLC0415
        unwire_knowledge_engine,
    )

    await unwire_knowledge_engine(app_state)


async def _activate_project_brain(app_state: AppState) -> None:
    """Wire the long-horizon project brain."""
    from synthorg.api.lifecycle_helpers.project_brain_wiring import (  # noqa: PLC0415
        wire_project_brain,
    )

    await wire_project_brain(app_state)


async def _deactivate_project_brain(app_state: AppState) -> None:
    """Take the project brain down."""
    from synthorg.api.lifecycle_helpers.project_brain_wiring import (  # noqa: PLC0415
        unwire_project_brain,
    )

    await unwire_project_brain(app_state)


async def _activate_research_engine(app_state: AppState) -> None:
    """Wire the research subsystem."""
    from synthorg.api.lifecycle_helpers.feature_wiring import (  # noqa: PLC0415
        _wire_research_engine,
    )

    await _wire_research_engine(app_state, provider_registry=_registry(app_state))


async def _activate_charter_engine(app_state: AppState) -> None:
    """Wire the charter interview engine."""
    from synthorg.api.lifecycle_helpers.charter_wiring import (  # noqa: PLC0415
        _wire_charter_engine,
    )

    await _wire_charter_engine(
        app_state,
        provider_registry=_registry(app_state),
        persistence=_persistence(app_state),
        cost_tracker=_cost_tracker(app_state),
        si_config=await _si_config(app_state),
    )


async def _activate_toolsmith(app_state: AppState) -> None:
    """Wire the self-extending toolkit."""
    from synthorg.api.lifecycle_helpers.toolsmith_wiring import (  # noqa: PLC0415
        wire_toolsmith,
    )

    await wire_toolsmith(
        app_state,
        provider_registry=_registry(app_state),
        persistence=_persistence(app_state),
        approval_store=_approval_store(app_state),
        cost_tracker=_cost_tracker(app_state),
    )


async def _activate_model_refresh(app_state: AppState) -> None:
    """Wire the periodic model-refresh service."""
    from synthorg.api.lifecycle_helpers.model_refresh_wiring import (  # noqa: PLC0415
        wire_model_refresh,
    )

    await wire_model_refresh(app_state)


async def _activate_custom_rules(app_state: AppState) -> None:
    """Wire the custom-rules facade."""
    from synthorg.api.lifecycle_helpers.feature_wiring import (  # noqa: PLC0415
        _wire_custom_rules_service,
    )

    await _wire_custom_rules_service(app_state)


async def _activate_budget_versions(app_state: AppState) -> None:
    """Wire the budget-config version history facade."""
    from synthorg.api.lifecycle_helpers.feature_wiring import (  # noqa: PLC0415
        _wire_budget_versions_service,
    )

    await _wire_budget_versions_service(app_state)


async def _activate_signals(app_state: AppState) -> None:
    """Wire the signals service."""
    from synthorg.api.lifecycle_helpers.feature_wiring import (  # noqa: PLC0415
        _wire_signals_service,
    )

    await _wire_signals_service(
        app_state,
        effective_approval_store=_required_approval_store(app_state),
    )


async def _activate_analytics(app_state: AppState) -> None:
    """Wire the analytics service."""
    from synthorg.api.lifecycle_helpers.meta_wiring import (  # noqa: PLC0415
        _wire_analytics_service,
    )

    await _wire_analytics_service(app_state)


async def _activate_reports(app_state: AppState) -> None:
    """Wire the reports service."""
    from synthorg.api.lifecycle_helpers.meta_wiring import (  # noqa: PLC0415
        _wire_reports_service,
    )

    await _wire_reports_service(app_state)


async def _activate_experiments(app_state: AppState) -> None:
    """Wire the experiment service."""
    from synthorg.api.lifecycle_helpers.meta_wiring import (  # noqa: PLC0415
        _wire_experiment_service,
    )

    await _wire_experiment_service(app_state)


async def _activate_ab_tests(app_state: AppState) -> None:
    """Wire the A/B test repository."""
    from synthorg.api.lifecycle_helpers.meta_wiring import (  # noqa: PLC0415
        _wire_ab_test_repo,
    )

    await _wire_ab_test_repo(app_state)


async def _activate_alerts(app_state: AppState) -> None:
    """Wire the alert repository."""
    from synthorg.api.lifecycle_helpers.meta_wiring import (  # noqa: PLC0415
        _wire_alert_repo,
    )

    await _wire_alert_repo(app_state)


async def _activate_org_inflection_monitor(app_state: AppState) -> None:
    """Wire the org-inflection monitor."""
    from synthorg.api.lifecycle_helpers.meta_wiring import (  # noqa: PLC0415
        _wire_org_inflection_monitor,
    )

    await _wire_org_inflection_monitor(app_state, si_config=await _si_config(app_state))


async def _activate_self_improvement(app_state: AppState) -> None:
    """Wire the self-improvement apply path."""
    from synthorg.api.lifecycle_helpers.meta_apply_wiring import (  # noqa: PLC0415
        wire_meta_apply,
    )

    await wire_meta_apply(app_state)


async def _deactivate_self_improvement(app_state: AppState) -> None:
    """Take the self-improvement apply path down."""
    from synthorg.api.lifecycle_helpers.meta_apply_wiring import (  # noqa: PLC0415
        unwire_meta_apply,
    )

    await unwire_meta_apply(app_state)


async def _activate_chief_of_staff_chat(app_state: AppState) -> None:
    """Wire the Chief-of-Staff chat surface."""
    from synthorg.api.lifecycle_helpers.feature_wiring import (  # noqa: PLC0415
        _wire_chief_of_staff_chat,
    )

    await _wire_chief_of_staff_chat(
        app_state,
        provider_registry=_registry(app_state),
        cost_tracker=_cost_tracker(app_state),
        si_config=await _si_config(app_state),
    )


async def _activate_chief_of_staff_proposer(app_state: AppState) -> None:
    """Wire the Chief-of-Staff proposer.

    The guard raises when propose or invite is enabled over a backend that
    does not advertise durable conversational approvals. Both shipped
    backends do, so this is forward-looking rather than reachable today.
    When it fires it is a refusal to wire, not a fault: the subsystem reads
    BLOCKED, its controller 503s, and the rest of the pass is unaffected.
    """
    from synthorg.api.lifecycle_helpers.conversational_wiring import (  # noqa: PLC0415
        ConversationalApprovalsUnsupportedError,
        wire_chief_of_staff_proposer,
    )

    try:
        await wire_chief_of_staff_proposer(
            app_state,
            provider_registry=_registry(app_state),
            persistence=_persistence(app_state),
            cost_tracker=_cost_tracker(app_state),
            effective_approval_store=_required_approval_store(app_state),
            si_config=await _si_config(app_state),
        )
    except ConversationalApprovalsUnsupportedError as exc:
        # lint-allow: swallow-ok -- the guard's refusal is the intended
        # outcome. Caught by its own type rather than the shared 503 base,
        # so an absent collaborator (which require_service reports with that
        # base) still marks the subsystem FAILED instead of reading as a
        # deliberate refusal.
        logger.warning(
            SUBSYSTEM_ACTIVATION_DECLINED,
            subsystem="chief_of_staff_proposer",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _activate_conversational_actor(app_state: AppState) -> None:
    """Wire the conversational actor."""
    from synthorg.api.lifecycle_helpers.conversational_wiring import (  # noqa: PLC0415
        wire_conversational_actor,
    )

    await wire_conversational_actor(app_state, si_config=await _si_config(app_state))


async def _deactivate_conversational_actor(app_state: AppState) -> None:
    """Take the conversational actor down."""
    from synthorg.api.lifecycle_helpers.conversational_wiring import (  # noqa: PLC0415
        unwire_conversational_actor,
    )

    await unwire_conversational_actor(app_state)


async def _activate_group_chat(app_state: AppState) -> None:
    """Wire the group-chat service."""
    from synthorg.api.lifecycle_helpers.conversational_wiring import (  # noqa: PLC0415
        wire_group_chat_service,
    )

    await wire_group_chat_service(
        app_state,
        provider_registry=_registry(app_state),
        persistence=_persistence(app_state),
        cost_tracker=_cost_tracker(app_state),
        si_config=await _si_config(app_state),
    )


async def _activate_operator_console(app_state: AppState) -> None:
    """Wire the operator console."""
    from synthorg.api.lifecycle_helpers.conversational_console_wiring import (  # noqa: PLC0415
        wire_operator_console,
    )

    await wire_operator_console(app_state, si_config=await _si_config(app_state))


async def _deactivate_operator_console(app_state: AppState) -> None:
    """Take the operator console down."""
    from synthorg.api.lifecycle_helpers.conversational_console_wiring import (  # noqa: PLC0415
        unwire_operator_console,
    )

    await unwire_operator_console(app_state)


async def _activate_sprint_service(app_state: AppState) -> None:
    """Wire the sprint service."""
    from synthorg.api.lifecycle_helpers.sprint_wiring import (  # noqa: PLC0415
        wire_sprint_service,
    )

    await wire_sprint_service(app_state)


async def _activate_tool_call_feedback(app_state: AppState) -> None:
    """Wire the tool-call feedback tracker."""
    from synthorg.api.lifecycle_helpers.tool_call_feedback_wiring import (  # noqa: PLC0415
        wire_tool_call_feedback,
    )

    await wire_tool_call_feedback(app_state)


async def _activate_project_rollup(app_state: AppState) -> None:
    """Wire the initiative rollup service."""
    from synthorg.api.lifecycle_helpers.project_rollup_wiring import (  # noqa: PLC0415
        wire_project_rollup_service,
    )

    await wire_project_rollup_service(app_state)


async def _activate_initiative_tail(app_state: AppState) -> None:
    """Attach the integrate / evaluate / replan tail onto the wired rollup."""
    from synthorg.api.lifecycle_helpers.project_rollup_wiring import (  # noqa: PLC0415
        attach_initiative_tail,
    )

    await attach_initiative_tail(app_state)


async def _activate_kanban_board(app_state: AppState) -> None:
    """Wire the Kanban board service."""
    from synthorg.api.lifecycle_helpers.kanban_wiring import (  # noqa: PLC0415
        wire_kanban_board,
    )

    await wire_kanban_board(app_state)


async def _activate_run_narrator(app_state: AppState) -> None:
    """Attach the post-run narrator to the work pipeline."""
    from synthorg.api.lifecycle_helpers.narrative_wiring import (  # noqa: PLC0415
        wire_run_narrator,
    )

    await wire_run_narrator(
        app_state,
        provider_registry=_registry(app_state),
        cost_tracker=_cost_tracker(app_state),
        si_config=await _si_config(app_state),
    )


async def _deactivate_run_narrator(app_state: AppState) -> None:
    """Detach the post-run narrator from the work pipeline."""
    from synthorg.api.lifecycle_helpers.narrative_wiring import (  # noqa: PLC0415
        unwire_run_narrator,
    )

    await unwire_run_narrator(app_state)


async def _activate_refinement_router(app_state: AppState) -> None:
    """Attach the refinement router to the work pipeline."""
    from synthorg.api.lifecycle_helpers.refinement_wiring import (  # noqa: PLC0415
        wire_refinement_router,
    )

    await wire_refinement_router(app_state)


async def _activate_plan_review_gate(app_state: AppState) -> None:
    """Attach the human plan-approval gate to the work pipeline."""
    from synthorg.api.lifecycle_helpers.plan_review_wiring import (  # noqa: PLC0415
        wire_plan_review_gate,
    )

    await wire_plan_review_gate(app_state)


async def _activate_plan_review_panel(app_state: AppState) -> None:
    """Attach the stakeholder plan-review panel to the work pipeline."""
    from synthorg.api.lifecycle_helpers.plan_review_wiring import (  # noqa: PLC0415
        wire_plan_review_panel,
    )

    await wire_plan_review_panel(
        app_state,
        provider_registry=_registry(app_state),
        cost_tracker=_cost_tracker(app_state),
    )


async def _activate_plan_dispatcher(app_state: AppState) -> None:
    """Attach the conversational plan dispatcher to the proposer."""
    from synthorg.api.lifecycle_helpers.conversational_wiring import (  # noqa: PLC0415
        wire_conversational_plan_dispatcher,
    )

    await wire_conversational_plan_dispatcher(app_state)


async def _activate_steering_service(app_state: AppState) -> None:
    """Wire the mid-flight steering service."""
    from synthorg.api._app_wiring import wire_steering_service  # noqa: PLC0415

    await wire_steering_service(
        app_state,
        provider_registry=_registry(app_state),
    )


async def _deactivate_steering_service(app_state: AppState) -> None:
    """Take the steering service down."""
    from synthorg.api._app_wiring import unwire_steering_service  # noqa: PLC0415

    await unwire_steering_service(app_state)


async def _activate_deliverable_receipts(app_state: AppState) -> None:
    """Wire the deliverable-receipts service."""
    from synthorg.api.lifecycle_helpers.deliverable_receipt_wiring import (  # noqa: PLC0415
        wire_deliverable_receipts,
    )

    await wire_deliverable_receipts(app_state)


async def _deactivate_deliverable_receipts(app_state: AppState) -> None:
    """Take the deliverable-receipts service down."""
    from synthorg.api.lifecycle_helpers.deliverable_receipt_wiring import (  # noqa: PLC0415
        unwire_deliverable_receipts,
    )

    await unwire_deliverable_receipts(app_state)


async def _activate_fine_tune_orchestrator(app_state: AppState) -> None:
    """Wire the embedding fine-tune orchestrator."""
    from synthorg.api.lifecycle_helpers.finetune_wiring import (  # noqa: PLC0415
        wire_fine_tune_orchestrator,
    )

    await wire_fine_tune_orchestrator(app_state)


async def _activate_team_service(app_state: AppState) -> None:
    """Wire the team service."""
    from synthorg.api.lifecycle_helpers.organization_wiring import (  # noqa: PLC0415
        wire_team_service,
    )

    await wire_team_service(app_state)


async def _activate_company_read_service(app_state: AppState) -> None:
    """Wire the company read facade."""
    from synthorg.api.lifecycle_helpers.organization_wiring import (  # noqa: PLC0415
        wire_company_read_service,
    )

    # Persistence is deliberately absent from ``requires``: company reads
    # degrade to the resolver alone, so ``None`` here is a supported state
    # rather than a missing dependency. Read through the typed accessor all
    # the same, so renaming the property fails the type check instead of
    # silently reporting every backend disconnected.
    persistence = _persistence(app_state)
    await wire_company_read_service(
        app_state,
        persistence,
        connected=persistence is not None and persistence.is_connected,
    )


async def _activate_role_version_service(app_state: AppState) -> None:
    """Wire the role-version read facade."""
    from synthorg.api.lifecycle_helpers.organization_wiring import (  # noqa: PLC0415
        wire_role_version_service,
    )

    backend = _persistence(app_state)
    if backend is None:
        # Declared in requires, so the reconciler should not have reached
        # here. Returning leaves the subsystem reading not-active and the
        # next pass retries, which is what every other unmet dependency does.
        return
    await wire_role_version_service(app_state, backend)


async def _activate_plan_item_reply(app_state: AppState) -> None:
    """Wire the conversational plan-item reply service."""
    from synthorg.api.lifecycle_helpers.plan_review_wiring import (  # noqa: PLC0415
        wire_plan_item_reply_service,
    )

    await wire_plan_item_reply_service(
        app_state,
        provider_registry=_registry(app_state),
        cost_tracker=_cost_tracker(app_state),
    )


async def _activate_analytics_collector(app_state: AppState) -> None:
    """Configure the cross-deployment analytics collector role."""
    from synthorg.api.lifecycle_helpers.meta_wiring import (  # noqa: PLC0415
        wire_analytics_collector,
    )

    await wire_analytics_collector(si_config=await _si_config(app_state))


async def _activate_eval_loop(app_state: AppState) -> None:
    """Wire the HR evaluation loop."""
    from synthorg.api.lifecycle_helpers.eval_loop_wiring import (  # noqa: PLC0415
        wire_eval_loop,
    )

    await wire_eval_loop(app_state, provider_registry=_registry(app_state))


async def _activate_pruning(app_state: AppState) -> None:
    """Wire the agent-pruning service."""
    from synthorg.api.lifecycle_helpers.pruning_wiring import (  # noqa: PLC0415
        wire_pruning,
    )

    await wire_pruning(app_state)


async def _activate_scaling(app_state: AppState) -> None:
    """Wire the agent-scaling service."""
    from synthorg.api.lifecycle_helpers.scaling_wiring import (  # noqa: PLC0415
        wire_scaling,
    )

    await wire_scaling(app_state)


async def _activate_quota_poller(app_state: AppState) -> None:
    """Wire the provider quota poller."""
    from synthorg.api.lifecycle_helpers.budget_wiring import (  # noqa: PLC0415
        wire_quota_poller,
    )

    await wire_quota_poller(app_state)


async def _activate_strategy_context(app_state: AppState) -> None:
    """Bind the ambient strategic-context provider."""
    from synthorg.api.lifecycle_helpers.strategy_context_wiring import (  # noqa: PLC0415
        wire_strategy_context,
    )

    await wire_strategy_context(app_state)


async def _activate_risk_override(app_state: AppState) -> None:
    """Wire the risk-override service."""
    from synthorg.api.lifecycle_helpers.security_wiring import (  # noqa: PLC0415
        wire_risk_override_service,
    )
    from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415

    await wire_risk_override_service(
        app_state,
        approval_timeout_config=app_state.config.config.approval_timeout,
        approval_timeout_scheduler=app_state.slice(
            ApprovalStateSlice
        ).timeout_scheduler,
    )


SUBSYSTEMS: tuple[SubsystemSpec, ...] = (
    SubsystemSpec(
        name="memory_backend",
        provides=CapabilityId.MEMORY_BACKEND,
        requires=(CapabilityId.PERSISTENCE,),
        activate=_activate_memory_backend,
        deactivate=_deactivate_memory_backend,
        # Every one of these is read at connect time and baked into the
        # backend, the embedder or the consolidation loop, so changing one
        # means replacing the instance rather than nudging it.
        settings=(
            "memory.backend",
            "memory.embedder_model",
            "memory.embedder_dims",
            "memory.consolidation_interval",
        ),
        rebuild_on_change=True,
    ),
    SubsystemSpec(
        name="org_memory_backend",
        provides=CapabilityId.ORG_MEMORY_BACKEND,
        requires=(CapabilityId.PERSISTENCE,),
        activate=_activate_org_memory_backend,
    ),
    SubsystemSpec(
        name="evolution_outcomes",
        provides=CapabilityId.EVOLUTION_OUTCOMES,
        requires=(CapabilityId.PERSISTENCE,),
        activate=_activate_evolution_outcomes,
    ),
    # The three below bake the memory backend into what they build, so a
    # replacement has to reach them too: without the teardown they would keep
    # reading through the instance the memory subsystem just disconnected,
    # and keep reporting themselves up while they did.
    SubsystemSpec(
        name="docs_engine",
        provides=CapabilityId.DOCS_ENGINE,
        requires=(CapabilityId.PERSISTENCE, CapabilityId.MEMORY_BACKEND),
        activate=_activate_docs_engine,
        deactivate=_deactivate_docs_engine,
        rebuild_on_change=True,
    ),
    SubsystemSpec(
        name="knowledge_engine",
        provides=CapabilityId.KNOWLEDGE_ENGINE,
        requires=(CapabilityId.PERSISTENCE, CapabilityId.MEMORY_BACKEND),
        activate=_activate_knowledge_engine,
        deactivate=_deactivate_knowledge_engine,
        rebuild_on_change=True,
    ),
    SubsystemSpec(
        name="project_brain",
        provides=CapabilityId.PROJECT_BRAIN,
        requires=(
            CapabilityId.PERSISTENCE,
            CapabilityId.MEMORY_BACKEND,
            CapabilityId.WORKSPACE_SERVICE,
        ),
        activate=_activate_project_brain,
        deactivate=_deactivate_project_brain,
        rebuild_on_change=True,
    ),
    SubsystemSpec(
        name="research_engine",
        provides=CapabilityId.RESEARCH_ENGINE,
        requires=(
            CapabilityId.PERSISTENCE,
            CapabilityId.SETTINGS_SERVICE,
            CapabilityId.PROVIDER_REGISTRY,
        ),
        activate=_activate_research_engine,
    ),
    SubsystemSpec(
        name="charter_engine",
        provides=CapabilityId.CHARTER_ENGINE,
        requires=(CapabilityId.PERSISTENCE, CapabilityId.PROVIDER_REGISTRY),
        activate=_activate_charter_engine,
        # Blank by default, so an empty-company boot leaves the interview
        # unwired; the operator naming a model is what brings it up.
        settings=("charter.interview_model",),
    ),
    SubsystemSpec(
        name="toolsmith",
        provides=CapabilityId.TOOLSMITH,
        # The default provider, not merely a registry: toolsmith dispatches
        # with no per-feature model, so an unbound registry resolves nothing.
        requires=(CapabilityId.PERSISTENCE, CapabilityId.DEFAULT_PROVIDER),
        activate=_activate_toolsmith,
    ),
    SubsystemSpec(
        name="model_refresh",
        provides=CapabilityId.MODEL_REFRESH,
        requires=(CapabilityId.SETTINGS_RESOLVER,),
        activate=_activate_model_refresh,
    ),
    SubsystemSpec(
        name="custom_rules",
        provides=CapabilityId.CUSTOM_RULES,
        requires=(CapabilityId.PERSISTENCE,),
        activate=_activate_custom_rules,
    ),
    SubsystemSpec(
        name="budget_versions_service",
        provides=CapabilityId.BUDGET_VERSIONS_SERVICE,
        requires=(CapabilityId.PERSISTENCE,),
        activate=_activate_budget_versions,
    ),
    SubsystemSpec(
        name="signals_service",
        provides=CapabilityId.SIGNALS_SERVICE,
        requires=(CapabilityId.PERSISTENCE, CapabilityId.APPROVAL_STORE),
        activate=_activate_signals,
    ),
    SubsystemSpec(
        name="analytics_service",
        provides=CapabilityId.ANALYTICS_SERVICE,
        requires=(CapabilityId.SIGNALS_SERVICE,),
        activate=_activate_analytics,
    ),
    SubsystemSpec(
        name="reports_service",
        provides=CapabilityId.REPORTS_SERVICE,
        requires=(CapabilityId.ANALYTICS_SERVICE,),
        activate=_activate_reports,
    ),
    SubsystemSpec(
        name="experiment_service",
        provides=CapabilityId.EXPERIMENT_SERVICE,
        requires=(CapabilityId.PERSISTENCE,),
        activate=_activate_experiments,
    ),
    SubsystemSpec(
        name="ab_test_repo",
        provides=CapabilityId.AB_TEST_REPO,
        requires=(CapabilityId.PERSISTENCE,),
        activate=_activate_ab_tests,
    ),
    SubsystemSpec(
        name="alert_repo",
        provides=CapabilityId.ALERT_REPO,
        requires=(CapabilityId.PERSISTENCE,),
        activate=_activate_alerts,
    ),
    SubsystemSpec(
        name="org_inflection_monitor",
        provides=CapabilityId.ORG_INFLECTION_MONITOR,
        requires=(CapabilityId.ALERT_REPO,),
        activate=_activate_org_inflection_monitor,
    ),
    SubsystemSpec(
        name="self_improvement",
        provides=CapabilityId.SELF_IMPROVEMENT,
        requires=(CapabilityId.PERSISTENCE,),
        activate=_activate_self_improvement,
        deactivate=_deactivate_self_improvement,
    ),
    SubsystemSpec(
        name="chief_of_staff_chat",
        provides=CapabilityId.CHIEF_OF_STAFF_CHAT,
        requires=(CapabilityId.PROVIDER_REGISTRY,),
        activate=_activate_chief_of_staff_chat,
        # Each per-feature model is blank by default, so the feature stays
        # unwired until an operator names one. The value itself is re-read per
        # call; what the declaration buys is the unwired -> wired transition a
        # live read cannot make on its own.
        settings=("chief_of_staff.chat_model",),
    ),
    SubsystemSpec(
        name="chief_of_staff_proposer",
        provides=CapabilityId.CHIEF_OF_STAFF_PROPOSER,
        requires=(
            CapabilityId.PERSISTENCE,
            CapabilityId.PROVIDER_REGISTRY,
            CapabilityId.APPROVAL_STORE,
        ),
        activate=_activate_chief_of_staff_proposer,
        settings=(
            "chief_of_staff.propose_model",
            "chief_of_staff.routing_model",
        ),
    ),
    # The toggle is what the operator changes, and the builder is fail-closed
    # on it, so a rebuild IS the live path: teardown, then an activation that
    # re-runs the governance gate and installs nothing when the answer is no.
    SubsystemSpec(
        name="conversational_actor",
        provides=CapabilityId.CONVERSATIONAL_ACTOR,
        requires=(CapabilityId.CHIEF_OF_STAFF_PROPOSER,),
        activate=_activate_conversational_actor,
        deactivate=_deactivate_conversational_actor,
        settings=("chief_of_staff.direct_mcp_enabled",),
        rebuild_on_change=True,
    ),
    SubsystemSpec(
        name="group_chat",
        provides=CapabilityId.GROUP_CHAT,
        # The cost tracker, approval store and config resolver are read
        # through and passed as None when absent: the service runs untracked
        # and ungated rather than not at all. Requiring them would hold the
        # whole surface down for collaborators it degrades without.
        requires=(
            CapabilityId.PERSISTENCE,
            CapabilityId.PROVIDER_REGISTRY,
            CapabilityId.AGENT_REGISTRY,
        ),
        activate=_activate_group_chat,
    ),
    SubsystemSpec(
        name="operator_console",
        provides=CapabilityId.OPERATOR_CONSOLE,
        requires=(CapabilityId.PERSISTENCE,),
        activate=_activate_operator_console,
        deactivate=_deactivate_operator_console,
        settings=(
            "chief_of_staff.operator_console_enabled",
            "chief_of_staff.operator_console_model",
        ),
        rebuild_on_change=True,
    ),
    SubsystemSpec(
        name="sprint_service",
        provides=CapabilityId.SPRINT_SERVICE,
        requires=(CapabilityId.PERSISTENCE,),
        activate=_activate_sprint_service,
    ),
    SubsystemSpec(
        name="tool_call_feedback",
        provides=CapabilityId.TOOL_CALL_FEEDBACK,
        requires=(CapabilityId.PERSISTENCE,),
        activate=_activate_tool_call_feedback,
    ),
    SubsystemSpec(
        name="risk_override_service",
        provides=CapabilityId.RISK_OVERRIDE_SERVICE,
        # The timeout scheduler this hot-swaps a classifier onto is built
        # from the approval store, so naming the store is what turns "never
        # came up" into a reported unmet requirement. The tiered-policy
        # config is not a capability: an untiered deployment has nothing to
        # override, so activation declines and the sweep re-reads it.
        requires=(CapabilityId.PERSISTENCE, CapabilityId.APPROVAL_STORE),
        activate=_activate_risk_override,
    ),
    SubsystemSpec(
        name="steering_service",
        provides=CapabilityId.STEERING_SERVICE,
        # The brain is the directive write path; without it a recorded
        # directive would go nowhere.
        requires=(
            CapabilityId.PERSISTENCE,
            CapabilityId.TASK_ENGINE,
            CapabilityId.PROJECT_BRAIN,
        ),
        activate=_activate_steering_service,
        deactivate=_deactivate_steering_service,
        rebuild_on_change=True,
    ),
    SubsystemSpec(
        name="deliverable_receipts",
        provides=CapabilityId.DELIVERABLE_RECEIPTS,
        # No docs engine means no deliverables to attach a receipt to.
        requires=(CapabilityId.PERSISTENCE, CapabilityId.DOCS_ENGINE),
        activate=_activate_deliverable_receipts,
        deactivate=_deactivate_deliverable_receipts,
        rebuild_on_change=True,
    ),
    SubsystemSpec(
        name="fine_tune_orchestrator",
        provides=CapabilityId.FINE_TUNE_ORCHESTRATOR,
        requires=(CapabilityId.PERSISTENCE,),
        activate=_activate_fine_tune_orchestrator,
    ),
    SubsystemSpec(
        name="team_service",
        provides=CapabilityId.TEAM_SERVICE,
        # Reads the company-structure settings blob, so a settings-less boot
        # leaves the team tools correctly unavailable.
        requires=(CapabilityId.SETTINGS_SERVICE,),
        activate=_activate_team_service,
    ),
    SubsystemSpec(
        name="company_read_service",
        provides=CapabilityId.COMPANY_READ_SERVICE,
        # The org-mutation service and the resolver are wired in one
        # synchronous call, with the resolver set last, so by the time
        # SETTINGS_RESOLVER reads present the other is already there.
        requires=(CapabilityId.SETTINGS_RESOLVER,),
        activate=_activate_company_read_service,
    ),
    SubsystemSpec(
        name="role_version_service",
        provides=CapabilityId.ROLE_VERSION_SERVICE,
        # Role-version reads are entirely durable.
        requires=(CapabilityId.PERSISTENCE,),
        activate=_activate_role_version_service,
    ),
    SubsystemSpec(
        name="plan_item_reply_service",
        provides=CapabilityId.PLAN_ITEM_REPLY_SERVICE,
        requires=(CapabilityId.PROVIDER_REGISTRY,),
        activate=_activate_plan_item_reply,
    ),
    SubsystemSpec(
        name="analytics_collector",
        provides=CapabilityId.ANALYTICS_COLLECTOR,
        requires=(CapabilityId.SETTINGS_SERVICE,),
        activate=_activate_analytics_collector,
    ),
    SubsystemSpec(
        name="project_rollup_service",
        provides=CapabilityId.PROJECT_ROLLUP_SERVICE,
        requires=(CapabilityId.PERSISTENCE, CapabilityId.TASK_ENGINE),
        activate=_activate_project_rollup,
    ),
    SubsystemSpec(
        # Declared apart from the rollup it attaches to, because the two
        # converge at different times: the rollup needs only persistence and
        # the task engine, which are up before setup configures a provider,
        # while every tail stage needs one of the three below. Folded into the
        # rollup's spec, its early success stood for the tail's, and since the
        # reconciler never revisits a subsystem it reads as converged, the tail
        # could never come up on any boot.
        name="initiative_tail",
        provides=CapabilityId.INITIATIVE_TAIL,
        requires=(
            CapabilityId.PROJECT_ROLLUP_SERVICE,
            CapabilityId.PERSISTENCE,
            CapabilityId.PROVIDER_REGISTRY,
            CapabilityId.AGENT_REGISTRY,
            CapabilityId.WORK_PIPELINE,
            CapabilityId.COORDINATOR,
        ),
        activate=_activate_initiative_tail,
    ),
    SubsystemSpec(
        name="kanban_board",
        provides=CapabilityId.KANBAN_BOARD,
        # The sprint service is an advisory gate the board reads at
        # construction, so it is ordered before rather than merely hoped for.
        requires=(
            CapabilityId.PERSISTENCE,
            CapabilityId.TASK_ENGINE,
            CapabilityId.SETTINGS_RESOLVER,
            CapabilityId.SPRINT_SERVICE,
        ),
        activate=_activate_kanban_board,
    ),
    # The four below mutate the work pipeline in place. Liveness is read from
    # the pipeline's own attachment record, so the reconciler never keeps a
    # second account of what it attached.
    SubsystemSpec(
        name="run_narrator",
        provides=CapabilityId.RUN_NARRATOR,
        # Reads both the docs engine and the project brain at construction.
        requires=(
            CapabilityId.PERSISTENCE,
            CapabilityId.PROVIDER_REGISTRY,
            CapabilityId.WORK_PIPELINE,
            CapabilityId.DOCS_ENGINE,
            CapabilityId.PROJECT_BRAIN,
        ),
        activate=_activate_run_narrator,
        deactivate=_deactivate_run_narrator,
        settings=("chief_of_staff.narrative_model",),
        rebuild_on_change=True,
    ),
    SubsystemSpec(
        name="refinement_router",
        provides=CapabilityId.REFINEMENT_ROUTER,
        requires=(
            CapabilityId.WORK_PIPELINE,
            CapabilityId.CHIEF_OF_STAFF_PROPOSER,
        ),
        activate=_activate_refinement_router,
    ),
    SubsystemSpec(
        name="plan_review_gate",
        provides=CapabilityId.PLAN_REVIEW_GATE,
        requires=(
            CapabilityId.PERSISTENCE,
            CapabilityId.APPROVAL_STORE,
            CapabilityId.WORK_PIPELINE,
        ),
        activate=_activate_plan_review_gate,
    ),
    SubsystemSpec(
        name="plan_review_panel",
        provides=CapabilityId.PLAN_REVIEW_PANEL,
        requires=(CapabilityId.WORK_PIPELINE, CapabilityId.PROVIDER_REGISTRY),
        activate=_activate_plan_review_panel,
    ),
    SubsystemSpec(
        name="conversational_plan_dispatcher",
        provides=CapabilityId.CONVERSATIONAL_PLAN_DISPATCHER,
        requires=(
            CapabilityId.PERSISTENCE,
            CapabilityId.WORK_PIPELINE,
            CapabilityId.CHIEF_OF_STAFF_PROPOSER,
        ),
        activate=_activate_plan_dispatcher,
    ),
    SubsystemSpec(
        name="eval_loop",
        provides=CapabilityId.EVAL_LOOP,
        requires=(
            CapabilityId.PERSISTENCE,
            CapabilityId.AGENT_REGISTRY,
            CapabilityId.PROVIDER_REGISTRY,
        ),
        activate=_activate_eval_loop,
    ),
    SubsystemSpec(
        name="pruning_service",
        provides=CapabilityId.PRUNING_SERVICE,
        requires=(CapabilityId.PERSISTENCE, CapabilityId.AGENT_REGISTRY),
        activate=_activate_pruning,
    ),
    SubsystemSpec(
        name="scaling_service",
        provides=CapabilityId.SCALING_SERVICE,
        requires=(CapabilityId.PERSISTENCE, CapabilityId.AGENT_REGISTRY),
        activate=_activate_scaling,
    ),
    SubsystemSpec(
        name="quota_poller",
        provides=CapabilityId.QUOTA_POLLER,
        requires=(CapabilityId.PROVIDER_REGISTRY,),
        activate=_activate_quota_poller,
    ),
    SubsystemSpec(
        name="strategy_context",
        provides=CapabilityId.STRATEGY_CONTEXT,
        # The memory backend and the meeting orchestrator are optional reads,
        # not requirements: a boot without memory resolves a thinner context
        # rather than none, so only the bus is declared.
        requires=(CapabilityId.MESSAGE_BUS,),
        activate=_activate_strategy_context,
    ),
)
