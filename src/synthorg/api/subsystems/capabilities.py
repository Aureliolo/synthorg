# module-kind: declarative
"""Live availability checks for every declared capability.

Each check reads current state and answers one question: is this here right
now? They run on every reconcile pass, so they stay cheap, synchronous and
total. A check that raised would decide the fate of every subsystem behind
it, so none of them may.
"""

from collections.abc import Callable

from synthorg.api.state import AppState
from synthorg.api.subsystems.spec import Capability, CapabilityId
from synthorg.approval.state import ApprovalStateSlice
from synthorg.budget.state import BudgetStateSlice
from synthorg.communication.state import CommunicationStateSlice
from synthorg.deliverable_receipts.state_slice import DeliverableReceiptStateSlice
from synthorg.docs_engine.state import DocsStateSlice
from synthorg.engine.cockpit.state import CockpitStateSlice
from synthorg.engine.initiative.rollup import ProjectRollupService
from synthorg.engine.pipeline.models import PipelineAttachments
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.workspace.state import WorkspaceStateSlice
from synthorg.hr.state import HrStateSlice
from synthorg.knowledge.state import KnowledgeStateSlice
from synthorg.memory.state import MemoryStateSlice
from synthorg.meta.charter.state import CharterStateSlice
from synthorg.meta.state import MetaStateSlice
from synthorg.meta.toolsmith.state import ToolsmithStateSlice
from synthorg.organization.state import OrganizationStateSlice
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.project_brain.state import ProjectBrainStateSlice
from synthorg.providers.management.refresh_state import ModelRefreshStateSlice
from synthorg.providers.state import ProvidersStateSlice
from synthorg.providers.tool_call_feedback.state import ToolCallFeedbackStateSlice
from synthorg.research.state import ResearchStateSlice
from synthorg.security.state import SecurityStateSlice
from synthorg.settings.state import SettingsStateSlice
from synthorg.workers.state import RuntimeStateSlice

# Stands in before the pipeline exists, so the four attachment probes stay
# total without each repeating the same None check.
_NOTHING_ATTACHED = PipelineAttachments(
    narrator=False,
    refinement_router=False,
    plan_review_gate=False,
    plan_review_panel=False,
)


def _attachments(app_state: AppState) -> PipelineAttachments | None:
    """Read the work pipeline's late-bound attachments.

    Args:
        app_state: Application state carrying the engine slice.

    Returns:
        The attachments, or ``None`` before the pipeline is built.
    """
    pipeline = app_state.slice(EngineStateSlice).work_pipeline
    return pipeline.attachments if pipeline is not None else None


def _tail_attached(
    app_state: AppState,
    probe: Callable[[ProjectRollupService], bool],
) -> bool:
    """Report whether one tail collaborator is attached to a wired rollup.

    Args:
        app_state: Application state carrying the engine slice.
        probe: The rollup's own predicate for this collaborator.

    Returns:
        ``True`` once it is attached; ``False`` before the rollup exists, and
        while it is up but this collaborator has not resolved.
    """
    rollup = app_state.slice(EngineStateSlice).project_rollup_service
    return rollup is not None and probe(rollup)


def _analytics_collector_configured() -> bool:
    """Report whether the cross-deployment analytics collector role is up.

    The only capability with no slice to read: the collector is module-global
    controller config, so its own "already configured" predicate is the
    observable.

    Returns:
        ``True`` once the collector role is configured.
    """
    from synthorg.api.controllers.meta_analytics import (  # noqa: PLC0415
        is_analytics_collector_configured,
    )

    return is_analytics_collector_configured()


def _strategy_context_bound() -> bool:
    """Report whether the ambient strategic-context provider is bound.

    Process-global rather than sliced, because strategic context is
    organisation-wide policy the synchronous prompt path reads.

    Returns:
        ``True`` once a refreshed provider is bound.
    """
    from synthorg.engine.strategy.strategic_context_provider import (  # noqa: PLC0415
        current_strategic_context,
    )

    return current_strategic_context() is not None


def _meeting_protocol_registry_installed(app_state: AppState) -> bool:
    """Report whether the orchestrator carries a protocol registry.

    Read from the orchestrator's own record of what was installed rather
    than from the orchestrator existing: it is constructed during the
    construction phase and serves reads with no registry at all, so its
    presence would tell the reconciler this had converged when it had
    not run once.

    Args:
        app_state: Application state carrying the communication slice.

    Returns:
        ``True`` once the factories are installed.
    """
    orchestrator = app_state.slice(CommunicationStateSlice).meeting_orchestrator
    return orchestrator is not None and orchestrator.has_protocol_registry


