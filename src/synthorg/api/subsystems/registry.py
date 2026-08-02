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
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.registry import ProviderRegistry


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


async def _activate_knowledge_engine(app_state: AppState) -> None:
    """Wire the knowledge + provenance substrate."""
    from synthorg.api.lifecycle_helpers.knowledge_wiring import (  # noqa: PLC0415
        wire_knowledge_engine,
    )

    await wire_knowledge_engine(app_state, provider_registry=_registry(app_state))


async def _activate_project_brain(app_state: AppState) -> None:
    """Wire the long-horizon project brain."""
    from synthorg.api.lifecycle_helpers.project_brain_wiring import (  # noqa: PLC0415
        wire_project_brain,
    )

    await wire_project_brain(app_state)


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
    """Wire the Chief-of-Staff proposer."""
    from synthorg.api.lifecycle_helpers.conversational_wiring import (  # noqa: PLC0415
        wire_chief_of_staff_proposer,
    )

    await wire_chief_of_staff_proposer(
        app_state,
        provider_registry=_registry(app_state),
        persistence=_persistence(app_state),
        cost_tracker=_cost_tracker(app_state),
        effective_approval_store=_required_approval_store(app_state),
        si_config=await _si_config(app_state),
    )


async def _activate_conversational_actor(app_state: AppState) -> None:
    """Wire the conversational actor."""
    from synthorg.api.lifecycle_helpers.conversational_wiring import (  # noqa: PLC0415
        wire_conversational_actor,
    )

    await wire_conversational_actor(app_state, si_config=await _si_config(app_state))


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
    SubsystemSpec(
        name="docs_engine",
        provides=CapabilityId.DOCS_ENGINE,
        requires=(CapabilityId.PERSISTENCE, CapabilityId.MEMORY_BACKEND),
        activate=_activate_docs_engine,
    ),
    SubsystemSpec(
        name="knowledge_engine",
        provides=CapabilityId.KNOWLEDGE_ENGINE,
        requires=(CapabilityId.PERSISTENCE, CapabilityId.MEMORY_BACKEND),
        activate=_activate_knowledge_engine,
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
    ),
    SubsystemSpec(
        name="chief_of_staff_chat",
        provides=CapabilityId.CHIEF_OF_STAFF_CHAT,
        requires=(CapabilityId.PROVIDER_REGISTRY,),
        activate=_activate_chief_of_staff_chat,
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
    ),
    SubsystemSpec(
        name="conversational_actor",
        provides=CapabilityId.CONVERSATIONAL_ACTOR,
        requires=(CapabilityId.CHIEF_OF_STAFF_PROPOSER,),
        activate=_activate_conversational_actor,
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
)
