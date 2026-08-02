# module-kind: declarative
"""Live availability checks for every declared capability.

Each check reads current state and answers one question: is this here right
now? They run on every reconcile pass, so they stay cheap, synchronous and
total. A check that raised would decide the fate of every subsystem behind
it, so none of them may.
"""

from synthorg.api.state import AppState
from synthorg.api.subsystems.spec import Capability, CapabilityId
from synthorg.approval.state import ApprovalStateSlice
from synthorg.budget.state import BudgetStateSlice
from synthorg.communication.state import CommunicationStateSlice
from synthorg.deliverable_receipts.state_slice import DeliverableReceiptStateSlice
from synthorg.docs_engine.state import DocsStateSlice
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


def _has_default_provider(app_state: AppState) -> bool:
    """Report whether an explicit provider binding is resolvable.

    Distinct from the registry merely existing: a registry holding several
    providers with no default chosen resolves nothing, and the features that
    dispatch without a per-feature model stay correctly unwired rather than
    picking one alphabetically.

    Args:
        app_state: Application state carrying the provider slice.

    Returns:
        ``True`` when a default provider resolves.
    """
    registry = app_state.slice(ProvidersStateSlice).registry
    return registry is not None and registry.default_provider() is not None


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
    Capability(id=CapabilityId.DEFAULT_PROVIDER, present=_has_default_provider),
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
)
