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
from synthorg.hr.state import HrStateSlice
from synthorg.memory.state import MemoryStateSlice
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.providers.state import ProvidersStateSlice
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
        id=CapabilityId.ORG_MEMORY_BACKEND,
        present=lambda s: s.slice(MemoryStateSlice).org_memory_backend is not None,
    ),
)