def _has_plan_dispatcher(app_state: AppState) -> bool:
    """Report whether the proposer's plan dispatcher is attached.

    Args:
        app_state: Application state carrying the meta slice.

    Returns:
        ``True`` when a proposer exists and carries a dispatcher.
    """
    proposer = app_state.slice(MetaStateSlice).chief_of_staff_proposer
    return proposer is not None and proposer.has_plan_dispatcher


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        id=CapabilityId.PERSISTENCE,
        present=lambda s: s.slice(PersistenceStateSlice).backend is not None,
    ),
    Capability(
        id=CapabilityId.SETTINGS_RESOLVER,
        present=lambda s: s.slice(SettingsStateSlice).config_resolver is not None,
    ),
    Capability(
        id=CapabilityId.PROVIDER_REGISTRY,
        present=lambda s: s.slice(ProvidersStateSlice).registry is not None,
    ),
    Capability(
        id=CapabilityId.COST_TRACKER,
        present=lambda s: s.slice(BudgetStateSlice).cost_tracker is not None,
    ),
    Capability(
        id=CapabilityId.APPROVAL_STORE,
        present=lambda s: s.slice(ApprovalStateSlice).store is not None,
    ),
    Capability(
        id=CapabilityId.MESSAGE_BUS,
        present=lambda s: s.slice(CommunicationStateSlice).message_bus is not None,
    ),
    Capability(
        id=CapabilityId.AGENT_REGISTRY,
        present=lambda s: s.slice(HrStateSlice).agent_registry is not None,
    ),
    Capability(
        id=CapabilityId.MEETING_ORCHESTRATOR,
        present=lambda s: (
            s.slice(CommunicationStateSlice).meeting_orchestrator is not None
        ),
    ),
    Capability(
        id=CapabilityId.MEETING_PROTOCOL_REGISTRY,
        present=_meeting_protocol_registry_installed,
    ),
    Capability(
        id=CapabilityId.MEMORY_BACKEND,
        present=lambda s: s.slice(MemoryStateSlice).backend is not None,
    ),
    Capability(
        id=CapabilityId.SETTINGS_SERVICE,
        present=lambda s: s.slice(SettingsStateSlice).settings_service is not None,
    ),
    Capability(
        id=CapabilityId.WORK_PIPELINE,
        present=lambda s: s.slice(EngineStateSlice).work_pipeline is not None,
    ),
    Capability(
        id=CapabilityId.TASK_ENGINE,
        present=lambda s: s.slice(EngineStateSlice).task_engine is not None,
    ),
    Capability(
        id=CapabilityId.COORDINATOR,
        present=lambda s: s.slice(RuntimeStateSlice).coordinator is not None,
    ),
    Capability(
        id=CapabilityId.WORKSPACE_SERVICE,
        present=lambda s: (
            s.slice(WorkspaceStateSlice).project_workspace_service is not None
        ),
    ),
    Capability(
        id=CapabilityId.ORG_MEMORY_BACKEND,
        present=lambda s: s.slice(MemoryStateSlice).org_memory_backend is not None,
    ),
    Capability(
        id=CapabilityId.EVOLUTION_OUTCOMES,
        present=lambda s: s.slice(MetaStateSlice).evolution_outcome_store is not None,
    ),
    Capability(
        id=CapabilityId.DOCS_ENGINE,
        present=lambda s: s.slice(DocsStateSlice).service is not None,
    ),
    Capability(
        id=CapabilityId.RESEARCH_ENGINE,
        present=lambda s: s.slice(ResearchStateSlice).service is not None,
    ),
    Capability(
        id=CapabilityId.KNOWLEDGE_ENGINE,
        present=lambda s: s.slice(KnowledgeStateSlice).service is not None,
    ),
    Capability(
        id=CapabilityId.PROJECT_BRAIN,
        present=lambda s: s.slice(ProjectBrainStateSlice).service is not None,
    ),
    Capability(
        id=CapabilityId.CHARTER_ENGINE,
        present=lambda s: s.slice(CharterStateSlice).interview_service is not None,
    ),
    Capability(
        id=CapabilityId.TOOLSMITH,
        present=lambda s: s.slice(ToolsmithStateSlice).service is not None,
    ),
    Capability(
        id=CapabilityId.MODEL_REFRESH,
        present=lambda s: s.slice(ModelRefreshStateSlice).service is not None,
    ),
    Capability(
        id=CapabilityId.OPERATOR_CONSOLE,
        present=lambda s: s.slice(MetaStateSlice).operator_console is not None,
    ),
    Capability(
        id=CapabilityId.CONVERSATIONAL_ACTOR,
        present=lambda s: s.slice(MetaStateSlice).conversational_actor is not None,
    ),
    Capability(
        id=CapabilityId.CHIEF_OF_STAFF_CHAT,
        present=lambda s: s.slice(MetaStateSlice).chief_of_staff_chat is not None,
    ),
    Capability(
        id=CapabilityId.CHIEF_OF_STAFF_PROPOSER,
        present=lambda s: s.slice(MetaStateSlice).chief_of_staff_proposer is not None,
    ),
    Capability(
        id=CapabilityId.TURN_INTENT_CLASSIFIER,
        present=lambda s: s.slice(MetaStateSlice).turn_intent_classifier is not None,
    ),
    Capability(
        id=CapabilityId.MULTI_VOICE_ROUTER,
        present=lambda s: s.slice(MetaStateSlice).multi_voice_router is not None,
    ),
    Capability(
        id=CapabilityId.GROUP_CHAT,
        present=lambda s: s.slice(MetaStateSlice).group_chat_service is not None,
    ),
    Capability(
        id=CapabilityId.SIGNALS_SERVICE,
        present=lambda s: s.slice(MetaStateSlice).signals_service is not None,
    ),
    Capability(
        id=CapabilityId.CUSTOM_RULES,
        present=lambda s: s.slice(MetaStateSlice).custom_rules_service is not None,
    ),
    Capability(
        id=CapabilityId.SELF_IMPROVEMENT,
        present=lambda s: s.slice(MetaStateSlice).self_improvement_service is not None,
    ),
    Capability(
        id=CapabilityId.ANALYTICS_SERVICE,
        present=lambda s: s.slice(MetaStateSlice).analytics_service is not None,
    ),
    Capability(
        id=CapabilityId.REPORTS_SERVICE,
        present=lambda s: s.slice(MetaStateSlice).reports_service is not None,
    ),
    Capability(
        id=CapabilityId.EXPERIMENT_SERVICE,
        present=lambda s: s.slice(MetaStateSlice).experiment_service is not None,
    ),
    Capability(
        id=CapabilityId.AB_TEST_REPO,
        present=lambda s: s.slice(MetaStateSlice).ab_test_repo is not None,
    ),
    Capability(
        id=CapabilityId.ALERT_REPO,
        present=lambda s: s.slice(MetaStateSlice).alert_repo is not None,
    ),
    Capability(
        id=CapabilityId.ORG_INFLECTION_MONITOR,
        present=lambda s: s.slice(MetaStateSlice).org_inflection_monitor is not None,
    ),
    Capability(
        id=CapabilityId.SPRINT_SERVICE,
        present=lambda s: s.slice(EngineStateSlice).sprint_service is not None,
    ),
    Capability(
        id=CapabilityId.RISK_OVERRIDE_SERVICE,
        present=lambda s: s.slice(SecurityStateSlice).risk_override_service is not None,
    ),
    Capability(
        id=CapabilityId.DELIVERABLE_RECEIPTS,
        present=lambda s: s.slice(DeliverableReceiptStateSlice).service is not None,
    ),
    Capability(
        id=CapabilityId.TOOL_CALL_FEEDBACK,
        present=lambda s: s.slice(ToolCallFeedbackStateSlice).tracker is not None,
    ),
    Capability(
        id=CapabilityId.ROLE_VERSION_SERVICE,
        present=lambda s: (
            s.slice(OrganizationStateSlice).role_version_service is not None
        ),
    ),
    Capability(
        id=CapabilityId.BUDGET_VERSIONS_SERVICE,
        present=lambda s: s.slice(BudgetStateSlice).budget_versions_service is not None,
    ),
    Capability(
        id=CapabilityId.SETTINGS_READ_SERVICE,
        present=lambda s: s.slice(SettingsStateSlice).settings_read_service is not None,
    ),
    Capability(
        id=CapabilityId.PROJECT_ROLLUP_SERVICE,
        present=lambda s: s.slice(EngineStateSlice).project_rollup_service is not None,
    ),
    # Each read from the rollup's own attachment record rather than from the
    # rollup existing, for the same reason as the four pipeline attachments
    # below: a tail collaborator is attached onto an already-wired rollup and
    # installs nothing else observable. The rollup comes up as soon as
    # persistence and the task engine do, which is before a provider is
    # configured, so its first wire legitimately leaves the tail unattached;
    # reading the rollup's mere existence as a live tail would tell the
    # reconciler these subsystems had converged and it would never revisit
    # them. One probe per collaborator, because they resolve independently:
    # a shared probe let a tail come up while the retro capture stayed unwired.
    Capability(
        id=CapabilityId.INITIATIVE_INTEGRATE,
        present=lambda s: _tail_attached(s, ProjectRollupService.has_integration),
    ),
    Capability(
        id=CapabilityId.INITIATIVE_EVALUATE,
        present=lambda s: _tail_attached(s, ProjectRollupService.has_evaluation),
    ),
    Capability(
        id=CapabilityId.INITIATIVE_REPLAN,
        present=lambda s: _tail_attached(s, ProjectRollupService.has_replan_trigger),
    ),
    Capability(
        id=CapabilityId.INITIATIVE_RETRO_CAPTURE,
        present=lambda s: _tail_attached(s, ProjectRollupService.has_retro_capture),
    ),
    Capability(
        id=CapabilityId.KANBAN_BOARD,
        present=lambda s: s.slice(EngineStateSlice).kanban_board_service is not None,
    ),
    # The four below read the pipeline's own attachment record rather than a
    # separate marker, so what the reconciler calls live is exactly what the
    # ``attach_*`` seam installed.
    Capability(
        id=CapabilityId.RUN_NARRATOR,
        present=lambda s: (_attachments(s) or _NOTHING_ATTACHED).narrator,
    ),
    Capability(
        id=CapabilityId.REFINEMENT_ROUTER,
        present=lambda s: (_attachments(s) or _NOTHING_ATTACHED).refinement_router,
    ),
    Capability(
        id=CapabilityId.PLAN_REVIEW_GATE,
        present=lambda s: (_attachments(s) or _NOTHING_ATTACHED).plan_review_gate,
    ),
    Capability(
        id=CapabilityId.PLAN_REVIEW_PANEL,
        present=lambda s: (_attachments(s) or _NOTHING_ATTACHED).plan_review_panel,
    ),
    Capability(
        id=CapabilityId.CONVERSATIONAL_PLAN_DISPATCHER,
        present=_has_plan_dispatcher,
    ),
    Capability(
        id=CapabilityId.STEERING_SERVICE,
        present=lambda s: s.slice(CockpitStateSlice).steering_service is not None,
    ),
    Capability(
        id=CapabilityId.FINE_TUNE_ORCHESTRATOR,
        present=lambda s: s.slice(MemoryStateSlice).fine_tune_orchestrator is not None,
    ),
    Capability(
        id=CapabilityId.TEAM_SERVICE,
        present=lambda s: s.slice(OrganizationStateSlice).team_service is not None,
    ),
    Capability(
        id=CapabilityId.COMPANY_READ_SERVICE,
        present=lambda s: (
            s.slice(OrganizationStateSlice).company_read_service is not None
        ),
    ),
    Capability(
        id=CapabilityId.PLAN_ITEM_REPLY_SERVICE,
        present=lambda s: s.slice(EngineStateSlice).plan_item_reply_service is not None,
    ),
    Capability(
        id=CapabilityId.ANALYTICS_COLLECTOR,
        present=lambda _s: _analytics_collector_configured(),
    ),
    Capability(
        id=CapabilityId.EVAL_LOOP,
        present=lambda s: s.slice(HrStateSlice).eval_loop_coordinator is not None,
    ),
    Capability(
        id=CapabilityId.PRUNING_SERVICE,
        present=lambda s: s.slice(HrStateSlice).pruning_service is not None,
    ),
    Capability(
        id=CapabilityId.SCALING_SERVICE,
        present=lambda s: s.slice(HrStateSlice).scaling_service is not None,
    ),
    Capability(
        id=CapabilityId.QUOTA_POLLER,
        present=lambda s: s.slice(BudgetStateSlice).quota_poller is not None,
    ),
    Capability(
        id=CapabilityId.STRATEGY_CONTEXT,
        present=lambda _s: _strategy_context_bound(),
    ),
)
