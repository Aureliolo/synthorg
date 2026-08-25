"""Boot-scoped collaborators for the governed publish tools.

``PublishToolsRuntime`` is built once at runtime-service construction.
``PublishToolDeps`` pairs it with the run's identity, task, and effective
autonomy at per-run registry augmentation time.

Like the deploy family this runtime carries an *allowlist* of target names
rather than one bound connection: a synthetic org publishes to several
registries, and which one a call uses is chosen per call. The workspace root
is the host-side directory the coding harness builds into; ``workspace_push``
reads the built image layout from under it, path-guarded. The destructive
path's audit identity is the run's own ``AgentIdentity``, passed to the push
tool as a constructor argument at augmentation time, so it is deliberately not
part of this boot-scoped bundle.
"""

from dataclasses import dataclass
from pathlib import Path

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.clock import Clock
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.security.timeout.protocol import RiskTierClassifier


@dataclass(frozen=True)
class PublishToolsRuntime:
    """Boot-scoped dependencies the publish tools need per run."""

    connection_catalog: ConnectionCatalog
    # Registry-connection names the operator has approved as targets. Empty
    # allows nothing, and an empty allowlist leaves the family unregistered
    # rather than registered against every connection in the catalog.
    allowed_targets: frozenset[str]
    timeout_seconds: float
    # Caps on what one push may move: the manifest read/published and the
    # total image bytes a workspace push uploads. Both bound how much an agent
    # can push through the governed path in one call.
    max_manifest_bytes: int
    max_image_bytes: int
    # Host-side root the coding harness builds into (the sandbox mounts it
    # read-write). ``workspace_push`` reads the built OCI layout from under it.
    workspace_root: Path

    @property
    def connection_name(self) -> NotBlankStr | None:
        """Always ``None``: this family binds per call, not per runtime.

        Each call resolves its connection from the target it names, so there is
        no single bound connection to report here;
        :attr:`PublishToolsRuntime.allowed_targets` is the real bound surface.
        """
        return None


@dataclass(frozen=True)
class PublishToolDeps:
    """Per-run collaborators shared by every publish tool instance."""

    runtime: PublishToolsRuntime
    approval_store: ApprovalStoreProtocol
    agent_id: str
    task_id: str | None = None
    effective_autonomy: EffectiveAutonomy | None = None
    risk_classifier: RiskTierClassifier | None = None
    clock: Clock | None = None
