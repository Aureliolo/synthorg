"""Boot-scoped collaborators for the forge agent tools.

``ForgeToolsRuntime`` is built once at runtime-service construction and
carried on the ``AgentEngine``. ``ForgeToolDeps`` pairs it with the run's
identity, task, and effective autonomy at per-run registry augmentation
time. Both are ``None`` when the feature is disabled or no connection
catalog / bound connection is wired, in which case the tools are not
registered.
"""

from dataclasses import dataclass

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.clock import Clock
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.security.timeout.protocol import RiskTierClassifier


@dataclass(frozen=True)
class ForgeToolsRuntime:
    """Boot-scoped dependencies the forge tools need per run."""

    connection_catalog: ConnectionCatalog
    connection_name: str
    timeout_seconds: float
    max_read_chars: int


@dataclass(frozen=True)
class ForgeToolDeps:
    """Per-run collaborators shared by every forge tool instance."""

    runtime: ForgeToolsRuntime
    approval_store: ApprovalStoreProtocol
    agent_id: str
    task_id: str | None = None
    effective_autonomy: EffectiveAutonomy | None = None
    risk_classifier: RiskTierClassifier | None = None
    clock: Clock | None = None
