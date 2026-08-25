"""Boot-scoped collaborators for the governed deploy tools.

``DeployToolsRuntime`` is built once at runtime-service construction.
``DeployToolDeps`` pairs it with the run's identity, task, and effective
autonomy at per-run registry augmentation time.

Unlike the forge and chat families this runtime carries an *allowlist* of
target names rather than one bound connection: a synthetic org deploys to
several targets, and which one a call uses is chosen per call. The
allowlist is the operator's list, so an agent can pick from it but never
extend it. The destructive path's audit identity is the run's own
``AgentIdentity``, passed to the release tool as a constructor argument at
augmentation time, so it is deliberately not part of this boot-scoped bundle.
"""

from dataclasses import dataclass

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.clock import Clock
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.security.timeout.protocol import RiskTierClassifier


@dataclass(frozen=True)
class DeployToolsRuntime:
    """Boot-scoped dependencies the deploy tools need per run."""

    connection_catalog: ConnectionCatalog
    # Deploy-connection names the operator has approved as targets. Empty
    # allows nothing, and an empty allowlist leaves the family unregistered
    # rather than registered against every connection in the catalog.
    allowed_targets: frozenset[str]
    timeout_seconds: float
    max_log_chars: int

    @property
    def connection_name(self) -> NotBlankStr | None:
        """Satisfy the shared runtime protocol.

        Returns:
            ``None``. This family resolves its connection from the call's
            target, so there is no single bound connection;
            :attr:`DeployToolsRuntime.allowed_targets` is the real bound
            surface.
        """
        return None


@dataclass(frozen=True)
class DeployToolDeps:
    """Per-run collaborators shared by every deploy tool instance."""

    runtime: DeployToolsRuntime
    approval_store: ApprovalStoreProtocol
    agent_id: str
    task_id: str | None = None
    effective_autonomy: EffectiveAutonomy | None = None
    risk_classifier: RiskTierClassifier | None = None
    clock: Clock | None = None
